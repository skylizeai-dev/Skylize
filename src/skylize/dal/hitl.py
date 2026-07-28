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

from .connection import Database
from .ports import HitlEscalation


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
            # verdict_* stay NULL until a human acts.
            await conn.execute(
                """
                INSERT INTO hitl_queue (
                    hitl_id, org_id, decision_id, correlation_id, partition_key,
                    trigger_reason, proposal_json, score_json,
                    status, expires_at, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
            )
