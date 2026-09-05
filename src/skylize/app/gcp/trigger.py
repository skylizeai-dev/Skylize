"""Turning a spend-ceiling breach into a governed containment PROPOSAL.

THE INVERSION THIS MODULE IMPLEMENTS (owner decision, Q2.2c)
-------------------------------------------------------------
A T4-class hard deny has no side-effect channel anywhere in the Decision Engine:
`rejected` terminates a proposal and cannot cause anything to happen
(app/agents/execution.py:85-86, :596). So the containment is NOT a consequence of
a denial. It is ITSELF a proposal, and it goes through the same gate every other
governed action goes through:

    ceiling breach observed
        -> this module builds an agent.execute request for `infrastructure_executor`
        -> AgentExecutionService.execute()  (the existing synchronous gate)
        -> evaluator sees FIRST_EXTERNAL_LAUNCH on the contract -> deferred_to_human
        -> hitl_queue row written BEFORE the terminal event (D3 ordering)
        -> a human approves -> HitlQueueService.approve replays the SAME request
        -> governance token minted -> ToolProxy gates -> the VM is stopped

No fifth mechanism. Every step above already existed.

HOW IT IS INVOKED - AUTOMATICALLY, AND WHY THE WIRING LOOKS THE WAY IT DOES
---------------------------------------------------------------------------
`ToolProxy._reserve_spend` calls this on every `CeilingExceeded`. Enforcement
that depends on someone remembering to call it is not enforcement, so the hook is
not optional (owner decision). Two things make that safe, and both are
deliberate:

**The construction cycle is broken by LATE BINDING, not by reordering bootstrap.**
The dependency runs `gcp_containment -> agent_execution -> tool_proxy`
(bootstrap.py builds them in that reverse order), so giving the proxy this
service through its CONSTRUCTOR would close a three-node cycle that no ordering
can satisfy. Instead the proxy is built without it and
`ToolProxy.set_containment_trigger(...)` is called once the trigger exists.
Nothing else in bootstrap moves, so no other connector's construction order is
touched. There is no import cycle to break: this module imports
`AgentExecutionService` lazily, inside `propose_containment`.

**The call is scheduled, not awaited.** The proxy hands this off as a background
task and immediately raises the spend denial the caller is owed. Awaiting it
would put a WIF read, a full agent execution, a `hitl_queue` write and an event
publish inside the latency of a call that has already failed - and would run a
second agent run inside the first one's request, which is a re-entrancy hazard.
A containment proposal that appears a second late is fine; a spend denial that
blocks on one is not.

The explicit entry point survives unchanged for operators and for tests: this is
still an ordinary method anyone may call.

DUPLICATE SUPPRESSION IS A DURABLE, CROSS-REPLICA CLAIM - NOT AN IN-PROCESS TIMER
----------------------------------------------------------------------------------
A runaway agent breaches its ceiling on EVERY subsequent tool call, so without
suppression the auto-hook would queue one "stop this VM" approval per breach -
burying the reviewer in duplicates at exactly the moment they need to act fast.
An earlier version of this module suppressed that with a Python dict keyed by
`(org_id, label)`, timed out after a fixed window. That dict lives in ONE
process: two replicas each observing a breach for the same org inside the window
each saw "no proposal pending" and each queued one. Bounded, but not correct
under horizontal scaling.

`GcpContainmentClaimRepository` (dal/gcp_containment.py, migration 0025) replaces
it with a row in Postgres, claimed via `INSERT ... ON CONFLICT (org_id, label)`
BEFORE `execute()` is ever called. Of two replicas racing the same breach,
Postgres itself resolves the conflicting write to exactly one winner - "the
guarantee lives in SQL, not in Python", the same reasoning `spend_reservation`'s
unique constraint already rests on (app/principal/spend.py's `_RESERVE_SQL`
docstring). The loser never calls `execute()` at all, so there is no duplicate
`hitl_queue` row to reject, not merely a duplicate the reviewer is spared from
seeing.

The claim releases itself the moment a human decides - not after a fixed
timeout - because staleness is checked by JOINING the claimed `hitl_id` against
`hitl_queue.status` (migration 0025's docstring has the exact predicate). A
ten-second decision unblocks the org in ten seconds; nothing here waits out an
arbitrary window once the real signal is available.

WHAT IT REFUSES TO PROPOSE, AND WHY EACH REFUSAL IS ITS OWN OUTCOME
--------------------------------------------------------------------
`propose_containment` returns a typed outcome rather than raising, because "no
proposal was made" has several very different meanings and an operator staring at
an overspend needs to know which one:

  * no GCP federation configured        -> nothing to contain through
  * federation present but not 'valid'  -> the health probe already found it
                                           broken; proposing would queue an
                                           approval that could only fail at the
                                           moment a human clicks it
  * no enabled targets                  -> the customer federated a project but
                                           listed no machine as stoppable
  * a containment is already pending    -> the durable claim above; nothing new
                                           is queued
  * proposed                            -> a HITL row now exists

The second is the one worth dwelling on. The probe (23d339c) exists so that a
broken trust is known BEFORE it is needed; checking it here means an operator
learns "your federation is misconfigured" while looking at the overspend, rather
than discovering it after approving a containment that then fails.

THE WIF CHECKS RUN BEFORE THE CLAIM ATTEMPT, DELIBERATELY. A breach that finds no
connection, a broken trust, or no targets never claims the slot at all - those
are conditions an operator may fix within any window, and the very next breach
must be free to propose once they have. Claiming, then releasing on those same
early exits, would work too, but would briefly (and pointlessly) contend the row
against a concurrent breach that might otherwise have gone on to claim and
succeed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from ...dal.gcp_containment import GcpContainmentClaimRepository
from ...dal.gcp_wif import GcpWifRepository

log = logging.getLogger("skylize.gcp.trigger")

#: The contract that carries a containment through the gate. Named here rather
#: than passed in: only this contract holds the tool, and letting a caller choose
#: the executor would be letting a caller choose how much authority to use.
EXECUTOR_AGENT_ID = "infrastructure_executor"

#: Crash-recovery BACKSTOP ONLY, not the steady-state suppression mechanism. A
#: claim with no recorded `hitl_id` means the claimant died between reserving
#: the slot and learning the ticket `execute()` produced (or never producing
#: one) - the ONLY case a stuck slot can arise, since every other exit path
#: (`AgentDeferredToHuman`, any other exception) explicitly records or releases
#: before returning. Two minutes is generous against how long `execute()`
#: normally takes and tight enough that a crash cannot wedge an org for long.
CLAIM_CRASH_TIMEOUT = timedelta(minutes=2)

ProposalStatus = Literal[
    "proposed",
    "already_proposed",
    "no_connection",
    "trust_not_valid",
    "no_targets",
    "execution_unavailable",
]


@dataclass(frozen=True, slots=True)
class ContainmentProposal:
    """What the trigger did, and enough detail to act on it."""

    status: ProposalStatus
    detail: str
    #: Set only when `status == "proposed"`. The HITL ticket a human must action.
    hitl_id: UUID | None = None
    #: The target the proposal names, when one was chosen.
    project: str | None = None
    zone: str | None = None
    instance: str | None = None

    @property
    def proposed(self) -> bool:
        return self.status == "proposed"


class SpendCeilingContainmentTrigger:
    """Proposes a GCP containment when an org's spend ceiling has been breached.

    ATTACHES TO THE EXISTING CEILING, inventing no new spend concept. The breach
    signal is whatever the caller already holds: `CeilingExceeded` from
    `SpendLedger.reserve` (app/principal/spend.py:154-159), or the
    `ToolSpendHardDenied` / `ToolSpendDeferredToHuman` the ToolProxy raises from
    it (tools/base.py). This module does not read a ceiling, define a threshold,
    or hold any notion of "how much is too much" - all of that already lives in
    `spend_envelope` and ADR-0006 forbids duplicating it.
    """

    def __init__(
        self,
        *,
        wif_repo: GcpWifRepository,
        execution: Any,
        claims: GcpContainmentClaimRepository,
        crash_timeout: timedelta = CLAIM_CRASH_TIMEOUT,
    ) -> None:
        self._wif_repo = wif_repo
        self._execution = execution
        self._claims = claims
        self._crash_timeout = crash_timeout

    async def propose_containment(
        self,
        *,
        org_id: str,
        breach_reason: str,
        user_id: str,
        label: str = "",
    ) -> ContainmentProposal:
        """Build and submit the containment proposal for `org_id`.

        Returns without proposing when there is nothing safe to propose. Never
        stops anything itself: the return value's best case is a QUEUED HUMAN
        DECISION, never a completed action.
        """
        row = await self._wif_repo.get(org_id, label)
        if row is None:
            return ContainmentProposal(
                status="no_connection",
                detail=(
                    f"org {org_id!r} has no GCP federation configured, so there is "
                    "no infrastructure this platform can contain"
                ),
            )

        if row.connection_state != "valid":
            # The probe already knows. Say so now rather than queueing an
            # approval whose only possible outcome is a failure at click time.
            return ContainmentProposal(
                status="trust_not_valid",
                detail=(
                    f"GCP federation for org {org_id!r} is {row.connection_state!r} "
                    f"and cannot be used: {row.state_reason or 'no reason recorded'}. "
                    "Fix the federation before a containment can be proposed."
                ),
            )

        targets = await self._wif_repo.list_targets(
            org_id, row.conn_id, enabled_only=True
        )
        if not targets:
            return ContainmentProposal(
                status="no_targets",
                detail=(
                    f"org {org_id!r} has a valid GCP federation but no enabled "
                    "target instances; nothing is authorised to be stopped"
                ),
            )

        # THE ATOMIC GATE. Of any number of concurrent callers reaching this
        # line for the same (org_id, label), Postgres's own conflict resolution
        # on the PRIMARY KEY guarantees at most one gets True - see
        # dal/gcp_containment.py and migration 0025.
        stale_before = datetime.now(timezone.utc) - self._crash_timeout
        claimed = await self._claims.try_claim(
            org_id=org_id, label=label, stale_before=stale_before,
        )
        if not claimed:
            return ContainmentProposal(
                status="already_proposed",
                detail=(
                    f"a containment for org {org_id!r} is already pending a human "
                    "decision; not queueing a duplicate"
                ),
            )

        # One target per proposal, deliberately. A human approving a containment
        # must be approving a NAMED machine, not a batch whose membership was
        # decided by this code. Multiple machines mean multiple proposals and
        # multiple approvals.
        target = targets[0]

        from ..agents.execution import AgentDeferredToHuman

        try:
            await self._execution.execute(
                org_id=org_id,
                agent_id=EXECUTOR_AGENT_ID,
                input_data={
                    "project": target.gcp_project_id,
                    "zone": target.zone,
                    "instance": target.instance_name,
                    "reason": breach_reason,
                },
                user_id=user_id,
            )
        except AgentDeferredToHuman as deferred:
            # THE EXPECTED PATH. The executor contract carries
            # FIRST_EXTERNAL_LAUNCH, so the gate defers every time and the
            # hitl_queue row is already durable when this is raised.
            #
            # Recording the ticket is what lets a LATER breach's staleness check
            # find it and release the slot the moment a human decides, rather
            # than waiting out the crash backstop.
            await self._claims.record_hitl_id(
                org_id=org_id, label=label, hitl_id=deferred.hitl_id,
            )
            log.warning(
                "gcp.containment_proposed",
                extra={
                    "org_id": org_id,
                    "hitl_id": str(deferred.hitl_id),
                    "project": target.gcp_project_id,
                    "zone": target.zone,
                    "instance": target.instance_name,
                },
            )
            return ContainmentProposal(
                status="proposed",
                detail=(
                    f"containment of {target.gcp_project_id}/{target.zone}/"
                    f"{target.instance_name} is awaiting human approval: "
                    f"{deferred.reason}"
                ),
                hitl_id=deferred.hitl_id,
                project=target.gcp_project_id,
                zone=target.zone,
                instance=target.instance_name,
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else - governance denial, database unavailable, an agent
            # registry that does not hold the executor. Reported, never retried
            # here: a containment that silently retries is a containment nobody
            # is supervising.
            #
            # Released IMMEDIATELY rather than left for the crash backstop to
            # expire: this was not a crash, it was a clean failure, and the next
            # breach should be free to try again as soon as whatever broke is
            # fixed - not stuck behind a timer measuring a crash that never
            # happened.
            await self._claims.release(org_id=org_id, label=label)
            log.error(
                "gcp.containment_proposal_failed",
                extra={"org_id": org_id, "error": type(exc).__name__},
            )
            return ContainmentProposal(
                status="execution_unavailable",
                detail=f"could not submit the containment proposal: {exc}",
            )

        # Reaching here means the gate APPROVED rather than deferring, which the
        # executor contract's FIRST_EXTERNAL_LAUNCH trigger is supposed to make
        # impossible. Treated as a defect rather than a success: this action must
        # never execute without a human, so an unexpected approval is reported
        # instead of being quietly accepted.
        await self._claims.release(org_id=org_id, label=label)
        return ContainmentProposal(
            status="execution_unavailable",
            detail=(
                "containment executed without deferring to a human, which the "
                f"{EXECUTOR_AGENT_ID} contract's human-in-loop trigger is meant to "
                "prevent. Treating as a configuration defect; verify the contract's "
                "human_in_loop_triggers."
            ),
        )
