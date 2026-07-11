"""One-time, idempotent re-key of legacy knowledge vectors → injective identity.

WHY: before the tenant-identity remediation, knowledge points were stored under
``md5(f"{org_id}:{doc_id}")`` and (in the oldest data) with no ``org_id`` payload
at all. The new scheme keys points by ``identity.point_id(org_id, doc_id)`` (an
injective UUIDv5) and every read filters on ``org_id``. After deploy the legacy
points are therefore both mis-keyed (idempotency + overwrite protection miss) and
— for pre-tenant data — unreachable. This script rewrites them under the new id
and enriches the payload, deleting the old point.

IDEMPOTENT: a point that already carries ``parent_doc_id`` is treated as migrated
and skipped, so the script is safe to re-run (e.g. after a partial failure).

SCOPE: only the KnowledgeIngestionService points are touched. MemoryService index
points (payload has ``entry_id``/``namespace``) are left untouched — they keep the
legacy md5 scheme by design and are rebuildable from Postgres.

Usage:
    python -m scripts.reindex_knowledge_identity --url "$SKYLIZE_QDRANT_URL" \
        [--api-key "$SKYLIZE_QDRANT_API_KEY"] [--purge-orphans] [--apply]

Without --apply the script runs read-only and prints what it *would* do.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import PointIdsList, PointStruct

from skylize.memory import identity

_COLLECTION = "platform_knowledge"
_SCROLL_PAGE = 256


def _is_memory_index_point(payload: dict) -> bool:
    """MemoryService index points — out of scope, never touched."""
    return "entry_id" in payload or "namespace" in payload


def _already_migrated(payload: dict) -> bool:
    return "parent_doc_id" in payload and "doc_content_hash" in payload


def _original_doc_id(org_id: str, legacy_doc_id: str) -> str:
    """Strip the ``{org_id}:`` scoping prefix the old adapter stored in payload."""
    prefix = f"{org_id}:"
    return legacy_doc_id[len(prefix):] if legacy_doc_id.startswith(prefix) else legacy_doc_id


def _plan_for_point(point) -> tuple[str, dict] | None:
    """Return (new_point_id, new_payload) for a legacy knowledge point, or None
    to skip. Raises nothing — callers decide apply vs dry-run."""
    payload = dict(point.payload or {})
    if _is_memory_index_point(payload) or _already_migrated(payload):
        return None
    org_id = payload.get("org_id")
    legacy_doc_id = payload.get("doc_id")
    if not org_id or not legacy_doc_id:
        return None  # pre-tenant / unrecognized → handled as orphan by caller
    doc_id = _original_doc_id(org_id, legacy_doc_id)
    parent_doc_id, _, chunk_suffix = doc_id.partition("#chunk")
    chunk_index = int(chunk_suffix) if chunk_suffix.isdigit() else None
    new_payload = {
        **payload,
        "doc_id": doc_id,
        "parent_doc_id": parent_doc_id,
        "chunk_index": chunk_index,
        # whole-document hash is unknown for legacy data; seed with the chunk
        # hash so at most one re-embed happens on the document's next update.
        "doc_content_hash": payload.get("content_hash", ""),
    }
    return identity.point_id(org_id, doc_id), new_payload


async def run(url: str, api_key: str, *, apply: bool, purge_orphans: bool) -> None:
    client = AsyncQdrantClient(url=url, api_key=api_key or None)
    migrated = skipped = orphaned = 0
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=_COLLECTION,
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for point in points:
            payload = dict(point.payload or {})
            if _is_memory_index_point(payload) or _already_migrated(payload):
                skipped += 1
                continue
            plan = _plan_for_point(point)
            if plan is None:
                # legacy knowledge point with no org_id → unreachable by any read.
                orphaned += 1
                if apply and purge_orphans:
                    await client.delete(
                        collection_name=_COLLECTION,
                        points_selector=PointIdsList(points=[point.id]),
                    )
                continue
            new_id, new_payload = plan
            migrated += 1
            if apply:
                await client.upsert(
                    collection_name=_COLLECTION,
                    points=[PointStruct(id=new_id, vector=point.vector, payload=new_payload)],
                )
                if str(new_id) != str(point.id):
                    await client.delete(
                        collection_name=_COLLECTION,
                        points_selector=PointIdsList(points=[point.id]),
                    )
        if offset is None:
            break
    verb = "migrated" if apply else "would migrate"
    print(
        f"{verb}={migrated} skipped(memory/already)={skipped} "
        f"orphans={'purged' if (apply and purge_orphans) else 'found'}={orphaned}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("SKYLIZE_QDRANT_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("SKYLIZE_QDRANT_API_KEY", ""))
    parser.add_argument("--apply", action="store_true", help="perform writes (default: dry run)")
    parser.add_argument("--purge-orphans", action="store_true", help="delete un-tenanted legacy points")
    args = parser.parse_args()
    if not args.url:
        raise SystemExit("--url (or SKYLIZE_QDRANT_URL) is required")
    asyncio.run(run(args.url, args.api_key, apply=args.apply, purge_orphans=args.purge_orphans))


if __name__ == "__main__":
    main()
