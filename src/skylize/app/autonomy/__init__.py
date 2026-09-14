"""Autonomous agent runs — the execution path with no human in the request.

PILOT SCOPE (owner decision, 2026-09-13). The mechanism here is generic across
all 23 live contracts; exactly ONE agent is wired to a trigger
(`pilot.PILOT_AGENT_ID`). Enabling the rest is a separate, deliberate step.

Layering, outermost last:

    principal.py   agent contract -> the human at the end of `escalation_path`
    attention.py   contract + output -> did this run hit a HITL boundary?
    runner.py      the transport-neutral run: govern, execute, journal, fail safe
    pilot.py       which agent is triggered, on what cadence, with what input
    triggers.py    the two trigger shapes, both of which just call the runner

`app/orchestrator/temporal/autonomous.py` holds the Temporal workflow/activity
binding; it is a separate module because it may only be imported where
`temporalio` is expected.
"""

from __future__ import annotations

from .errors import ContractNotAutonomous, PrincipalUnresolvable
from .principal import HUMAN_OWNER, OWNER_ROLE, AutonomousPrincipalResolver
from .runner import (
    AutonomousRunOutcome,
    AutonomousRunRequest,
    AutonomousRunService,
    RunStatus,
)

__all__ = [
    "AutonomousPrincipalResolver",
    "AutonomousRunOutcome",
    "AutonomousRunRequest",
    "AutonomousRunService",
    "ContractNotAutonomous",
    "HUMAN_OWNER",
    "OWNER_ROLE",
    "PrincipalUnresolvable",
    "RunStatus",
]
