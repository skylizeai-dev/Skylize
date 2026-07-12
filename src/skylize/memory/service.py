"""MemoryService — single entry point agents use (via tool proxy).

Coordinates:
  1. Qdrant vector search (primary recall path)
  2. Postgres FTS fallback (via MemoryRepository)
  3. Event emission for memory.recall_served / memory.committed / memory.invalidated

Import constraints (import-linter): may import dal/, events/, schemas/ — NOT agents/, app/, adapters/.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, Union
from uuid import UUID, uuid4

import structlog

from ..errors import MemoryWriteError
from ..events.bus import EventBus
from ..schemas.events.memory import (
    MemoryCommitted,
    MemoryEmbeddingIndexed,
    MemoryInvalidated,
    MemoryRecallServed,
)
from ..schemas.memory import MemoryEntry, MemoryScope


class _MemoryRepoPort(Protocol):
    """Minimal structural protocol covering the repo methods MemoryService uses."""

    async def write(self, entry: MemoryEntry) -> None: ...

    async def search(
        self, scope: MemoryScope, query_embedding: list[float], limit: int = 5
    ) -> list[MemoryEntry]: ...

    async def supersede(self, entry_id: UUID, superseded_by: UUID) -> None: ...

log = structlog.get_logger(__name__)

EmbeddingFn = Union[
    Callable[[str], list[float]],
    Callable[[str], Coroutine[Any, Any, list[float]]],
]


async def _embed(fn: EmbeddingFn, text: str) -> list[float]:
    result = fn(text)
    if asyncio.iscoroutine(result):
        return await result
    return result


class MemoryService:
    """High-level memory operations used by agent tool-proxy.

    All methods require org_id — enforced at the type boundary via MemoryScope.
    Stateless-agent blocking happens in MemoryGateway, not here.
    """

    def __init__(
        self,
        *,
        repo: _MemoryRepoPort,
        mem0_client: Any = None,
        embedding_fn: EmbeddingFn,
        bus: EventBus | None = None,
        qdrant_adapter: Any = None,  # QdrantAdapter (optional, avoids cross-layer import)
    ) -> None:
        self._repo: _MemoryRepoPort = repo
        self._mem0 = mem0_client
        self._embed_fn = embedding_fn
        self._bus = bus
        self._qdrant = qdrant_adapter

    # ------------------------------------------------------------------
    # recall — primary: Qdrant vector search; fallback: Postgres FTS
    # ------------------------------------------------------------------

    async def recall(
        self,
        namespace: str,
        org_id: str,
        query: str,
        k: int = 5,
    ) -> list[MemoryEntry]:
        scope = MemoryScope(org_id=org_id, department=namespace)
        results: list[MemoryEntry] = []

        # 1. Qdrant vector search (primary)
        try:
            query_vec = await _embed(self._embed_fn, query)
            if self._qdrant is not None:
                hits = await self._qdrant.search(
                    query_vector=query_vec,
                    top_k=k,
                    filters={"org_id": org_id, "namespace": namespace},
                )
                # hits from QdrantAdapter are plain dicts with score + payload
                for h in hits:
                    entry_id_str = h.get("entry_id")
                    content = h.get("text") or h.get("content_text") or ""
                    if not content:
                        continue
                    results.append(
                        MemoryEntry(
                            entry_id=UUID(entry_id_str) if entry_id_str else uuid4(),
                            org_id=org_id,
                            agent_id=h.get("agent_id", "unknown"),
                            scope=namespace,
                            department=namespace,
                            tier=h.get("tier", "episodic"),
                            content_text=content,
                            content_hash=h.get("content_hash", ""),
                            metadata={"score": h.get("score", 0.0), **{
                                k2: v for k2, v in h.items()
                                if k2 not in {"score", "text", "content_text", "entry_id", "org_id", "namespace"}
                            }},
                            importance_score=float(h.get("importance_score", 1.0)),
                            created_by_agent=h.get("created_by_agent", "unknown"),
                        )
                    )
            elif query_vec:
                # No Qdrant — fall through to Postgres vector search
                pg_results = await self._repo.search(scope, query_vec, limit=k)
                results.extend(pg_results)
        except Exception as exc:
            log.warning("memory.recall.qdrant_failed", org_id=org_id, namespace=namespace, error=str(exc))

        # 2. Postgres FTS fallback when Qdrant returned nothing
        if not results:
            try:
                query_vec_fb = await _embed(self._embed_fn, query)
                pg_results = await self._repo.search(scope, query_vec_fb, limit=k)
                results.extend(pg_results)
            except Exception as exc:
                log.warning("memory.recall.pg_fallback_failed", org_id=org_id, error=str(exc))

        # 3. Fuse + re-rank: dedupe by content_hash, sort by importance_score desc
        seen: set[str] = set()
        deduped: list[MemoryEntry] = []
        for entry in results:
            key = entry.content_hash or entry.content_text[:64]
            if key not in seen:
                seen.add(key)
                deduped.append(entry)
        deduped.sort(key=lambda e: e.importance_score, reverse=True)
        final = deduped[:k]

        # 4. Emit memory.recall_served
        if self._bus is not None:
            try:
                await self._bus.publish(
                    MemoryRecallServed(
                        tenant_id=org_id,
                        partition_key=org_id,
                        department=namespace,
                        correlation_id=uuid4(),
                        payload=MemoryRecallServed.Payload(
                            namespace=namespace,
                            query_hash=hashlib.sha256(query.encode()).hexdigest()[:16],
                            result_count=len(final),
                            confidence=final[0].importance_score if final else 0.0,
                        ),
                    )
                )
            except Exception as exc:
                log.warning("memory.recall_served.emit_failed", error=str(exc))

        return final

    # ------------------------------------------------------------------
    # commit — canonical write to Postgres + background Qdrant index
    # ------------------------------------------------------------------

    async def commit(
        self,
        namespace: str,
        org_id: str,
        text: str,
        metadata: dict[str, Any],
        *,
        agent_id: str = "system",
        supersede_entry_id: UUID | None = None,
    ) -> UUID:
        entry_id = uuid4()
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        # 1. Write to Postgres (canonical, transactional)
        try:
            entry = MemoryEntry(
                entry_id=entry_id,
                org_id=org_id,
                agent_id=agent_id,
                scope=namespace,
                department=namespace,
                tier=metadata.get("tier", "episodic"),
                content_text=text,
                content_hash=content_hash,
                metadata={k: v for k, v in metadata.items() if k != "tier"},
                importance_score=float(metadata.get("importance_score", 1.0)),
                created_by_agent=agent_id,
            )
            await self._repo.write(entry)
        except Exception as exc:
            log.error("memory.commit.pg_write_failed", org_id=org_id, error=str(exc))
            raise MemoryWriteError(str(exc)) from exc

        # 2. Handle supersede
        if supersede_entry_id is not None:
            try:
                await self._repo.supersede(supersede_entry_id, entry_id)
                if self._bus is not None:
                    await self._bus.publish(
                        MemoryInvalidated(
                            tenant_id=org_id,
                            partition_key=org_id,
                            department=namespace,
                            correlation_id=uuid4(),
                            payload=MemoryInvalidated.Payload(
                                record_id=supersede_entry_id,
                                superseded_by=entry_id,
                            ),
                        )
                    )
            except Exception as exc:
                log.warning("memory.commit.supersede_failed", error=str(exc))

        # 3. Emit memory.committed
        if self._bus is not None:
            try:
                await self._bus.publish(
                    MemoryCommitted(
                        tenant_id=org_id,
                        partition_key=org_id,
                        department=namespace,
                        correlation_id=uuid4(),
                        payload=MemoryCommitted.Payload(
                            record_id=entry_id,
                            namespace=namespace,
                            content_hash=content_hash,
                        ),
                    )
                )
            except Exception as exc:
                log.warning("memory.committed.emit_failed", error=str(exc))

        # 4. Enqueue embedding → upsert to Qdrant (background, fire-and-forget)
        if self._qdrant is not None:
            asyncio.create_task(
                self._index_to_qdrant(entry_id, org_id, namespace, text, metadata, agent_id, content_hash)
            )

        # Also write to Mem0 if client present
        if self._mem0 is not None:
            user_id = f"{org_id}:{namespace}"
            try:
                self._mem0.add(
                    text,
                    user_id=user_id,
                    metadata={"org_id": org_id, "namespace": namespace, **metadata},
                )
            except Exception as exc:
                log.warning("memory.commit.mem0_failed", org_id=org_id, error=str(exc))

        return entry_id

    async def _index_to_qdrant(
        self,
        entry_id: UUID,
        org_id: str,
        namespace: str,
        text: str,
        metadata: dict[str, Any],
        agent_id: str,
        content_hash: str,
    ) -> None:
        try:
            vec = await _embed(self._embed_fn, text)
            doc_id = f"{org_id}:{namespace}:{entry_id}"
            await self._qdrant.upsert_vector(
                doc_id=doc_id,
                vector=vec,
                metadata={
                    "org_id": org_id,
                    "namespace": namespace,
                    "entry_id": str(entry_id),
                    "agent_id": agent_id,
                    "content_hash": content_hash,
                    "text": text,
                    **metadata,
                },
            )
            if self._bus is not None:
                await self._bus.publish(
                    MemoryEmbeddingIndexed(
                        tenant_id=org_id,
                        partition_key=org_id,
                        department=namespace,
                        correlation_id=uuid4(),
                        payload=MemoryEmbeddingIndexed.Payload(
                            record_id=entry_id,
                            namespace=namespace,
                            vector_id=doc_id,
                        ),
                    )
                )
        except Exception as exc:
            log.warning(
                "memory.qdrant_index.failed",
                org_id=org_id,
                entry_id=str(entry_id),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Legacy compat — proxies to recall/commit for existing callers
    # ------------------------------------------------------------------

    async def retrieve(self, scope: MemoryScope, query: str) -> list[MemoryEntry]:
        return await self.recall(
            namespace=scope.department or "default",
            org_id=scope.org_id,
            query=query,
        )

    async def store(
        self,
        scope: MemoryScope,
        agent_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.commit(
            namespace=scope.department or "default",
            org_id=scope.org_id,
            text=content,
            metadata=metadata,
            agent_id=agent_id,
        )

    async def is_stateless(self, contract: Any) -> bool:
        return len(contract.memory_write_access) == 0
