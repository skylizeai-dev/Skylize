"""The two trigger shapes. Both are thin; the run itself is `runner.py`.

Owner decision (2026-09-13) #2: BOTH scheduled (cron-like, recurring work) AND
event-driven (reactive work). Which one an agent uses is a property of that
agent, not a platform-wide choice.

They are both here, next to each other, because their whole point is that they
are interchangeable at the seam: each builds an `AutonomousRunRequest`, calls
`AutonomousRunService.run`, and returns its outcome. Neither decides anything
about governance, attribution, money, or attention. Moving the pilot agent from
the scheduled shape to the event shape is a change of which function a process
calls -- nothing downstream notices.

Every entry point calls `assert_pilot_agent` first. The hard exit gate ("build
and prove this for ONE agent, do not wire all 23") is enforced at the door of
the mechanism, not by the absence of configuration somewhere else.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from .pilot import assert_pilot_agent, pilot_input
from .runner import AutonomousRunOutcome, AutonomousRunRequest, AutonomousRunService

log = logging.getLogger(__name__)

#: `trigger` provenance strings recorded on the journal row's `detail`. The
#: prefix says which shape fired; the suffix says which instance of it.
TRIGGER_SCHEDULE = "schedule"
TRIGGER_EVENT = "event"


async def run_scheduled(
    service: AutonomousRunService,
    *,
    org_id: str,
    agent_id: str,
    schedule_id: str,
    correlation_id: UUID | None = None,
    input_data: dict[str, Any] | None = None,
) -> AutonomousRunOutcome:
    """The cron-like shape: a cadence fired, run the agent's standing work.

    `input_data` defaults to the pilot's declared sweep descriptor
    (`pilot.pilot_input`) -- see pilot.py for the honest account of why that is a
    descriptor and not harvested signals.

    `correlation_id` SHOULD be supplied by a transport that already owns a
    durable, retry-stable id (a Temporal workflow id derived from the scheduled
    time). Passing the transport's id makes a retried delivery reuse one
    correlation, so the journal, audit trail and cost ledger agree on how many
    runs actually happened.
    """
    assert_pilot_agent(agent_id)
    request = AutonomousRunRequest(
        org_id=org_id,
        agent_id=agent_id,
        input_data=input_data if input_data is not None else pilot_input(),
        trigger=f"{TRIGGER_SCHEDULE}:{schedule_id}",
        **({"correlation_id": correlation_id} if correlation_id is not None else {}),
    )
    outcome = await service.run(request)
    log.info(
        "autonomous_scheduled_run",
        extra={
            "org_id": org_id,
            "agent_id": agent_id,
            "schedule_id": schedule_id,
            "status": outcome.status,
            "requires_attention": outcome.requires_attention,
            "correlation_id": str(outcome.correlation_id),
            "journal_seq": outcome.journal_seq,
        },
    )
    return outcome


async def run_on_event(
    service: AutonomousRunService,
    *,
    org_id: str,
    agent_id: str,
    event_name: str,
    input_data: dict[str, Any],
    correlation_id: UUID | None = None,
) -> AutonomousRunOutcome:
    """The reactive shape: something happened, evaluate it.

    Unlike the scheduled shape this has NO default input -- an event-driven run
    exists because a specific thing happened, and a reactive agent invoked
    against a placeholder payload would produce a confident verdict about
    nothing. The caller supplies the payload it observed or there is no run.

    Deliberately transport-free: it takes the already-decoded payload rather than
    a `DeliveredEvent`, so the bus adapter keeps ownership of decode / ack / DLQ
    (events/bus.py) and this stays unit-testable with no Redis. The subscriber
    that wires a stream to this is a separate, per-agent decision and is NOT part
    of the pilot -- the pilot agent runs on the scheduled shape.
    """
    assert_pilot_agent(agent_id)
    request = AutonomousRunRequest(
        org_id=org_id,
        agent_id=agent_id,
        input_data=input_data,
        trigger=f"{TRIGGER_EVENT}:{event_name}",
        **({"correlation_id": correlation_id} if correlation_id is not None else {}),
    )
    outcome = await service.run(request)
    log.info(
        "autonomous_event_run",
        extra={
            "org_id": org_id,
            "agent_id": agent_id,
            "event_name": event_name,
            "status": outcome.status,
            "requires_attention": outcome.requires_attention,
            "correlation_id": str(outcome.correlation_id),
            "journal_seq": outcome.journal_seq,
        },
    )
    return outcome
