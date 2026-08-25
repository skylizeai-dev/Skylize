"""Qdrant vector store adapter for platform knowledge.

TENANT ISOLATION IS STRUCTURAL HERE, NOT CONVENTIONAL. Every tenant shares one
collection (``platform_knowledge``), so unlike Postgres there is no store-level
equivalent of RLS: the only thing standing between tenant A and tenant B is the
``org_id`` filter. Previously that filter was supplied by each call site inside a
free-form ``filters`` dict, and ``search`` degraded to an UNFILTERED
whole-collection read when the dict was empty — one forgetful call site was a
cross-tenant read. It is now impossible to express that call:

  * ``org_id`` is a REQUIRED KEYWORD-ONLY argument on every public method that
    touches the collection. Omitting it is a mypy error and a ``TypeError`` at
    run time; there is no default and no implicit shared scope.
  * The adapter, not the caller, builds the ``org_id`` condition. Passing
    ``org_id`` inside ``filters`` is rejected (``OrgScopeRequired``) so a caller
    cannot shadow, widen, or blank the scope it was handed.
  * Reads that address a point by id (``verify_point`` / ``point_doc_hash`` /
    ``verify_document``) re-check the stored payload's ``org_id`` and fail closed
    (absent/None/False) on a mismatch, so a caller holding another tenant's point
    id still learns nothing.
  * Writes stamp ``org_id`` into the payload LAST, after caller metadata, so a
    point can never be stored unlabelled or mislabelled.

Per-org collections (E19) remain the larger, correct end state; this closes the
bypass on the shared collection without a re-index.

Two write paths share this adapter:

* ``upsert_vector`` / ``verify_document`` — the legacy md5-keyed path used by the
  MemoryService index. The point id derivation is kept byte-compatible so that
  subsystem is untouched; note the legacy id is NOT injective over ``org_id``, so
  two tenants using the same ``doc_id`` collide onto one point. Reads on this
  path are org-checked (a collision cannot leak), but a write collision is still
  possible in principle. Unreachable today: ``MemoryService`` is constructed
  nowhere in ``src/``.
* ``upsert_points`` / ``verify_point`` / ``point_doc_hash`` / ``delete_by_filter``
  — the tenant-injective knowledge path. Callers compute an injective point id
  via ``memory.identity`` and pass it explicitly; the adapter never derives it.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    UpdateStatus,
    VectorParams,
)

from .org_scope import ORG_FIELD, OrgScopeRequired, require_org, scoped_filters

__all__ = ["ORG_FIELD", "OrgScopeRequired", "QdrantAdapter", "QdrantPoint"]

log = structlog.get_logger(__name__)

_COLLECTION = "platform_knowledge"
_DIMENSION = 1536
_UPSERT_BATCH = 256  # points per Qdrant upsert request


def _scoped_filter(org_id: str, extra: dict[str, Any] | None) -> Filter:
    """The shared org-scope rule (org_scope.scoped_filters) as a Qdrant Filter."""
    return Filter(
        must=[
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in scoped_filters(org_id, extra).items()
        ]
    )


class QdrantPoint(BaseModel):
    """A vector point with a caller-computed, tenant-injective point id.

    ``org_id`` is a required field, not a payload convention: the adapter stamps
    it into the stored payload itself, so no point can be written unlabelled.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    org_id: str
    point_id: str
    vector: list[float]
    payload: dict[str, Any]


class QdrantAdapter:
    def __init__(self, qdrant_url: str, qdrant_api_key: str = "") -> None:
        self._client = AsyncQdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key or None,
        )
        # Collections are immortal once created; memoize so the hot ingest loop
        # doesn't pay a get_collections round-trip on every point.
        self._collection_ready = False

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        existing = {c.name for c in (await self._client.get_collections()).collections}
        if _COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=_COLLECTION,
                vectors_config=VectorParams(size=_DIMENSION, distance=Distance.COSINE),
            )
        self._collection_ready = True

    async def upsert_document(
        self, doc_id: str, content: str, metadata: dict[str, object], *, org_id: str
    ) -> None:
        raise TypeError(
            "upsert_document requires a pre-computed vector; call upsert_vector instead"
        )

    async def upsert_vector(
        self,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, object],
        *,
        org_id: str,
    ) -> None:
        require_org(org_id)
        await self._ensure_collection()
        result = await self._client.upsert(
            collection_name=_COLLECTION,
            points=[
                PointStruct(
                    id=_stable_id(doc_id),
                    vector=vector,
                    # org stamped LAST: caller metadata cannot mislabel the point.
                    payload={"doc_id": doc_id, **metadata, ORG_FIELD: org_id},
                )
            ],
        )
        if result.status != UpdateStatus.COMPLETED:
            raise RuntimeError(f"Qdrant upsert failed: {result.status}")
        log.debug("qdrant.upserted", doc_id=doc_id, org_id=org_id)

    # ── tenant-injective knowledge path (explicit point ids) ────────────────
    async def upsert_points(self, points: list[QdrantPoint]) -> None:
        """Batch upsert of vectors under caller-supplied injective point ids.

        Each point carries its own ``org_id`` (a required model field), so this
        method needs no separate scope argument and no point can be unlabelled.
        """
        if not points:
            return
        for p in points:
            require_org(p.org_id)
        await self._ensure_collection()
        for start in range(0, len(points), _UPSERT_BATCH):
            batch = points[start : start + _UPSERT_BATCH]
            result = await self._client.upsert(
                collection_name=_COLLECTION,
                points=[
                    PointStruct(
                        id=p.point_id,
                        vector=p.vector,
                        payload={**p.payload, ORG_FIELD: p.org_id},
                    )
                    for p in batch
                ],
            )
            if result.status != UpdateStatus.COMPLETED:
                raise RuntimeError(f"Qdrant batch upsert failed: {result.status}")

    async def verify_point(self, point_id: str, content_hash: str, *, org_id: str) -> bool:
        """True if ``point_id`` exists UNDER ``org_id`` with a matching hash.

        A point belonging to another tenant reads as absent (False), so a caller
        holding a foreign point id learns nothing about it.
        """
        payload = await self._scoped_payload(point_id, org_id=org_id)
        if payload is None:
            return False
        return payload.get("content_hash") == content_hash

    async def point_doc_hash(self, point_id: str, *, org_id: str) -> str | None:
        """The whole-document hash stored on ``point_id`` under ``org_id``.

        None when the point is absent OR belongs to another tenant.
        """
        payload = await self._scoped_payload(point_id, org_id=org_id)
        if payload is None:
            return None
        doc_hash = payload.get("doc_content_hash")
        return str(doc_hash) if doc_hash is not None else None

    async def delete_by_filter(
        self, filters: dict[str, Any] | None = None, *, org_id: str
    ) -> None:
        """Delete every point of ``org_id`` also matching ``filters`` (exact match).

        The org condition is added by the adapter and cannot be omitted, so a
        delete can never reach beyond the calling tenant — including when
        ``filters`` is empty.
        """
        condition = _scoped_filter(org_id, filters)
        await self._ensure_collection()
        result = await self._client.delete(
            collection_name=_COLLECTION,
            points_selector=FilterSelector(filter=condition),
        )
        if result.status != UpdateStatus.COMPLETED:
            raise RuntimeError(f"Qdrant delete failed: {result.status}")

    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, object] | None = None,
        *,
        org_id: str,
    ) -> list[dict[str, object]]:
        """Org-scoped vector search. There is no unfiltered form of this call.

        ``filters`` only ever NARROWS the org scope; an empty or omitted dict
        still searches exactly one tenant's points.
        """
        qdrant_filter = _scoped_filter(org_id, filters)
        await self._ensure_collection()
        hits = await self._client.search(  # type: ignore[attr-defined]  # qdrant-client API drift; recall path unwired (dead code, no tracked removal plan)
            collection_name=_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [{"score": h.score, **(h.payload or {})} for h in hits]

    async def verify_document(self, doc_id: str, content_hash: str, *, org_id: str) -> bool:
        """True if doc_id exists UNDER ``org_id`` with matching content_hash.

        Legacy md5 id path: the id is not org-injective, so the org check is on
        the stored payload rather than the id.
        """
        payload = await self._scoped_payload(_stable_id(doc_id), org_id=org_id)
        if payload is None:
            return False
        return payload.get("content_hash") == content_hash

    # ── internals ───────────────────────────────────────────────────────────
    async def _scoped_payload(self, point_id: str, *, org_id: str) -> dict[str, Any] | None:
        """Payload of ``point_id`` if it belongs to ``org_id``, else None.

        The single choke point for every read-by-id: no caller can retrieve a
        payload without passing the org check, because no caller reaches
        ``_client.retrieve`` directly.
        """
        require_org(org_id)
        await self._ensure_collection()
        results = await self._client.retrieve(
            collection_name=_COLLECTION, ids=[point_id], with_payload=True
        )
        if not results:
            return None
        payload = results[0].payload or {}
        if payload.get(ORG_FIELD) != org_id:
            log.warning(
                "qdrant.cross_tenant_point_read_blocked",
                point_id=point_id,
                requesting_org=org_id,
            )
            return None
        return dict(payload)


def _stable_id(doc_id: str) -> str:
    """Legacy md5→UUID id derivation, retained for the MemoryService index path
    (out of scope for the tenant-identity remediation). Knowledge writes use the
    injective ``memory.identity.point_id`` instead of this function."""
    return str(uuid.UUID(bytes=hashlib.md5(doc_id.encode()).digest()))
