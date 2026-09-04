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

WHY THIS IS NOT CALLED FROM INSIDE ToolProxy - AN ARCHITECTURAL CONSTRAINT, NOT
A PREFERENCE
-------------------------------------------------------------------------------
The obvious place to hook a ceiling breach is where one is detected:
`ToolProxy._reserve_spend`, which raises `ToolSpendHardDenied` /
`ToolSpendDeferredToHuman`. It cannot go there. `AgentExecutionService` is
constructed WITH the `ToolProxy` (bootstrap.py:774 builds the proxy, :796 passes
it to the service), so a proxy that called back into the execution service would
close a construction cycle. Proposing from inside the proxy would also mean an
agent's own denied tool call synchronously spawns a second agent run inside the
first one's request, which is a re-entrancy hazard on the request path.

So this service is a SEPARATE, EXPLICITLY-INVOKED seam. Its caller is an operator
action or a worker that observes the breach - not the request path that produced
it. That boundary is the honest one, and it keeps a containment proposal from
ever being a side effect of a tool call the customer was already making.

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
  * proposed                            -> a HITL row now exists

The second is the one worth dwelling on. The probe (23d339c) exists so that a
broken trust is known BEFORE it is needed; checking it here means an operator
learns "your federation is misconfigured" while looking at the overspend, rather
than discovering it after approving a containment that then fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from ...dal.gcp_wif import GcpWifRepository

log = logging.getLogger("skylize.gcp.trigger")

#: The contract that carries a containment through the gate. Named here rather
#: than passed in: only this contract holds the tool, and letting a caller choose
#: the executor would be letting a caller choose how much authority to use.
EXECUTOR_AGENT_ID = "infrastructure_executor"

ProposalStatus = Literal[
    "proposed",
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
    ) -> None:
        self._wif_repo = wif_repo
        self._execution = execution

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
        return ContainmentProposal(
            status="execution_unavailable",
            detail=(
                "containment executed without deferring to a human, which the "
                f"{EXECUTOR_AGENT_ID} contract's human-in-loop trigger is meant to "
                "prevent. Treating as a configuration defect; verify the contract's "
                "human_in_loop_triggers."
            ),
        )
