"""The six-stage evaluation pipeline (decision_engine.md §4, decision_flow.md §3).

A single decision walks the stages in order:

    AUTHORITY → OPA_POLICY → SCORING → CAPITAL → CONFLICT → HITL_GATE

The first stage that produces a terminal outcome short-circuits the rest (most
restrictive wins). Every stage appends an append-only ``EvaluationStepRecord`` to
``DecisionContext.steps``, mirrors an ``AuditEvent`` onto the audit stream, and is
measured (``duration_ms``). The whole pipeline is wrapped in a hard timeout so a
hung policy/capital call can never stall a consumer worker forever.

Telemetry (Langfuse trace + span per stage, OTel span attributes) is best-effort:
it is recorded inside try/except and never fails a decision.

Determinism (decision_engine.md §5): the decision logic is deterministic given
inputs. The ``decision_id`` is therefore derived deterministically from the
originating ``event_id`` (``uuid5``) so a redelivered proposal reconstructs the
same ticket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .capital_dal import CapitalDAL
from .config import DecisionEngineSettings
from .constants import (
    ALLOWED_DEPARTMENTS,
    ALLOWED_EVENT_TYPES_BY_DEPARTMENT,
    APPROVAL_SIGNAL_KEYS,
    AUDIT_EVENT_TYPE_PREFIX,
    HITL_HIGH_RISK_OPPORTUNITY_FLOOR,
    MAX_EVALUATION_TIMEOUT_SECONDS,
    REJECTION_SIGNAL_KEYS,
)
from .exceptions import ConflictDetected, EvaluationTimeout, OPAPolicyDenied
from .models import (
    CapitalCheckResult,
    DecisionContext,
    DecisionOutcome,
    DecisionResult,
    EvaluationStage,
    EvaluationStepRecord,
    RiskBand,
    ScoringResult,
)
from .opa_client import OPAClient
from .scoring import ScoringEngine

if TYPE_CHECKING:
    from ..events.bus import EventBus

log = logging.getLogger(__name__)

# Deterministic namespace for decision_id derivation from event_id.
_DECISION_NS = uuid5(NAMESPACE_URL, "skylize.decision_engine.decision_id")


def decision_id_for(event_id: str) -> str:
    """Deterministically map an ``event_id`` to its ``decision_id`` (uuid5)."""
    return str(uuid5(_DECISION_NS, event_id))


class _StageTerminal(Exception):  # noqa: N818 — internal control-flow signal, not an error
    """Internal: a stage produced a terminal outcome; stop the pipeline.

    Carries the terminal outcome and human-readable reason so ``_run_stages``
    can build the final ``DecisionResult`` without threading return values
    through every stage.
    """

    def __init__(self, outcome: DecisionOutcome, reason: str) -> None:
        self.outcome = outcome
        self.reason = reason
        super().__init__(reason)


class EvaluationPipeline:
    """Runs the six-stage evaluation for one ``DecisionContext``."""

    def __init__(
        self,
        opa_client: OPAClient,
        scoring_engine: ScoringEngine,
        capital_dal: CapitalDAL,
        settings: DecisionEngineSettings,
        *,
        event_bus: "EventBus | None" = None,
        langfuse_client: Any | None = None,
        tracer: Any | None = None,
    ) -> None:
        # ``event_bus`` is the sink for mirrored AuditEvents (department="audit"
        # routes to ``evt:{tenant}:audit``). It is not in the original task
        # __init__ list but is required to publish audit mirrors; audit publish
        # is always best-effort and never fails a decision.
        self._opa = opa_client
        self._scoring = scoring_engine
        self._capital = capital_dal
        self._settings = settings
        self._event_bus = event_bus
        self._langfuse = langfuse_client
        self._tracer = tracer

    # -- public API ---------------------------------------------------------

    async def evaluate(self, context: DecisionContext) -> DecisionResult:
        """Evaluate a proposal end-to-end, bounded by a hard timeout.

        Raises ``EvaluationTimeout`` if the pipeline exceeds
        ``MAX_EVALUATION_TIMEOUT_SECONDS`` (so the consumer leaves the message
        un-ACKed for redelivery / DLQ).
        """
        trace = self._start_trace(context)
        otel_span_cm = (
            self._tracer.start_as_current_span("decision_engine.pipeline")
            if self._tracer is not None
            else None
        )
        if otel_span_cm is not None:
            otel_span_cm.__enter__()
        try:
            return await asyncio.wait_for(
                self._run_stages(context),
                timeout=MAX_EVALUATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise EvaluationTimeout(
                f"evaluation exceeded {MAX_EVALUATION_TIMEOUT_SECONDS}s "
                f"for event_id={context.event_id}"
            ) from exc
        finally:
            if otel_span_cm is not None:
                try:
                    otel_span_cm.__exit__(None, None, None)
                except Exception:  # pragma: no cover - telemetry must not raise
                    log.debug("otel_pipeline_span_exit_failed", exc_info=True)
            self._end_trace(trace)

    # -- stage orchestration ------------------------------------------------

    async def _run_stages(self, context: DecisionContext) -> DecisionResult:
        scoring: ScoringResult | None = None
        capital: CapitalCheckResult | None = None
        preliminary: DecisionOutcome | None = None

        try:
            self._stage_authority(context)

            await self._stage_opa(context)

            scoring, preliminary = self._stage_scoring(context)

            capital = await self._stage_capital(context)

            self._stage_conflict(context)

            outcome, reason = self._stage_hitl_gate(context, scoring, preliminary)
        except _StageTerminal as terminal:
            outcome, reason = terminal.outcome, terminal.reason

        return DecisionResult(
            decision_id=decision_id_for(context.event_id),
            event_id=context.event_id,
            tenant_id=context.tenant_id,
            outcome=outcome,
            scoring=scoring,
            capital=capital,
            final_reason=reason,
            steps=list(context.steps),
            evaluated_at=datetime.now(timezone.utc),
        )

    # -- STAGE 1: AUTHORITY -------------------------------------------------

    def _stage_authority(self, context: DecisionContext) -> None:
        start = time.monotonic()
        allowed_types = ALLOWED_EVENT_TYPES_BY_DEPARTMENT.get(
            context.department, frozenset()
        )
        dept_ok = context.department in ALLOWED_DEPARTMENTS
        type_ok = context.event_type in allowed_types
        passed = dept_ok and type_ok

        if not dept_ok:
            reason = f"department {context.department!r} not served by the engine"
        elif not type_ok:
            reason = (
                f"event_type {context.event_type!r} not permitted for "
                f"department {context.department!r}"
            )
        else:
            reason = "authority check passed"

        detail = {
            "department": context.department,
            "event_type": context.event_type,
            "department_allowed": dept_ok,
            "event_type_allowed": type_ok,
        }
        outcome = None if passed else DecisionOutcome.REJECTED
        self._record_step(
            context, EvaluationStage.AUTHORITY, passed, outcome, detail, start
        )
        if not passed:
            raise _StageTerminal(DecisionOutcome.REJECTED, reason)

    # -- STAGE 2: OPA_POLICY ------------------------------------------------

    async def _stage_opa(self, context: DecisionContext) -> None:
        start = time.monotonic()
        try:
            allow, deny_reasons = await self._opa.evaluate(context)
        except OPAPolicyDenied as exc:
            detail = {"allow": False, "denial_reason": exc.denial_reason}
            self._record_step(
                context,
                EvaluationStage.OPA_POLICY,
                False,
                DecisionOutcome.REJECTED,
                detail,
                start,
            )
            raise _StageTerminal(
                DecisionOutcome.REJECTED, f"policy denied: {exc.denial_reason}"
            ) from exc

        passed = allow
        detail = {"allow": allow, "deny_reasons": deny_reasons}
        outcome = None if passed else DecisionOutcome.REJECTED
        self._record_step(
            context, EvaluationStage.OPA_POLICY, passed, outcome, detail, start
        )
        if not passed:
            reason = (
                "policy denied: " + "; ".join(deny_reasons)
                if deny_reasons
                else "policy denied (no explicit allow)"
            )
            raise _StageTerminal(DecisionOutcome.REJECTED, reason)

    # -- STAGE 3: SCORING ---------------------------------------------------

    def _stage_scoring(
        self, context: DecisionContext
    ) -> tuple[ScoringResult, DecisionOutcome]:
        start = time.monotonic()
        scoring = self._scoring.score(context)
        preliminary = ScoringEngine.lookup_matrix(
            scoring.risk_band, scoring.opportunity_score
        )
        critical = scoring.risk_band is RiskBand.CRITICAL
        # Scoring is never terminal on its own EXCEPT a CRITICAL risk band, which
        # is an immediate reject (decision_engine.md §4: most restrictive wins).
        detail = {
            "risk_score": scoring.risk_score,
            "opportunity_score": scoring.opportunity_score,
            "risk_band": scoring.risk_band.value,
            "confidence": scoring.confidence,
            "preliminary_outcome": preliminary.value,
            "factors": scoring.factors,
        }
        outcome = DecisionOutcome.REJECTED if critical else None
        self._record_step(
            context,
            EvaluationStage.SCORING,
            not critical,
            outcome,
            detail,
            start,
        )
        if critical:
            raise _StageTerminal(
                DecisionOutcome.REJECTED,
                f"CRITICAL risk band (risk_score={scoring.risk_score})",
            )
        return scoring, preliminary

    # -- STAGE 4: CAPITAL ---------------------------------------------------

    async def _stage_capital(
        self, context: DecisionContext
    ) -> CapitalCheckResult | None:
        start = time.monotonic()
        requested = await self._capital.extract_requested_amount(context)

        if requested is None:
            detail: dict[str, Any] = {"requested_amount": None, "auto_pass": True}
            self._record_step(
                context, EvaluationStage.CAPITAL, True, None, detail, start
            )
            return None

        result = await self._capital.check_capital_ceiling(
            context.tenant_id, context.department, requested
        )
        detail = {
            "requested_amount": str(result.requested_amount),
            "available_budget": str(result.available_budget),
            "ceiling_pct": result.ceiling_pct,
            "passes": result.passes,
            "reason": result.reason,
        }
        outcome = None if result.passes else DecisionOutcome.REJECTED
        self._record_step(
            context, EvaluationStage.CAPITAL, result.passes, outcome, detail, start
        )
        if not result.passes:
            raise _StageTerminal(DecisionOutcome.REJECTED, result.reason)
        return result

    # -- STAGE 5: CONFLICT --------------------------------------------------

    def _stage_conflict(self, context: DecisionContext) -> None:
        start = time.monotonic()
        keys = set(context.payload)
        approval_present = sorted(keys & APPROVAL_SIGNAL_KEYS)
        rejection_present = sorted(keys & REJECTION_SIGNAL_KEYS)
        conflict = bool(approval_present and rejection_present)

        detail = {
            "approval_signals": approval_present,
            "rejection_signals": rejection_present,
            "conflict": conflict,
        }
        outcome = DecisionOutcome.DEFERRED_TO_HUMAN if conflict else None
        self._record_step(
            context, EvaluationStage.CONFLICT, not conflict, outcome, detail, start
        )
        if conflict:
            conflict_keys = approval_present + rejection_present
            exc = ConflictDetected(conflict_keys)
            raise _StageTerminal(DecisionOutcome.DEFERRED_TO_HUMAN, str(exc))

    # -- STAGE 6: HITL_GATE -------------------------------------------------

    def _stage_hitl_gate(
        self,
        context: DecisionContext,
        scoring: ScoringResult,
        preliminary: DecisionOutcome,
    ) -> tuple[DecisionOutcome, str]:
        start = time.monotonic()

        if preliminary in (
            DecisionOutcome.DEFERRED_TO_HUMAN,
            DecisionOutcome.ESCALATED,
        ):
            outcome = DecisionOutcome.DEFERRED_TO_HUMAN
            reason = f"scoring matrix routed to human ({preliminary.value})"
        elif (
            scoring.risk_band is RiskBand.HIGH
            and scoring.opportunity_score < HITL_HIGH_RISK_OPPORTUNITY_FLOOR
        ):
            outcome = DecisionOutcome.DEFERRED_TO_HUMAN
            reason = (
                f"HIGH risk with low opportunity "
                f"({scoring.opportunity_score} < {HITL_HIGH_RISK_OPPORTUNITY_FLOOR})"
            )
        else:
            outcome = preliminary  # APPROVED or REJECTED from the matrix
            reason = f"confirmed {outcome.value} (risk_band={scoring.risk_band.value})"

        passed = outcome is DecisionOutcome.APPROVED
        detail = {
            "preliminary_outcome": preliminary.value,
            "final_outcome": outcome.value,
            "risk_band": scoring.risk_band.value,
            "opportunity_score": scoring.opportunity_score,
        }
        self._record_step(
            context, EvaluationStage.HITL_GATE, passed, outcome, detail, start
        )
        return outcome, reason

    # -- step recording + telemetry ----------------------------------------

    def _record_step(
        self,
        context: DecisionContext,
        stage: EvaluationStage,
        passed: bool,
        outcome: DecisionOutcome | None,
        detail: dict[str, Any],
        start_time: float,
    ) -> None:
        """Append the step (append-only), record telemetry, mirror an audit event."""
        duration_ms = (time.monotonic() - start_time) * 1000.0
        step = EvaluationStepRecord(
            stage=stage,
            passed=passed,
            outcome=outcome,
            detail=detail,
            duration_ms=round(duration_ms, 3),
            timestamp=datetime.now(timezone.utc),
        )
        # Append-only: never mutate existing steps (decision_engine.md §5 replay).
        context.steps.append(step)

        self._record_langfuse_span(context, step)
        self._record_otel_span(step)
        self._emit_audit_event(context, step)

    def _record_langfuse_span(
        self, context: DecisionContext, step: EvaluationStepRecord
    ) -> None:
        if self._langfuse is None:
            return
        try:
            trace = self._langfuse.trace(
                id=context.event_id, name="decision_engine.evaluate"
            )
            trace.span(
                name=f"stage.{step.stage.value}",
                metadata={
                    "stage": step.stage.value,
                    "passed": step.passed,
                    "outcome": step.outcome.value if step.outcome else None,
                    "duration_ms": step.duration_ms,
                },
            )
        except Exception:  # pragma: no cover - telemetry must not raise
            log.debug("langfuse_stage_span_failed", exc_info=True)

    def _record_otel_span(self, step: EvaluationStepRecord) -> None:
        if self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span(
                f"decision_engine.stage.{step.stage.value}"
            ) as span:
                span.set_attribute("decision_engine.stage", step.stage.value)
                span.set_attribute("decision_engine.passed", step.passed)
                span.set_attribute(
                    "decision_engine.outcome",
                    step.outcome.value if step.outcome else "none",
                )
                span.set_attribute("decision_engine.duration_ms", step.duration_ms)
        except Exception:  # pragma: no cover - telemetry must not raise
            log.debug("otel_stage_span_failed", exc_info=True)

    # -- audit mirror -------------------------------------------------------

    def _emit_audit_event(
        self, context: DecisionContext, step: EvaluationStepRecord
    ) -> None:
        """Mirror a step onto ``evt:{tenant}:audit``. Never raises.

        Uses the platform ``AuditActionRecorded`` event (the PII-safe audit
        spine). The full structured ``detail`` also lives in
        ``DecisionResult.steps`` / ``decision.evaluated`` — the authoritative
        record — so the mirror carries a compact summary in ``result_reason``.
        """
        if self._event_bus is None:
            return
        try:
            from ..schemas.events.audit import AuditActionRecorded

            event = AuditActionRecorded(
                tenant_id=context.tenant_id,
                partition_key=self._partition_key(context),
                department="audit",
                correlation_id=self._correlation_id(context),
                payload=AuditActionRecorded.Payload(
                    action_type=f"{AUDIT_EVENT_TYPE_PREFIX}.{step.stage.value}",
                    result=self._audit_result(step),
                    result_reason=json.dumps(
                        {
                            "stage": step.stage.value,
                            "outcome": step.outcome.value if step.outcome else None,
                            "detail": step.detail,
                        },
                        default=str,
                    ),
                ),
            )
        except Exception:  # pragma: no cover - construction must not raise
            log.warning("decision_engine_audit_build_failed", exc_info=True)
            return

        bus = self._event_bus

        async def _publish() -> None:
            try:
                await bus.publish(event)
            except Exception:
                log.warning("decision_engine_audit_publish_failed", exc_info=True)

        # Fire-and-forget so a slow/broken audit sink never blocks the decision.
        asyncio.ensure_future(_publish())

    @staticmethod
    def _audit_result(step: EvaluationStepRecord) -> str:
        if step.outcome is DecisionOutcome.REJECTED:
            return "denied"
        if step.outcome in (
            DecisionOutcome.DEFERRED_TO_HUMAN,
            DecisionOutcome.ESCALATED,
        ):
            return "escalated"
        return "success"

    @staticmethod
    def _partition_key(context: DecisionContext) -> str:
        value = context.payload.get("partition_key")
        return str(value) if value else context.event_id

    @staticmethod
    def _correlation_id(context: DecisionContext) -> UUID:
        raw = context.payload.get("correlation_id")
        if isinstance(raw, UUID):
            return raw
        if raw is not None:
            try:
                return UUID(str(raw))
            except (ValueError, AttributeError):
                pass
        # Deterministic fallback so the mirror is still correlatable to the event.
        return uuid5(_DECISION_NS, context.event_id)

    # -- trace lifecycle ----------------------------------------------------

    def _start_trace(self, context: DecisionContext) -> Any | None:
        if self._langfuse is None:
            return None
        try:
            return self._langfuse.trace(
                id=context.event_id,
                name="decision_engine.evaluate",
                metadata={
                    "tenant_id": context.tenant_id,
                    "department": context.department,
                    "event_type": context.event_type,
                },
            )
        except Exception:  # pragma: no cover - telemetry must not raise
            log.debug("langfuse_trace_start_failed", exc_info=True)
            return None

    def _end_trace(self, trace: Any | None) -> None:
        if trace is None or self._langfuse is None:
            return
        try:
            flush = getattr(self._langfuse, "flush", None)
            if callable(flush):
                flush()
        except Exception:  # pragma: no cover - telemetry must not raise
            log.debug("langfuse_flush_failed", exc_info=True)


__all__ = ["EvaluationPipeline", "decision_id_for"]
