"""HITL resume — a human verdict drives a deferred decision to its terminal state.

The counterpart to ``orchestrator.DecisionOrchestrator``: that path decides a
proposal and may park it on a human; this path takes the human's answer and
finishes it. They are separate types on purpose. This handler holds no
``EvaluationPipeline`` reference at all, so "a resume never re-runs the six
stages" is a structural property of the wiring rather than a rule someone has to
remember — re-evaluating a decision a person has already ruled on would let
policy silently overturn them (decision_flow.md §3 keeps the human terminal).

The verdict event carries everything needed (``decision_id``, ``hitl_id``,
``approved``, ``decided_by``, ``reason`` — schemas/events/governance.py:150-158),
so no lookup-by-hitl_id is required to reach a terminal outcome. This mirrors the
inline engine's proven ``_resume_from_human`` (app/decision_engine/engine.py:270).

Where it necessarily diverges from the inline engine is durability. The inline
engine publishes straight to the bus; the OPA engine's writes go through
Postgres as the single commit point, and ``DecisionEventPublisher.publish_outcome``
cannot express this transition: its CTE gates the outbox row on the ``decisions``
row being NEWLY inserted (``ON CONFLICT (decision_id) DO NOTHING``,
publisher.py:279-286). A resume targets a ``decisions`` row that already exists
from the deferral, so that INSERT always conflicts and the terminal event would
never be enqueued. Hence the mirrored-but-inverted statement below: UPDATE where
the publisher INSERTs, with the same gated-outbox shape.

Idempotency needs no new mechanism. ``hitl_queue.status`` is the durable state,
and the UPDATE is guarded on ``status = 'pending'`` (the CHECK constraint's
vocabulary, migration 0001:212-214). A redelivered approval updates zero rows,
so the CTE chain yields nothing and no duplicate terminal event is enqueued —
the same "gate the outbox on the row transition actually happening" trick the
publisher already relies on, and the reason ``verdict_by``/``verdict_json``/
``verdict_at`` exist in that table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..schemas.events.decision import DecisionApproved, DecisionRejected
from ..schemas.events.governance import GovernanceHumanApprovalReceived
from .config import DecisionEngineSettings
from .exceptions import DecisionEngineError
from .publisher import _new_outbox_row_id, _strip_none

if TYPE_CHECKING:
    from ..dal.connection import Database

log = logging.getLogger(__name__)

# The terminal decision.* events ride the canonical `decision` channel, exactly
# as publisher._OUTCOME_TO_DEPARTMENT routes an APPROVED/REJECTED proposal
# (publisher.py:70-76). A resume is terminal for the same decision, so it must
# land on the same channel its consumers already watch.
_RESUME_DEPARTMENT = "decision"

# ``action_kind`` marking a decision that a human, not the pipeline, terminated.
# Identical to the inline engine's resume (app/decision_engine/engine.py:286,302)
# so downstream consumers see one vocabulary across both engines.
_RESUME_ACTION_KIND = "human_resumed"

# The stage a human-rejected decision is attributed to. HITL_GATE is the stage
# that deferred it, so it is also the stage the rejection resolves.
_RESUME_REJECT_STAGE = "hitl_gate"


class HITLResumeHandler:
    """Applies a human verdict to the decision it was deferred from."""

    def __init__(self, db: "Database", settings: DecisionEngineSettings) -> None:
        self._db = db
        self._settings = settings

    async def resume(self, event: GovernanceHumanApprovalReceived) -> bool:
        """Drive one deferred decision to its terminal outcome.

        Returns True if this call resolved the decision, False if it was already
        resolved (a redelivery). Raising, not swallowing, is deliberate: a lost
        human verdict is worse than a redelivered one, and the router's DLQ at
        least makes the failure visible.

        That now buys real recovery: the shared Redis adapter reclaims un-acked
        PEL entries, so a raise here is retried once the idle window elapses and,
        past the router's budget, routed to the DLQ. A transient failure resolves
        itself; a persistent one becomes visible instead of stranding the verdict
        silently. This handler is idempotent against the redelivery either way —
        the `status = 'pending'` guard in the UPDATE is the durable half.
        """
        payload = event.payload
        outcome = "approved" if payload.approved else "rejected"
        now = datetime.now(timezone.utc)

        outbound = self._build_outbound(event, outcome)
        stream_key = f"evt:{event.tenant_id}:{_RESUME_DEPARTMENT}"
        event_type = f"decision.{outcome}"
        outbox_row_id, outbox_id = _new_outbox_row_id()

        verdict_json = json.dumps(
            {
                "approved": payload.approved,
                "decided_by": payload.decided_by,
                "reason": payload.reason,
                "resume_event_id": str(event.event_id),
            },
            default=str,
        )

        async with self._db.tenant_session(event.tenant_id) as conn:
            enqueued = await conn.fetchval(
                """
                WITH resolved AS (
                    UPDATE hitl_queue
                       SET status = $1,
                           verdict_by = $2,
                           verdict_json = $3::jsonb,
                           verdict_at = $4
                     WHERE hitl_id = $5
                       AND org_id = $6
                       AND status = 'pending'
                    RETURNING decision_id
                ),
                resolved_decision AS (
                    UPDATE decisions
                       SET outcome = $7,
                           outcome_reason = $8,
                           resolved_at = $4
                     WHERE org_id = $6
                       AND decision_id IN (SELECT decision_id FROM resolved)
                    RETURNING decision_id
                )
                INSERT INTO decision_outbox (
                    id, tenant_id, stream_key, event_type, payload, outbox_row_id
                )
                SELECT $9, $6, $10, $11, $12::jsonb, $13
                FROM resolved_decision
                RETURNING id
                """,
                outcome,                       # $1  hitl_queue.status
                payload.decided_by,            # $2
                verdict_json,                  # $3
                now,                           # $4
                payload.hitl_id,               # $5
                event.tenant_id,               # $6
                outcome,                       # $7  decisions.outcome
                self._outcome_reason(payload), # $8
                outbox_id,                     # $9
                stream_key,                    # $10
                event_type,                    # $11
                json.dumps(outbound, default=str),  # $12
                outbox_row_id,                 # $13
            )

        if enqueued is None:
            # Either a redelivery (status already terminal) or a verdict for a
            # hitl_id this tenant has no pending row for. Both are non-fatal and
            # must NOT raise: raising would put a duplicate on an endless retry.
            log.info(
                "decision_resume_noop",
                extra={
                    "hitl_id": str(payload.hitl_id),
                    "decision_id": str(payload.decision_id),
                    "tenant_id": event.tenant_id,
                    "outcome": outcome,
                },
            )
            return False

        log.info(
            "decision_resumed_by_human",
            extra={
                "hitl_id": str(payload.hitl_id),
                "decision_id": str(payload.decision_id),
                "tenant_id": event.tenant_id,
                "outcome": outcome,
                "decided_by": payload.decided_by,
                "event_type": event_type,
                "stream_key": stream_key,
                "outbox_row_id": outbox_row_id,
            },
        )
        return True

    # -- payload ------------------------------------------------------------

    @staticmethod
    def _outcome_reason(payload: GovernanceHumanApprovalReceived.Payload) -> str:
        detail = f": {payload.reason}" if payload.reason else ""
        return f"human_resume by {payload.decided_by}{detail}"

    def _build_outbound(
        self, event: GovernanceHumanApprovalReceived, outcome: str
    ) -> dict:
        """Build and validate the terminal decision.* event.

        Validated before any I/O, like ``publisher.publish_outcome`` — an invalid
        outbound payload raises and nothing is written.
        """
        payload = event.payload
        # partition_key carries the decision id, matching the publisher's
        # convention for terminal events (publisher.py:433).
        partition_key = str(payload.decision_id)
        try:
            if outcome == "approved":
                built = DecisionApproved(
                    tenant_id=event.tenant_id,
                    partition_key=partition_key,
                    department=_RESUME_DEPARTMENT,
                    causation_id=event.event_id,
                    correlation_id=event.correlation_id,
                    payload=DecisionApproved.Payload(
                        decision_id=payload.decision_id,
                        action_kind=_RESUME_ACTION_KIND,
                        approved_scope={
                            "decided_by": payload.decided_by,
                            "resumed": "true",
                            "hitl_id": str(payload.hitl_id),
                        },
                    ),
                )
            else:
                built = DecisionRejected(
                    tenant_id=event.tenant_id,
                    partition_key=partition_key,
                    department=_RESUME_DEPARTMENT,
                    causation_id=event.event_id,
                    correlation_id=event.correlation_id,
                    payload=DecisionRejected.Payload(
                        decision_id=payload.decision_id,
                        action_kind=_RESUME_ACTION_KIND,
                        stage_rejected_at=_RESUME_REJECT_STAGE,
                        reasons=[payload.reason or "human_rejected"],
                        policy_version=None,
                    ),
                )
        except ValidationError as exc:
            log.error(
                "resume_outbound_validation_failed",
                extra={"outcome": outcome, "errors": exc.errors()},
            )
            raise DecisionEngineError(
                f"Resume outbound validation failed for decision.{outcome}: {exc}"
            ) from exc

        return _strip_none(built.model_dump(mode="json"))


def resume_dedup_key(hitl_id: uuid.UUID | str) -> str:
    """The ``ProcessedEventStore`` key for a resume, namespaced off proposals.

    Keyed on ``hitl_id`` — deterministic (uuid5 of the decision_id,
    ``pipeline.hitl_id_for``) and the correlation key the verdict carries — NOT
    on the verdict event's own ``event_id``, which differs between two
    publications of the same human decision. The ``hitl:`` prefix keeps it from
    ever colliding with a proposal's ``event_id`` in the same store, mirroring
    the inline engine's key (app/decision_engine/engine.py:272).
    """
    return f"hitl:{hitl_id}"


__all__ = ["HITLResumeHandler", "resume_dedup_key"]
