"""asyncpg implementation of ``HitlQueueRepository`` — the request-path HITL writer.

DUPLICATION (owner decision K3, deliberate and recorded here):
``src/skylize/decision_engine/hitl_writer.py`` (``HITLQueueWriter``) is the OTHER
writer of ``hitl_queue``. It runs inside the async OPA worker process and builds
its rows from that package's ``DecisionContext`` / ``DecisionResult`` models,
also emitting a Redis governance-stream event. THIS class is the SYNCHRONOUS
request-path sibling: it writes the same ``hitl_queue`` columns with the same
semantics as that writer's INSERT (``hitl_writer.py:136-141``) directly from the
inline engine's decision — no translation between the two engines' types, and no
import from ``skylize.decision_engine`` on the request path.

It additionally writes the parent ``decisions`` row in the same transaction,
because ``hitl_queue.decision_id`` is an FK to ``decisions`` and the request path
has no separate decisions projection (the async publisher, ``publisher.py:276``,
writes that row on the OPA path). Both rows land under ``tenant_session`` so RLS
applies.

Unifying the two writers behind one decision-persistence seam is DEFERRED — it
waits until the inline engine and the OPA engine share one persistence path.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from .connection import Database
from .ports import HitlEscalation, HitlQueueItem

_ITEM_COLUMNS = (
    "hitl_id, org_id, decision_id, correlation_id, partition_key, "
    "trigger_reason, proposal_json, request_json, status, "
    "verdict_by, verdict_json, verdict_at, expires_at, created_at"
)


def _item(rec: Any) -> HitlQueueItem:
    # JSONB decodes to dict via the pool codec (connection._init_connection).
    return HitlQueueItem(
        hitl_id=rec["hitl_id"],
        org_id=rec["org_id"],
        decision_id=rec["decision_id"],
        correlation_id=rec["correlation_id"],
        partition_key=rec["partition_key"],
        trigger_reason=rec["trigger_reason"],
        proposal_json=rec["proposal_json"] or {},
        request_json=rec["request_json"],
        status=rec["status"],
        verdict_by=rec["verdict_by"],
        verdict_json=rec["verdict_json"],
        verdict_at=rec["verdict_at"],
        expires_at=rec["expires_at"],
        created_at=rec["created_at"],
    )


class PgHitlQueueRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(self, escalation: HitlEscalation) -> None:
        """Persist the parent decision + the hitl_queue escalation atomically.

        ``tenant_session`` opens a transaction and sets ``skylize.org_id`` (RLS),
        so both INSERTs commit together or not at all — a hitl_queue row can never
        reference a decision that was never written."""
        e = escalation
        proposal_json = json.dumps(e.proposal_json, default=str)
        score_json = json.dumps(e.score_json, default=str) if e.score_json is not None else None
        async with self._db.tenant_session(e.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, org_id, correlation_id, causation_event_id,
                    partition_key, proposing_agent, authority_level, action_kind,
                    proposal_json, outcome, outcome_reason, policy_version,
                    score_json, governance_token_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                e.decision_id,
                e.org_id,
                e.correlation_id,
                e.causation_event_id,
                e.partition_key,
                e.proposing_agent,
                e.authority_level,
                e.action_kind,
                proposal_json,
                e.outcome,
                e.outcome_reason,
                e.policy_version,
                score_json,
                e.governance_token_id,
            )
            # Same columns + semantics as HITLQueueWriter's INSERT
            # (decision_engine/hitl_writer.py:136-141): status starts 'pending',
            # verdict_* stay NULL until a human acts. request_json (0015, owner
            # decision K4) is the request-path extra that writer never sets.
            await conn.execute(
                """
                INSERT INTO hitl_queue (
                    hitl_id, org_id, decision_id, correlation_id, partition_key,
                    trigger_reason, proposal_json, score_json,
                    status, expires_at, created_at, request_json
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                e.hitl_id,
                e.org_id,
                e.decision_id,
                e.correlation_id,
                e.partition_key,
                e.trigger_reason,
                proposal_json,
                score_json,
                "pending",
                e.expires_at,
                e.created_at,
                json.dumps(e.request_json, default=str) if e.request_json is not None else None,
            )

    # -- review/approval reads + the exactly-once verdict claim --------------

    async def list_pending(
        self, org_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[HitlQueueItem], int]:
        # WHERE org_id=... AND status='pending' ORDER BY created_at DESC is
        # exactly the shape of the partial index idx_hitl_org_pending
        # (0001_initial_schema.py:223-226).
        async with self._db.tenant_session(org_id) as conn:
            total: int = await conn.fetchval(
                "SELECT COUNT(*) FROM hitl_queue WHERE org_id=$1 AND status='pending'",
                org_id,
            )
            rows = await conn.fetch(
                f"SELECT {_ITEM_COLUMNS} FROM hitl_queue "
                "WHERE org_id=$1 AND status='pending' "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                org_id, limit, offset,
            )
            return [_item(r) for r in rows], total

    async def get(self, hitl_id: UUID, org_id: str) -> HitlQueueItem | None:
        async with self._db.tenant_session(org_id) as conn:
            rec = await conn.fetchrow(
                f"SELECT {_ITEM_COLUMNS} FROM hitl_queue WHERE hitl_id=$1 AND org_id=$2",
                hitl_id, org_id,
            )
            return None if rec is None else _item(rec)

    async def claim(
        self,
        hitl_id: UUID,
        org_id: str,
        *,
        status_to: str,
        verdict_by: str,
        verdict_json: dict[str, Any],
        verdict_at: datetime,
        require_request: bool,
    ) -> HitlQueueItem | None:
        """Conditional UPDATE ... WHERE status='pending' RETURNING — the
        exactly-once guard. Two simultaneous verdicts race on this predicate and
        Postgres serializes them: exactly one matches, the other gets None."""
        async with self._db.tenant_session(org_id) as conn:
            rec = await conn.fetchrow(
                f"""
                UPDATE hitl_queue
                   SET status=$3, verdict_by=$4, verdict_json=$5, verdict_at=$6
                 WHERE hitl_id=$1 AND org_id=$2 AND status='pending'
                   AND (expires_at IS NULL OR expires_at > $6)
                   AND ($7::boolean IS FALSE OR request_json IS NOT NULL)
                RETURNING {_ITEM_COLUMNS}
                """,
                hitl_id, org_id, status_to, verdict_by,
                json.dumps(verdict_json, default=str), verdict_at, require_request,
            )
            return None if rec is None else _item(rec)

    async def release(self, hitl_id: UUID, org_id: str, *, from_status: str) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE hitl_queue SET status='pending', "
                "verdict_by=NULL, verdict_json=NULL, verdict_at=NULL "
                "WHERE hitl_id=$1 AND org_id=$2 AND status=$3",
                hitl_id, org_id, from_status,
            )
            return str(tag).split()[-1] != "0"

    async def terminate(self, hitl_id: UUID, org_id: str, *, from_status: str) -> bool:
        """Terminal disposition for a PERMANENTLY unreplayable row.

        'expired' is an existing CHECK value (0001_initial_schema.py:212-214) and
        is what migration 0015 already assigns to rows that can never be replayed
        — no new status vocabulary. Unlike `release`, the verdict columns are NOT
        cleared: a human really did approve, and erasing that would lose the
        reason the row left 'pending' at all.
        """
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE hitl_queue SET status='expired' "
                "WHERE hitl_id=$1 AND org_id=$2 AND status=$3",
                hitl_id, org_id, from_status,
            )
            return str(tag).split()[-1] != "0"

    async def update_verdict_json(
        self, hitl_id: UUID, org_id: str, verdict_json: dict[str, Any]
    ) -> None:
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                "UPDATE hitl_queue SET verdict_json=$3 WHERE hitl_id=$1 AND org_id=$2",
                hitl_id, org_id, json.dumps(verdict_json, default=str),
            )
