"""
HitlQueueService — the human side of the synchronous decision gate.

A deferred agent-execute request sits in ``hitl_queue`` as status='pending'
with a replayable ``request_json`` envelope (owner decisions K4/K6). This
service closes the loop:

  * ``list_pending`` — the reviewer's org-scoped queue (partial index shape).
  * ``approve``      — claim the row exactly once, replay the ORIGINAL request
                       through the SAME AgentExecutionService.execute() path
                       (gate satisfied by the human verdict, never re-deferred),
                       record the verdict, emit the terminal decision event +
                       audit record synchronously before returning.
  * ``reject``       — claim the row exactly once, record the verdict, emit
                       DecisionRejected + audit. Nothing executes.

Exactly-once (brief item 11): the ONLY mutation path is the repository's
conditional ``UPDATE ... WHERE status='pending' ... RETURNING`` claim — never a
read-then-write. Two simultaneous verdicts race on that predicate; Postgres
serializes them and exactly one wins.

Failure after approval (owner decisions K7/K12): if the stored payload no
longer validates (schema drift), the agent is gone, or execution fails, the row
is RELEASED back to 'pending' (verdict cleared) and the failure is audited —
the approved work is never silently lost, and no status outside the existing
CHECK vocabulary is invented. The caller sees a typed error and can retry once
the cause is fixed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from ...contracts.registry import AgentNotRegistered
from ...dal.ports import DeliverableRow, HitlQueueItem, HitlQueueRepository
from ...events.bus import EventBus
from ...schemas.events.decision import DecisionApproved, DecisionRejected
from ...schemas.hitl import HitlReplayEnvelope
from ..agents.execution import (
    AgentExecutionService,
    AgentInputError,
    HitlApprovalContext,
)
from ..audit.service import AuditService
from ..decision_engine.events import decision_id_for

log = logging.getLogger(__name__)


class HitlError(Exception):
    """Base for typed HITL verdict failures (the route maps each subclass)."""


class HitlNotFound(HitlError):
    """No such hitl_id in the caller's org (RLS also hides other orgs' rows)."""


class HitlAlreadyActioned(HitlError):
    """The row is not 'pending' — a verdict already exists (idempotency: the
    second call gets THIS, never a re-execution)."""

    def __init__(self, status: str) -> None:
        super().__init__(f"hitl item already actioned: status={status}")
        self.status = status


class HitlExpired(HitlError):
    """The row's expires_at has passed; a verdict can no longer act on it."""


class HitlNotReplayable(HitlError):
    """The row carries no (valid) request_json envelope — approval cannot
    reconstruct the work (pre-0015 rows, OPA-side writer rows)."""


class HitlReplayInvalid(HitlError):
    """K7: the stored payload failed re-validation against the agent's CURRENT
    input schema (or the agent is no longer registered). Row released back to
    'pending'; nothing executed."""


class HitlExecutionFailed(HitlError):
    """K12: execution failed AFTER the approval claim. Row released back to
    'pending' so the approved work is not silently lost; the failure is
    audited and the reviewer can retry."""


class HitlQueueService:
    def __init__(
        self,
        *,
        repo: HitlQueueRepository,
        execution: AgentExecutionService,
        audit: AuditService,
        bus: EventBus,
    ) -> None:
        self._repo = repo
        self._execution = execution
        self._audit = audit
        self._bus = bus

    # -- reads ---------------------------------------------------------------

    async def list_pending(
        self, org_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[HitlQueueItem], int]:
        return await self._repo.list_pending(org_id, limit=limit, offset=offset)

    # -- verdicts ------------------------------------------------------------

    async def approve(
        self, *, org_id: str, hitl_id: UUID, reviewed_by: str, note: str | None = None
    ) -> tuple[HitlQueueItem, DeliverableRow]:
        now = datetime.now(timezone.utc)
        fresh_correlation = uuid4()  # K8: the replay run's own correlation
        verdict: dict[str, Any] = {
            "verdict": "approved", "reviewed_by": reviewed_by, "note": note,
        }
        row = await self._repo.claim(
            hitl_id, org_id,
            status_to="approved", verdict_by=reviewed_by,
            verdict_json=verdict, verdict_at=now, require_request=True,
        )
        if row is None:
            await self._raise_refusal(hitl_id, org_id, now=now, for_approve=True)
            raise AssertionError("unreachable")  # _raise_refusal always raises

        try:
            envelope = HitlReplayEnvelope.model_validate(row.request_json)
        except ValidationError as exc:
            await self._release_failed(
                row, org_id, fresh_correlation,
                action_type="hitl.approve_failed",
                reason=f"stored request envelope invalid: {exc}",
            )
            raise HitlNotReplayable(
                f"stored request envelope is not a valid HitlReplayEnvelope: {exc}"
            ) from exc

        try:
            deliverable = await self._execution.execute(
                org_id=org_id,
                agent_id=envelope.agent_id,
                input_data=envelope.input,
                user_id=envelope.user_id,
                hitl_approval=HitlApprovalContext(
                    hitl_id=row.hitl_id,
                    decision_id=row.decision_id,
                    original_correlation_id=envelope.correlation_id,
                    approved_by=reviewed_by,
                ),
            )
        except (AgentInputError, AgentNotRegistered) as exc:
            # K7: schema drift / vanished agent — fail loudly, row stays
            # actionable, nothing executed.
            await self._release_failed(
                row, org_id, fresh_correlation,
                action_type="hitl.approve_failed",
                reason=f"replay_invalid: {exc}",
                source_agent_id=envelope.agent_id,
            )
            raise HitlReplayInvalid(str(exc)) from exc
        except Exception as exc:
            # K12: anything that failed after the claim (LLM egress, output
            # validation, governance denial, budget) — release, audit, surface.
            await self._release_failed(
                row, org_id, fresh_correlation,
                action_type="hitl.approve_failed",
                reason=f"execution_failed: {exc}",
                source_agent_id=envelope.agent_id,
            )
            raise HitlExecutionFailed(str(exc)) from exc

        verdict["deliverable_id"] = str(deliverable.id)
        await self._repo.update_verdict_json(hitl_id, org_id, verdict)

        # Terminal decision event + audit, synchronously before responding.
        decision_id = self._decision_id(row)
        if decision_id is not None:
            await self._bus.publish(
                DecisionApproved(
                    tenant_id=org_id,
                    partition_key=row.partition_key,
                    department="decision",
                    correlation_id=fresh_correlation,
                    causation_id=row.correlation_id,
                    payload=DecisionApproved.Payload(
                        decision_id=decision_id,
                        action_kind=self._action_kind(row),
                        approved_scope={
                            "agent": envelope.agent_id,
                            "department": str(row.proposal_json.get("department", "")),
                            "partition_key": row.partition_key,
                        },
                    ),
                )
            )
        else:  # pragma: no cover - request-path rows always carry decision_id
            log.warning("hitl_approve_no_decision_id", extra={"hitl_id": str(hitl_id)})
        await self._audit.record(
            org_id=org_id,
            correlation_id=fresh_correlation,
            causation_id=row.correlation_id,  # K8: the ORIGINAL correlation
            action_type="hitl.approved",
            result="success",
            source_agent_id=envelope.agent_id,
            partition_key=row.partition_key,
            inputs={"hitl_id": str(row.hitl_id), "trigger_reason": row.trigger_reason},
            outputs={"deliverable_id": str(deliverable.id)},
            result_reason=f"approved by {reviewed_by}",
        )
        return row, deliverable

    async def reject(
        self, *, org_id: str, hitl_id: UUID, reviewed_by: str, note: str | None = None
    ) -> HitlQueueItem:
        now = datetime.now(timezone.utc)
        fresh_correlation = uuid4()
        verdict: dict[str, Any] = {
            "verdict": "rejected", "reviewed_by": reviewed_by, "note": note,
        }
        row = await self._repo.claim(
            hitl_id, org_id,
            status_to="rejected", verdict_by=reviewed_by,
            verdict_json=verdict, verdict_at=now, require_request=False,
        )
        if row is None:
            await self._raise_refusal(hitl_id, org_id, now=now, for_approve=False)
            raise AssertionError("unreachable")

        reasons = [f"rejected by human reviewer {reviewed_by}"]
        if note:
            reasons.append(note)
        decision_id = self._decision_id(row)
        if decision_id is not None:
            await self._bus.publish(
                DecisionRejected(
                    tenant_id=org_id,
                    partition_key=row.partition_key,
                    department="decision",
                    correlation_id=fresh_correlation,
                    causation_id=row.correlation_id,
                    payload=DecisionRejected.Payload(
                        decision_id=decision_id,
                        action_kind=self._action_kind(row),
                        stage_rejected_at="hitl_gate",
                        reasons=reasons,
                    ),
                )
            )
        else:  # pragma: no cover
            log.warning("hitl_reject_no_decision_id", extra={"hitl_id": str(hitl_id)})
        await self._audit.record(
            org_id=org_id,
            correlation_id=fresh_correlation,
            causation_id=row.correlation_id,  # K8
            action_type="hitl.rejected",
            result="denied",
            source_agent_id=str(row.proposal_json.get("proposing_agent_id", "")) or None,
            partition_key=row.partition_key,
            inputs={"hitl_id": str(row.hitl_id), "trigger_reason": row.trigger_reason},
            result_reason="; ".join(reasons),
        )
        return row

    # -- internals -----------------------------------------------------------

    async def _raise_refusal(
        self, hitl_id: UUID, org_id: str, *, now: datetime, for_approve: bool
    ) -> None:
        """The claim predicate did not match — re-read the row and raise the
        matching typed error. Always raises."""
        current = await self._repo.get(hitl_id, org_id)
        if current is None:
            raise HitlNotFound(f"no pending hitl item {hitl_id}")
        if current.status != "pending":
            raise HitlAlreadyActioned(current.status)
        if current.expires_at is not None and current.expires_at <= now:
            raise HitlExpired(f"hitl item {hitl_id} expired at {current.expires_at.isoformat()}")
        if for_approve and current.request_json is None:
            raise HitlNotReplayable(
                f"hitl item {hitl_id} carries no replayable request_json"
            )
        # Lost a race: someone claimed between our UPDATE and this read.
        raise HitlAlreadyActioned(current.status)

    async def _release_failed(
        self,
        row: HitlQueueItem,
        org_id: str,
        correlation_id: UUID,
        *,
        action_type: str,
        reason: str,
        source_agent_id: str | None = None,
    ) -> None:
        await self._repo.release(row.hitl_id, org_id, from_status="approved")
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            causation_id=row.correlation_id,
            action_type=action_type,
            result="failed",
            source_agent_id=source_agent_id,
            partition_key=row.partition_key,
            inputs={"hitl_id": str(row.hitl_id)},
            result_reason=reason,
        )

    @staticmethod
    def _decision_id(row: HitlQueueItem) -> UUID | None:
        """The parent decision's id — from the row's FK, else re-derived with
        the existing deterministic decision_id_for (uuid5 of the proposal id;
        the same single derivation, not a second mint)."""
        if row.decision_id is not None:
            return row.decision_id
        proposal_id = row.proposal_json.get("proposal_id")
        if not proposal_id:
            return None
        try:
            return decision_id_for(UUID(str(proposal_id)))
        except ValueError:
            return None

    @staticmethod
    def _action_kind(row: HitlQueueItem) -> str:
        return str(row.proposal_json.get("action_kind", "unknown"))
