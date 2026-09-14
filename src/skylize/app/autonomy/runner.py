"""The autonomous run: agent intent -> governed execution -> a line in a human's brief.

WHAT THIS IS
------------
The one place an agent run happens with NO human in the request path. It is
deliberately TRANSPORT-NEUTRAL: it knows nothing about Temporal, cron, Redis
streams or HTTP. A trigger -- any trigger -- hands it an
`AutonomousRunRequest` and it returns an `AutonomousRunOutcome`.

That split is not tidiness. CI's `integration` job provides Postgres and Redis
and NO Temporal server (.github/workflows/ci.yml:124-148), so a design that put
this logic inside a workflow definition could not be proven by any test CI
actually runs. Everything that can be wrong lives here, where a real database
can prove it; the Temporal binding above it is glue thin enough to read.

THE FOUR TERMINAL STATES, AND WHY EACH WRITES A ROW
---------------------------------------------------
An autonomous run has no caller watching a response code. If a state can end a
run, it must end it VISIBLY -- in the journal, which is what
`GET /api/v1/me/brief` reads:

  completed  -> journal row, `requires_attention` decided by the contract's own
                boundaries (attention.py). Lands in `done_while_away`.
  deferred   -> the decision gate deferred to a human and a `hitl_queue` row is
                already durable. Journal row carries the `hitl_id`,
                `requires_attention=True`. Lands in `needs_attention`.
  rejected   -> the decision gate rejected it. Nothing executed. Journal row,
                `requires_attention=True` -- a scheduled job that is being refused
                every night is exactly what a human must be told.
  failed     -> anything else raised: bad input, malformed model output, provider
                outage, budget/authority denial, timeout. Journal row,
                `requires_attention=True`.

The ONLY state with no journal row is an unresolvable principal, and it is
unwritable rather than unwritten: `work_journal` rows are principal-scoped, so
with no principal there is no row to write and no brief to write it into. That
path raises `PrincipalUnresolvable` and is logged at ERROR. It is the one case a
trigger must surface itself.

WHY THE PRINCIPAL IS RESOLVED BEFORE DISPATCH
---------------------------------------------
Resolution is the FIRST thing this does, before the contract is dispatched and
before a single token is spent. Resolving at completion instead would mean
discovering that a run is unattributable only after paying for it, and leaving
its cost stranded on a ledger row no brief will ever show.

MONEY (ADR-0006)
----------------
`cost_minor` is read back out of `ai_cost_ledger` -- the ledger whose stated job
is "money value of consumed LLM tokens" -- and never recomputed here. The run
owns its `correlation_id`, every ledger row the run writes carries it, so the
sum is the run's true spend on BOTH the success and failure paths. Micro ->
minor conversion happens exactly once, on that aggregate, via the ledger's own
`micros_to_minor`. Two ledgers are deliberately NOT touched: `run_ledger` counts
tokens, and `budget_ledger` is business spend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, Sequence
from uuid import UUID, uuid4

from ...contracts.registry import AgentNotRegistered, AgentRegistry
from ..agents.execution import (
    AgentDeferredToHuman,
    AgentExecutionService,
    AgentGovernanceRejected,
)
from ..principal.journal import WorkJournal
from ..principal.models import ActorKind
from .attention import evaluate_attention
from .errors import PrincipalUnresolvable
from .principal import AutonomousPrincipalResolver

log = logging.getLogger(__name__)

RunStatus = Literal["completed", "deferred", "rejected", "failed"]

#: `work_journal.kind` vocabulary for this path. Free text at the schema level
#: (migration 0019), so the discipline has to be here: one stable string per
#: terminal state, namespaced to the path that writes them, so a brief renderer
#: or a query can group them without string-matching headlines.
KIND_COMPLETED = "autonomous.run_completed"
KIND_DEFERRED = "autonomous.run_deferred_to_human"
KIND_REJECTED = "autonomous.run_rejected"
KIND_FAILED = "autonomous.run_failed"

#: `headline` is CHECK (length BETWEEN 1 AND 280) in migration 0019. Truncated
#: here rather than allowed to raise: a journal row that fails to insert because
#: an error message was long is the exact silent-drop this module exists to stop.
_HEADLINE_MAX = 280


def _headline(text: str) -> str:
    collapsed = " ".join(text.split()) or "(no detail)"
    if len(collapsed) <= _HEADLINE_MAX:
        return collapsed
    return collapsed[: _HEADLINE_MAX - 1] + "…"


class RunCostSource(Protocol):
    """The money read-back. Satisfied by `dal.cost_ledger.CostLedgerDAL`.

    A Protocol rather than the concrete DAL for two reasons. Architecturally, it
    keeps `skylize.app` depending on a port instead of a concrete -- and here that
    is ENFORCED, not merely preferred: the import-linter contract "Application
    logic contains no SQL" fails on `skylize.app -> skylize.dal.cost_ledger`,
    because that module reaches `dal.connection` and so asyncpg. Practically, the
    memory backend supplies nothing at all rather than a null-object DAL.

    It asks for MINOR units, not micros, so every piece of money arithmetic --
    including ADR-0006's "only rounding to cents in the money path" -- stays
    inside the ledger. This layer transports a number it never computes.
    """

    async def run_total_minor(self, org_id: str, correlation_id: UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class AutonomousRunRequest:
    """One triggered run. Constructed by a trigger, never by a request handler."""

    org_id: str
    agent_id: str
    input_data: dict[str, Any]
    #: Provenance of the trigger, recorded on the journal row so a human reading
    #: their brief can tell WHY this happened -- "the 06:00 sweep" vs "a signal
    #: arrived". Free text by design; the trigger names itself.
    trigger: str
    #: Supplied by the caller when the transport already has a durable id worth
    #: joining on (a Temporal workflow id). Minted here otherwise.
    correlation_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class AutonomousRunOutcome:
    """What happened, in a form a trigger can log, assert on, or retry against."""

    status: RunStatus
    org_id: str
    agent_id: str
    principal_id: str
    correlation_id: UUID
    requires_attention: bool
    cost_minor: int
    journal_seq: int | None
    reasons: Sequence[str] = ()
    deliverable_id: UUID | None = None
    hitl_id: UUID | None = None


class AutonomousRunService:
    """Executes a triggered agent run and records it in the owner's work journal.

    GENERIC. Nothing here names the pilot agent: the contract supplies the
    boundaries, the resolver supplies the human, the ledger supplies the money.
    The pilot restriction is enforced by WHAT GETS TRIGGERED, not by this class.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        execution: AgentExecutionService,
        resolver: AutonomousPrincipalResolver,
        journal: WorkJournal,
        cost_source: RunCostSource | None = None,
    ) -> None:
        self._registry = registry
        self._execution = execution
        self._resolver = resolver
        self._journal = journal
        # None on the memory backend, where there is no ai_cost_ledger. Cost then
        # reads 0, which is the truth on a backend that prices nothing -- not a
        # missing value dressed up as zero.
        self._cost_source = cost_source

    async def run(self, request: AutonomousRunRequest) -> AutonomousRunOutcome:
        """Execute one triggered run to a terminal, journalled state.

        Raises ONLY `PrincipalUnresolvable` / `ContractNotAutonomous` -- the two
        conditions under which no journal row is possible. Every other failure is
        caught and becomes a `failed` outcome with a row.
        """
        contract = self._registry.resolve(request.agent_id)  # AgentNotRegistered
        # BEFORE dispatch. See the module docstring.
        principal_id = await self._resolver.resolve(
            org_id=request.org_id, contract=contract
        )

        captured: list[Any] = []
        status: RunStatus
        reasons: list[str] = []
        deliverable_id: UUID | None = None
        hitl_id: UUID | None = None
        requires_attention: bool

        try:
            row = await self._execution.execute(
                org_id=request.org_id,
                agent_id=request.agent_id,
                input_data=request.input_data,
                # The journal's `principal_id` and the token's human claim are the
                # SAME id. `user_id` is the audit trail's actor; for a run nobody
                # asked for, the accountable human is that actor.
                user_id=principal_id,
                on_behalf_of_principal=principal_id,
                correlation_id=request.correlation_id,
                # The first producer of this value in the codebase. It is what
                # makes the token say "bound to a human who was NOT present",
                # mirroring ActorKind.AGENT_AUTONOMOUS on the row below.
                session_kind="autonomous",
                output_sink=captured.append,
            )
        except AgentDeferredToHuman as exc:
            status, hitl_id = "deferred", exc.hitl_id
            requires_attention = True
            reasons = [f"deferred_to_human: {exc.reason}"]
        except AgentGovernanceRejected as exc:
            status = "rejected"
            requires_attention = True
            reasons = [f"governance_rejected: {exc}"]
        except Exception as exc:  # noqa: BLE001 - deliberately total; see below
            # TOTAL ON PURPOSE. The alternative is an enumerated except list that
            # silently stops covering a failure mode the day a new one is added
            # upstream -- and the failure mode of THAT is a scheduled run that
            # vanishes. Everything unexpected becomes a visible `failed` row
            # naming the exception type.
            status = "failed"
            requires_attention = True
            reasons = [f"{type(exc).__name__}: {exc}"]
            log.error(
                "autonomous_run_failed",
                extra={
                    "org_id": request.org_id,
                    "agent_id": request.agent_id,
                    "correlation_id": str(request.correlation_id),
                    "principal_id": principal_id,
                    "trigger": request.trigger,
                },
                exc_info=True,
            )
        else:
            status = "completed"
            deliverable_id = row.id
            # The contract decides which boundaries exist; the output decides
            # whether one was reached. Neither is decided here.
            output = captured[0] if captured else None
            requires_attention, attention_reasons = evaluate_attention(contract, output)
            reasons = list(attention_reasons)

        cost_minor = await self._cost_minor(request.org_id, request.correlation_id)
        seq = await self._append_journal(
            request=request,
            principal_id=principal_id,
            status=status,
            requires_attention=requires_attention,
            reasons=reasons,
            cost_minor=cost_minor,
            deliverable_id=deliverable_id,
            hitl_id=hitl_id,
        )
        return AutonomousRunOutcome(
            status=status,
            org_id=request.org_id,
            agent_id=request.agent_id,
            principal_id=principal_id,
            correlation_id=request.correlation_id,
            requires_attention=requires_attention,
            cost_minor=cost_minor,
            journal_seq=seq,
            reasons=reasons,
            deliverable_id=deliverable_id,
            hitl_id=hitl_id,
        )

    async def _cost_minor(self, org_id: str, correlation_id: UUID) -> int:
        """This run's real spend in minor units, or 0 when nothing priced it.

        Never raises: a cost read that fails must not turn a completed run into a
        failed one, nor lose its journal row. It degrades to 0 and says so in the
        log, because an under-reported cost is recoverable from the ledger and a
        dropped journal row is not.
        """
        if self._cost_source is None:
            return 0
        try:
            minor = await self._cost_source.run_total_minor(org_id, correlation_id)
        except Exception:  # noqa: BLE001
            log.error(
                "autonomous_run_cost_readback_failed",
                extra={"org_id": org_id, "correlation_id": str(correlation_id)},
                exc_info=True,
            )
            return 0
        # A reversal-heavy run can net negative, and `cost_minor` is
        # CHECK (cost_minor >= 0) in migration 0019. Clamp rather than fail the
        # insert: the ledger keeps the signed truth, and losing the journal row
        # over a sign would be the silent drop this module exists to prevent.
        return max(0, minor)

    async def _append_journal(
        self,
        *,
        request: AutonomousRunRequest,
        principal_id: str,
        status: RunStatus,
        requires_attention: bool,
        reasons: Sequence[str],
        cost_minor: int,
        deliverable_id: UUID | None,
        hitl_id: UUID | None,
    ) -> int | None:
        """Write the run's one row. Returns the assigned `seq`, or None if the
        write itself failed.

        A failure here is logged at ERROR and swallowed -- matching the existing
        journal writers (app/hitl/service.py:449-461, edge/routes/cowork.py) --
        because by this point the run has already happened and the deliverable is
        the system of record. Re-raising would turn a lost journal LINE into a
        lost run, and would make the Temporal activity retry a run that already
        spent money.
        """
        kind = {
            "completed": KIND_COMPLETED,
            "deferred": KIND_DEFERRED,
            "rejected": KIND_REJECTED,
            "failed": KIND_FAILED,
        }[status]
        detail: dict[str, object] = {
            "status": status,
            "trigger": request.trigger,
            "agent_id": request.agent_id,
            "session_kind": "autonomous",
        }
        if reasons:
            detail["reasons"] = list(reasons)
        if deliverable_id is not None:
            detail["deliverable_id"] = str(deliverable_id)
        if hitl_id is not None:
            detail["hitl_id"] = str(hitl_id)

        try:
            return await self._journal.record(
                org_id=request.org_id,
                principal_id=principal_id,
                actor_kind=ActorKind.AGENT_AUTONOMOUS,
                actor_id=request.agent_id,
                correlation_id=request.correlation_id,
                kind=kind,
                headline=_headline(
                    _summarize(request.agent_id, status, reasons)
                ),
                detail=detail,
                cost_minor=cost_minor,
                requires_attention=requires_attention,
                occurred_at=datetime.now(timezone.utc),
            )
        except Exception:  # noqa: BLE001
            log.error(
                "autonomous_run_journal_write_failed",
                extra={
                    "org_id": request.org_id,
                    "agent_id": request.agent_id,
                    "principal_id": principal_id,
                    "correlation_id": str(request.correlation_id),
                    "status": status,
                },
                exc_info=True,
            )
            return None


def _summarize(agent_id: str, status: RunStatus, reasons: Sequence[str]) -> str:
    """The one line a human reads in their brief."""
    if status == "completed" and not reasons:
        return f"{agent_id} completed a scheduled run"
    if status == "completed":
        return f"{agent_id} needs review: {'; '.join(reasons)}"
    if status == "deferred":
        return f"{agent_id} paused for your approval: {'; '.join(reasons)}"
    if status == "rejected":
        return f"{agent_id} was blocked by governance: {'; '.join(reasons)}"
    return f"{agent_id} failed: {'; '.join(reasons)}"


__all__ = [
    "AgentNotRegistered",
    "AutonomousRunOutcome",
    "AutonomousRunRequest",
    "AutonomousRunService",
    "PrincipalUnresolvable",
    "RunCostSource",
    "RunStatus",
]
