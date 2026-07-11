"""Decision event publisher — writes decisions to Postgres then publishes to Redis.

Write order per publish_outcome():
  1. Validate outbound payload (Pydantic v2) — raise on invalid, never publish garbage.
  2. INSERT into ``decisions`` (source of truth).
  3. XADD to Redis stream.  If Redis fails after step 2: log + dead-letter the
     event payload to ``evt:dlq:decision_engine``; do NOT rollback Postgres.

mirror_audit_step() is called per pipeline stage. Uses the pool (no new
connection per call) and serialises step fields into the audit_log columns
available in migration 0001.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import asyncpg
import redis.asyncio as aioredis
from pydantic import ValidationError

from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.exceptions import DecisionEngineError
from skylize.decision_engine.models import (
    DecisionOutcome,
    DecisionResult,
    EvaluationStepRecord,
)
from skylize.schemas.events.decision import (
    DecisionApproved,
    DecisionDeferredToHuman,
    DecisionRejected,
)

if TYPE_CHECKING:
    from skylize.dal.connection import Database

log = logging.getLogger(__name__)

# outcome → event_type string (event_driven_architecture.md §7)
_OUTCOME_TO_EVENT_TYPE: dict[DecisionOutcome, str] = {
    DecisionOutcome.APPROVED: "decision.approved",
    DecisionOutcome.REJECTED: "decision.rejected",
    DecisionOutcome.DEFERRED_TO_HUMAN: "decision.deferred_to_human",
    DecisionOutcome.ESCALATED: "governance.human_escalation_raised",
}

# decisions table outcome values (migration 0001 CHECK constraint)
_OUTCOME_TO_DB: dict[DecisionOutcome, str] = {
    DecisionOutcome.APPROVED: "approved",
    DecisionOutcome.REJECTED: "rejected",
    DecisionOutcome.DEFERRED_TO_HUMAN: "deferred_to_human",
    DecisionOutcome.ESCALATED: "deferred_to_human",
}


def _strip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _flatten_for_stream(payload: dict, prefix: str = "") -> dict[str, str]:
    """Recursively flatten nested dict to dot-separated string pairs for XADD."""
    out: dict[str, str] = {}
    for k, v in payload.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_for_stream(v, full_key))
        elif isinstance(v, list):
            out[full_key] = json.dumps(v)
        else:
            out[full_key] = str(v) if v is not None else ""
    return out


def _audit_result_str(step: EvaluationStepRecord) -> str:
    if step.outcome == DecisionOutcome.REJECTED:
        return "denied"
    if step.outcome in (DecisionOutcome.DEFERRED_TO_HUMAN, DecisionOutcome.ESCALATED):
        return "escalated"
    return "success"


# ---------------------------------------------------------------------------
# Field extraction helpers (publisher only receives DecisionResult, not context)
# ---------------------------------------------------------------------------

def _extract_correlation_id(result: DecisionResult) -> UUID:
    for step in result.steps:
        raw = step.detail.get("correlation_id")
        if raw is not None:
            try:
                return UUID(str(raw))
            except (ValueError, AttributeError):
                pass
    return uuid4()


def _extract_causation_id(result: DecisionResult) -> UUID | None:
    try:
        return UUID(result.event_id)
    except (ValueError, AttributeError):
        return None


def _extract_partition_key(result: DecisionResult) -> str:
    for step in result.steps:
        pk = step.detail.get("partition_key")
        if pk:
            return str(pk)
    return result.decision_id


def _extract_proposing_agent(result: DecisionResult) -> str:
    for step in result.steps:
        agent = step.detail.get("proposing_agent") or step.detail.get("source_agent_id")
        if agent:
            return str(agent)
    return "unknown"


def _extract_authority_level(result: DecisionResult) -> str:
    for step in result.steps:
        lvl = step.detail.get("authority_level")
        if lvl:
            return str(lvl)
    return "worker"


def _extract_action_kind(result: DecisionResult) -> str:
    for step in result.steps:
        ak = step.detail.get("action_kind") or step.detail.get("event_type")
        if ak:
            return str(ak)
    return "unknown"


def _extract_department(result: DecisionResult) -> str:
    for step in result.steps:
        dept = step.detail.get("department")
        if dept:
            return str(dept)
    return "unknown"


def _extract_governance_token_id(result: DecisionResult) -> UUID | None:
    for step in result.steps:
        gt = step.detail.get("governance_token_id")
        if gt:
            try:
                return UUID(str(gt))
            except (ValueError, AttributeError):
                pass
    return None


def _extract_approved_scope(result: DecisionResult) -> dict[str, str]:
    for step in result.steps:
        scope = step.detail.get("approved_scope")
        if isinstance(scope, dict):
            return {str(k): str(v) for k, v in scope.items()}
    return {"action_kind": _extract_action_kind(result)}


def _find_rejecting_stage(result: DecisionResult) -> str:
    for step in result.steps:
        if step.outcome == DecisionOutcome.REJECTED:
            return step.stage.value
    return "unknown"


def _build_proposal_json(result: DecisionResult) -> dict:
    return {
        "event_id": result.event_id,
        "outcome": result.outcome.value,
        "final_reason": result.final_reason,
        "steps": [
            {
                "stage": s.stage.value,
                "passed": s.passed,
                "outcome": s.outcome.value if s.outcome else None,
                "duration_ms": s.duration_ms,
            }
            for s in result.steps
        ],
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


class DecisionEventPublisher:
    """Publishes decision outcomes: Postgres write (source of truth) then Redis XADD.

    Redis is not the source of truth. If XADD fails after the Postgres INSERT
    the event is dead-lettered to ``evt:dlq:decision_engine``; the decision row
    is not rolled back.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        db: "Database",
        settings: DecisionEngineSettings,
    ) -> None:
        self._redis = redis
        self._db = db
        self._settings = settings

    async def publish_outcome(self, result: DecisionResult) -> None:
        """Write decision to Postgres, then XADD to Redis stream.

        Validation happens before any I/O. Postgres write is the commit point.
        Redis failure after Postgres commit → dead-letter + log, no rollback.
        """
        event_type = _OUTCOME_TO_EVENT_TYPE.get(result.outcome)
        if event_type is None:
            raise DecisionEngineError(
                f"No event_type mapping for outcome {result.outcome!r}"
            )

        payload = await self._validate_outbound(event_type, await self._build_outbound_payload(result))
        stream_key = f"evt:{result.tenant_id}:decisions"

        async with self._db.tenant_session(result.tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, org_id, correlation_id, causation_event_id,
                    partition_key, proposing_agent, authority_level, action_kind,
                    proposal_json, outcome, outcome_reason, score_json,
                    governance_token_id, resolved_at, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (decision_id) DO NOTHING
                """,
                UUID(result.decision_id),
                result.tenant_id,
                _extract_correlation_id(result),
                _extract_causation_id(result),
                _extract_partition_key(result),
                _extract_proposing_agent(result),
                _extract_authority_level(result),
                _extract_action_kind(result),
                json.dumps(_build_proposal_json(result), default=str),
                _OUTCOME_TO_DB[result.outcome],
                result.final_reason,
                json.dumps(_build_score_json(result), default=str) if result.scoring else None,
                _extract_governance_token_id(result),
                result.evaluated_at,
                result.evaluated_at,
            )

        # Postgres committed — Redis failure must not roll it back
        fields = _flatten_for_stream(payload)
        fields["event_type"] = event_type
        try:
            await self._redis.xadd(stream_key, fields)
        except Exception as exc:
            log.error(
                "decision_redis_publish_failed_dead_lettering",
                extra={
                    "decision_id": result.decision_id,
                    "tenant_id": result.tenant_id,
                    "event_type": event_type,
                    "error": str(exc),
                },
                exc_info=True,
            )
            await self._dead_letter(result.tenant_id, event_type, payload, str(exc))
            return

        log.info(
            "decision_published",
            extra={
                "decision_id": result.decision_id,
                "tenant_id": result.tenant_id,
                "outcome": result.outcome.value,
                "event_type": event_type,
                "stream_key": stream_key,
            },
        )

    async def mirror_audit_step(
        self, tenant_id: str, step: EvaluationStepRecord, decision_id: str
    ) -> None:
        """Insert one audit_log row for a pipeline evaluation step.

        Uses the pool (tenant_session acquires from it) — no new connection per call.
        RLS: set_config('skylize.org_id') is applied inside tenant_session().
        """
        try:
            async with self._db.tenant_session(tenant_id) as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_log (
                        event_id, org_id, tenant_id, correlation_id,
                        source_agent_id, action_type, result, result_reason,
                        occurred_at, recorded_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    uuid4(),
                    tenant_id,
                    tenant_id,
                    step.detail.get("correlation_id")
                        and _safe_uuid(step.detail["correlation_id"])
                        or uuid4(),
                    step.detail.get("source_agent_id"),
                    f"decision_engine.stage.{step.stage.value}",
                    _audit_result_str(step),
                    json.dumps(
                        {
                            "decision_id": decision_id,
                            "stage": step.stage.value,
                            "passed": step.passed,
                            "outcome": step.outcome.value if step.outcome else None,
                            "detail": step.detail,
                            "duration_ms": step.duration_ms,
                        },
                        default=str,
                    ),
                    step.timestamp,
                    datetime.now(timezone.utc),
                )
        except asyncpg.PostgresError as exc:
            log.warning(
                "audit_step_mirror_failed",
                extra={
                    "tenant_id": tenant_id,
                    "decision_id": decision_id,
                    "stage": step.stage.value,
                    "error": str(exc),
                },
                exc_info=True,
            )
            raise DecisionEngineError(
                f"audit_log insert failed for stage {step.stage.value}: {exc}"
            ) from exc

    async def _validate_outbound(self, event_type: str, payload: dict) -> dict:
        """Validate payload against its Pydantic v2 schema and strip None values.

        Raises DecisionEngineError on validation failure — never publish invalid events.
        For governance.human_escalation_raised (no typed schema yet) runs a
        structural check (required keys present, all values serialisable) instead.
        """
        decision_event_map: dict[str, type] = {
            "decision.approved": DecisionApproved,
            "decision.rejected": DecisionRejected,
            "decision.deferred_to_human": DecisionDeferredToHuman,
        }

        schema_cls = decision_event_map.get(event_type)
        if schema_cls is not None:
            try:
                validated = schema_cls.model_validate(payload)
                return _strip_none(validated.model_dump(mode="json"))
            except ValidationError as exc:
                log.error(
                    "outbound_event_validation_failed",
                    extra={"event_type": event_type, "errors": exc.errors()},
                )
                raise DecisionEngineError(
                    f"Outbound event validation failed for {event_type}: {exc}"
                ) from exc

        # governance.human_escalation_raised — no typed schema; structural check
        required_keys = {"decision_id", "tenant_id", "event_type"}
        payload_with_type = {**payload, "event_type": event_type}
        missing = required_keys - payload_with_type.keys()
        if missing:
            msg = f"Outbound payload for {event_type} missing keys: {missing}"
            log.error("outbound_event_validation_failed", extra={"event_type": event_type, "missing": list(missing)})
            raise DecisionEngineError(msg)
        try:
            json.dumps(payload, default=str)
        except (TypeError, ValueError) as exc:
            log.error("outbound_event_not_serialisable", extra={"event_type": event_type, "error": str(exc)})
            raise DecisionEngineError(f"Outbound payload for {event_type} is not JSON-serialisable: {exc}") from exc

        return _strip_none(payload)

    async def _build_outbound_payload(self, result: DecisionResult) -> dict:
        """Build the outbound payload dict (before validation)."""
        outcome = result.outcome

        if outcome == DecisionOutcome.APPROVED:
            event = DecisionApproved(
                tenant_id=result.tenant_id,
                partition_key=result.decision_id,
                department=_extract_department(result),
                correlation_id=_extract_correlation_id(result),
                payload=DecisionApproved.Payload(
                    decision_id=UUID(result.decision_id),
                    action_kind=_extract_action_kind(result),
                    approved_scope=_extract_approved_scope(result),
                ),
            )
            return event.model_dump(mode="json")

        if outcome == DecisionOutcome.REJECTED:
            event = DecisionRejected(
                tenant_id=result.tenant_id,
                partition_key=result.decision_id,
                department=_extract_department(result),
                correlation_id=_extract_correlation_id(result),
                payload=DecisionRejected.Payload(
                    decision_id=UUID(result.decision_id),
                    action_kind=_extract_action_kind(result),
                    stage_rejected_at=_find_rejecting_stage(result),
                    reasons=[result.final_reason],
                    policy_version=None,
                ),
            )
            return event.model_dump(mode="json")

        # DEFERRED_TO_HUMAN uses decision.deferred_to_human schema
        if outcome == DecisionOutcome.DEFERRED_TO_HUMAN:
            event = DecisionDeferredToHuman(
                tenant_id=result.tenant_id,
                partition_key=result.decision_id,
                department=_extract_department(result),
                correlation_id=_extract_correlation_id(result),
                payload=DecisionDeferredToHuman.Payload(
                    decision_id=UUID(result.decision_id),
                    hitl_id=uuid4(),
                    trigger_reason=result.final_reason,
                    routed_to="hitl_queue",
                ),
            )
            return event.model_dump(mode="json")

        # ESCALATED → governance.human_escalation_raised (no typed schema yet)
        return {
            "decision_id": result.decision_id,
            "tenant_id": result.tenant_id,
            "event_type": "governance.human_escalation_raised",
            "outcome": result.outcome.value,
            "final_reason": result.final_reason,
            "department": _extract_department(result),
            "correlation_id": str(_extract_correlation_id(result)),
            "proposing_agent": _extract_proposing_agent(result),
            "authority_level": _extract_authority_level(result),
            "escalated_at": result.evaluated_at.isoformat(),
        }

    async def _dead_letter(
        self, tenant_id: str, event_type: str, payload: dict, error: str
    ) -> None:
        dlq_key = self._settings.redis_dlq_stream
        try:
            await self._redis.xadd(
                dlq_key,
                {
                    "tenant_id": tenant_id,
                    "event_type": event_type,
                    "payload": json.dumps(payload, default=str),
                    "error": error,
                    "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            log.error(
                "dead_letter_xadd_failed",
                extra={"tenant_id": tenant_id, "event_type": event_type},
                exc_info=True,
            )


def _safe_uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return uuid4()
