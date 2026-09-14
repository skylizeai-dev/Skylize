"""The Temporal binding for autonomous agent runs — the FIRST workflow definition.

WHY TEMPORAL AND NOT A NEW SCHEDULER
------------------------------------
The gap the audits found was real but narrow: `worker.py` registers activities
and no workflows ("No workflow definitions are registered yet", worker.py:19-21),
so nothing was ever *started*. That is not a missing scheduler — Temporal already
ships one (Schedules), and Temporal is the locked stack. Building a cron loop
beside it would mean a second source of truth for "did this run fire", which is
the thing durable execution exists to eliminate. So: one thin workflow, one
Schedule, and the existing worker process.

THE SANDBOX SPLIT, WHICH IS NOT NEGOTIABLE
------------------------------------------
Temporal replays workflow code, so a workflow must be deterministic and may not
do I/O. The agent run is nothing but I/O — LLM calls, Postgres writes. So:

    workflow  -> decides WHAT to run and derives a retry-stable correlation id
    activity  -> actually runs it (app/autonomy/runner.py)

`AutonomousAgentRunWorkflow` therefore contains no database handle, no container,
and no clock read other than Temporal's own.

WHY THE CORRELATION ID IS DERIVED, NOT MINTED
---------------------------------------------
`uuid4()` inside a workflow is non-deterministic and would change on replay. More
importantly, a fresh id per ATTEMPT would make a retried activity look like a
second run: two journal rows in a human's brief for one scheduled sweep, and a
cost sum split across two correlations. So the id is `uuid5` over the workflow id,
which Temporal derives from the schedule and the scheduled time. One scheduled
firing == one correlation id, across every retry of it.

WHAT THIS FILE IS NOT
---------------------
It is not wired into `worker.py`'s registration list, and no Schedule is created
at import time. Turning this on is an explicit operator action
(`scripts/create_autonomous_schedule.py`) against an org that has an owner. New
infrastructure that schedules paid, governed work must not arrive switched on.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

# Imported under `imports_passed_through` so the workflow sandbox does not
# re-import (and re-validate) the whole application tree on every replay. Only
# the activity below touches these; the workflow itself uses none of them.
with workflow.unsafe.imports_passed_through():
    from ....app.autonomy.pilot import PILOT_CRON, assert_pilot_agent
    from ....app.autonomy.runner import AutonomousRunService
    from ....app.autonomy.triggers import run_scheduled

#: Deterministic namespace for `uuid5(NAMESPACE, workflow_id)`. A fixed constant,
#: never regenerated: changing it would silently re-correlate every future run and
#: break the join between a journal row and the ledger rows it priced.
CORRELATION_NAMESPACE = uuid.UUID("6f5f0f2a-9d1a-5a4e-9a2f-0d7a1c3b8e55")

#: Activity name. Pinned as a string constant so a rename of the Python function
#: cannot orphan schedules already registered against the old name.
RUN_AUTONOMOUS_AGENT = "run_autonomous_agent"

#: Workflow type name, pinned for the same reason.
AUTONOMOUS_AGENT_RUN_WORKFLOW = "AutonomousAgentRunWorkflow"


@dataclasses.dataclass
class AutonomousRunInput:
    """What a schedule firing needs to know. Plain dataclass — Temporal serialises it.

    Deliberately carries no input payload: the scheduled shape's input is the
    pilot's declared sweep descriptor, resolved inside the activity from
    `app/autonomy/pilot.py`. Putting it in the Schedule instead would freeze a copy
    of it into Temporal's own store, where it would drift from the code that
    validates it against the agent's input schema.
    """

    org_id: str
    agent_id: str
    schedule_id: str


@dataclasses.dataclass
class AutonomousRunResult:
    """The workflow's return value — what the run did, in a form Temporal can store."""

    status: str
    principal_id: str
    correlation_id: str
    requires_attention: bool
    cost_minor: int
    journal_seq: int | None


def correlation_for(workflow_id: str) -> uuid.UUID:
    """The retry-stable correlation id for one scheduled firing.

    Pure and importable so a test can assert the join key without running a
    workflow, and so an operator can find a run's rows from its workflow id.
    """
    return uuid.uuid5(CORRELATION_NAMESPACE, workflow_id)


class AutonomousActivities:
    """Activity implementations, holding the one dependency they need.

    Mirrors `WorkflowActivities` (activities.py): grouped as instance methods so
    the service is injected once at worker construction rather than reached for
    through a global.
    """

    def __init__(self, service: AutonomousRunService) -> None:
        self._service = service

    @activity.defn(name=RUN_AUTONOMOUS_AGENT)
    async def run_autonomous_agent(
        self, request: AutonomousRunInput
    ) -> AutonomousRunResult:
        """Execute one autonomous run. All the I/O lives here.

        Raises only where a RETRY could plausibly help — an unresolvable principal
        or a non-pilot agent propagate, and the retry policy below is what decides
        whether to try again. Every other failure is already a journalled `failed`
        outcome by the time `run_scheduled` returns, so it must NOT raise: raising
        would make Temporal re-run a job that already spent money and already told
        the human it went wrong.
        """
        assert_pilot_agent(request.agent_id)
        correlation_id = correlation_for(activity.info().workflow_id)
        outcome = await run_scheduled(
            self._service,
            org_id=request.org_id,
            agent_id=request.agent_id,
            schedule_id=request.schedule_id,
            correlation_id=correlation_id,
        )
        return AutonomousRunResult(
            status=outcome.status,
            principal_id=outcome.principal_id,
            correlation_id=str(outcome.correlation_id),
            requires_attention=outcome.requires_attention,
            cost_minor=outcome.cost_minor,
            journal_seq=outcome.journal_seq,
        )


@workflow.defn(name=AUTONOMOUS_AGENT_RUN_WORKFLOW)
class AutonomousAgentRunWorkflow:
    """One scheduled agent run. Deterministic glue and nothing else."""

    @workflow.run
    async def run(self, request: AutonomousRunInput) -> AutonomousRunResult:
        return await workflow.execute_activity(
            RUN_AUTONOMOUS_AGENT,
            request,
            # Generous relative to the pilot contract's
            # `max_execution_time_seconds=90`, so the contract's own budget is what
            # bounds a run and this is only a backstop against a wedged worker.
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=30),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=5),
                # BOUNDED, not infinite. A run that has failed three times is not
                # going to succeed on the fourth, and each attempt can cost money.
                # After this the workflow fails and Temporal keeps that visible;
                # the human already has a `failed` journal row from attempt one.
                maximum_attempts=3,
                # Neither of these is transient. Retrying an agent that is not in
                # the pilot allowlist, or an org with no owner to attribute work
                # to, just burns attempts on a condition only a human can change.
                non_retryable_error_types=[
                    "ContractNotAutonomous",
                    "PrincipalUnresolvable",
                ],
            ),
        )


def schedule_id_for(org_id: str, agent_id: str) -> str:
    """The Schedule's id. One per (org, agent), so re-running the installer is
    an update rather than a duplicate cadence."""
    return f"skylize-autonomous-{org_id}-{agent_id}"


def build_schedule(
    *, org_id: str, agent_id: str, task_queue: str, cron: str = PILOT_CRON
) -> "tuple[str, object]":
    """`(schedule_id, Schedule)` for one agent's cadence.

    Built here rather than in the installer script so the cadence, the workflow
    type and the id scheme live next to the workflow they start, and so a test can
    assert the shape without a Temporal connection.
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
    )

    sched_id = schedule_id_for(org_id, agent_id)
    return sched_id, Schedule(
        action=ScheduleActionStartWorkflow(
            AUTONOMOUS_AGENT_RUN_WORKFLOW,
            AutonomousRunInput(
                org_id=org_id, agent_id=agent_id, schedule_id=sched_id
            ),
            id=f"{sched_id}-run",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(cron_expressions=[cron]),
        policy=SchedulePolicy(
            # SKIP, never BUFFER_ALL. If a sweep is still running when the next
            # hour arrives, the right answer is to skip that firing: buffering
            # would queue a backlog of paid runs that all report on the same
            # stale window, and a human would find their brief full of near
            # duplicates after any outage.
            overlap=ScheduleOverlapPolicy.SKIP,
        ),
    )
