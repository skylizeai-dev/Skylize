"""HITL queue writer — persists escalations to ``hitl_queue`` and publishes to Redis.

Write order per write_escalation():
  1. Check duplicate (skip if pending record for same event_id + tenant already exists).
  2. INSERT into ``hitl_queue`` (source of truth). Failure → raise, do not emit event.
  3. XADD to Redis stream ``evt:{tenant_id}:governance``. Failure → log warning only;
     the DB row is the source of truth and must NOT be rolled back.

Only call when outcome is DEFERRED_TO_HUMAN or ESCALATED.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.models import (
    DecisionContext,
    DecisionOutcome,
    DecisionResult,
)

if TYPE_CHECKING:
    from skylize.dal.connection import Database

log = logging.getLogger(__name__)

_HITL_EXPIRY_HOURS = 48
_ELIGIBLE_OUTCOMES = {DecisionOutcome.DEFERRED_TO_HUMAN, DecisionOutcome.ESCALATED}


def _build_proposal_json(context: DecisionContext, result: DecisionResult) -> dict:
    return {
        "event_id": context.event_id,
        "event_type": context.event_type,
        "department": context.department,
        "received_at": context.received_at.isoformat(),
        "original_payload": context.payload,
        "evaluation_steps": [
            {
                "stage": s.stage.value,
                "passed": s.passed,
                "outcome": s.outcome.value if s.outcome else None,
                "detail": s.detail,
                "duration_ms": s.duration_ms,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in result.steps
        ],
        "final_reason": result.final_reason,
        "outcome": result.outcome.value,
    }


def _build_score_json(result: DecisionResult) -> dict | None:
    if result.scoring is None:
        return None
    return {
        "risk_score": result.scoring.risk_score,
        "opportunity_score": result.scoring.opportunity_score,
        "risk_band": result.scoring.risk_band.value,
        "confidence": result.scoring.confidence,
        "factors": result.scoring.factors,
    }


def _derive_escalation_reason(result: DecisionResult) -> str:
    for step in reversed(result.steps):
        if step.detail.get("escalation_reason"):
            return str(step.detail["escalation_reason"])
        if step.detail.get("trigger_reason"):
            return str(step.detail["trigger_reason"])
        if step.detail.get("reason"):
            return str(step.detail["reason"])
    return result.final_reason


class HITLQueueWriter:
    """Writes escalation records to hitl_queue and emits governance stream events."""

    def __init__(
        self,
        db: "Database",
        redis: aioredis.Redis,
        settings: DecisionEngineSettings,
    ) -> None:
        self._db = db
        self._redis = redis
        self._settings = settings

    async def write_escalation(
        self,
        context: DecisionContext,
        result: DecisionResult,
        hitl_id: uuid.UUID | str,
    ) -> str:
        """Persist HITL record and emit governance event. Returns hitl_id.

        ``hitl_id`` must be minted once by the caller (the orchestrator, via
        ``pipeline.hitl_id_for``) and be the same id passed to
        ``DecisionEventPublisher.publish_outcome`` for this result, so the
        ``decision.deferred_to_human`` event and the ``hitl_queue`` row agree.

        Only call when result.outcome is DEFERRED_TO_HUMAN or ESCALATED.
        Postgres INSERT is the commit point — Redis failure does not trigger rollback.
        """
        if result.outcome not in _ELIGIBLE_OUTCOMES:
            raise ValueError(
                f"write_escalation called with non-escalation outcome: {result.outcome!r}"
            )

        hitl_id = str(hitl_id)
        now = datetime.now(timezone.utc)
        expiry_hours = getattr(self._settings, "hitl_expiry_hours", _HITL_EXPIRY_HOURS)
        expires_at = now + timedelta(hours=expiry_hours)

        escalation_reason = _derive_escalation_reason(result)
        proposal_json = _build_proposal_json(context, result)
        score_json = _build_score_json(result)

        # correlation_id: prefer event_id parsed as UUID, else generate
        try:
            correlation_id = uuid.UUID(context.event_id)
        except (ValueError, AttributeError):
            correlation_id = uuid.uuid4()

        async with self._db.tenant_session(context.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO hitl_queue (
                    hitl_id, org_id, decision_id, correlation_id, partition_key,
                    trigger_reason, proposal_json, score_json,
                    status, expires_at, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                uuid.UUID(hitl_id),
                context.tenant_id,
                uuid.UUID(result.decision_id),
                correlation_id,
                context.event_id,          # partition_key = event_id for dedup
                escalation_reason,
                json.dumps(proposal_json, default=str),
                json.dumps(score_json, default=str) if score_json else None,
                "pending",
                expires_at,
                now,
            )

        log.info(
            "hitl_queue_record_inserted",
            extra={
                "hitl_id": hitl_id,
                "decision_id": result.decision_id,
                "tenant_id": context.tenant_id,
                "event_id": context.event_id,
                "escalation_reason": escalation_reason,
            },
        )

        await self._emit_governance_event(
            hitl_id=hitl_id,
            context=context,
            result=result,
            escalation_reason=escalation_reason,
            risk_score=result.scoring.risk_score if result.scoring else None,
            created_at=now,
        )

        return hitl_id

    async def check_duplicate_escalation(
        self, event_id: str, tenant_id: str
    ) -> bool:
        """Return True if a pending hitl_queue row already exists for this event + tenant."""
        async with self._db.tenant_session(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT hitl_id FROM hitl_queue
                WHERE partition_key = $1
                  AND org_id = $2
                  AND status = 'pending'
                LIMIT 1
                """,
                event_id,
                tenant_id,
            )
        return row is not None

    async def _emit_governance_event(
        self,
        hitl_id: str,
        context: DecisionContext,
        result: DecisionResult,
        escalation_reason: str,
        risk_score: float | None,
        created_at: datetime,
    ) -> None:
        stream_key = f"evt:{context.tenant_id}:governance"
        fields: dict[str, str] = {
            "event_type": "governance.human_escalation_raised",
            "decision_id": result.decision_id,
            "hitl_queue_id": hitl_id,
            "department": context.department,
            "escalation_reason": escalation_reason,
            "risk_score": str(risk_score) if risk_score is not None else "",
            "created_at": created_at.isoformat(),
        }
        try:
            await self._redis.xadd(stream_key, fields)
        except Exception as exc:
            log.warning(
                "hitl_governance_event_publish_failed",
                extra={
                    "hitl_id": hitl_id,
                    "decision_id": result.decision_id,
                    "tenant_id": context.tenant_id,
                    "stream_key": stream_key,
                    "error": str(exc),
                },
                exc_info=True,
            )
