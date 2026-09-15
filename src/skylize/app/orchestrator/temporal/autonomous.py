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

THIS MODULE IMPORTS NO APPLICATION CODE AT MODULE LEVEL, and that is load-bearing
rather than stylistic. The sandbox re-imports a workflow's entire module tree when
it validates the definition, and it refuses any module that does non-deterministic
work at import time. Reaching `skylize.app.autonomy` from here pulls the whole
application graph in behind it -- which transitively reaches `rich`, whose
`style.py:22` calls `random.getrandbits(24)` at import. Worker construction then
fails with RestrictedWorkflowAccessError before a single run happens. This was
found by running a real worker against a real Temporal server, not by reading.

Two things follow, and BOTH are needed:

  * the application imports live INSIDE the activity method, which runs outside
    the sandbox;
  * the worker must be built with `sandbox_runner()` below, which marks `skylize`
    as a passthrough module. Deferring this module's own imports is not enough on
    its own, because the sandbox imports a workflow by its full dotted path and so
    re-executes every PARENT package -- and
    `app/orchestrator/__init__.py:5` imports the orchestrator, which reaches its
    runner and `app/deliverables/service.py`'s `import structlog`, which is the
    whole graph again.

Passthrough is safe precisely because of the split above: the workflow performs no
application work, so reusing the already-imported modules rather than re-executing
them cannot make replay non-deterministic.

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
from typing import TYPE_CHECKING, Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

if TYPE_CHECKING:  # never executed, so never seen by the workflow sandbox
    from ....app.autonomy.runner import AutonomousRunService

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

    def __init__(self, service: "AutonomousRunService") -> None:
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
        # Deferred: see the module docstring. An activity runs outside the
        # sandbox, so these are ordinary imports here and invisible to it.
        from ....app.autonomy.pilot import assert_pilot_agent
        from ....app.autonomy.triggers import run_scheduled

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


def sandbox_runner() -> "Any":
    """The workflow runner a worker serving this workflow MUST be built with.

    Marks `skylize` as a passthrough module, which is exactly what the sandbox's
    own error message prescribes for "code from a module not used in a workflow or
    known to only be used deterministically from a workflow". Both clauses hold
    here: the workflow body touches no application module at all.

    Without it, `Worker(...)` fails at CONSTRUCTION -- before any run -- and WHICH
    error it fails with depends only on how far the re-import gets. `governance/
    authority.py:23` reaches `cryptography`'s native `_rust` extension, which
    CPython cannot re-exec against the sandbox's substituted module dict
    (`SystemError` out of `exec_dynamic`); past that, the chain reaches
    `rich.style`'s import-time `random.getrandbits`
    (`RestrictedWorkflowAccessError`). Neither is a failure the worker recovers
    from. Wrapped in a named function so every worker gets the same configuration
    and the reason travels with it.
    """
    from temporalio.worker.workflow_sandbox import (
        SandboxedWorkflowRunner,
        SandboxRestrictions,
    )

    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("skylize")
    )


def schedule_id_for(org_id: str, agent_id: str) -> str:
    """The Schedule's id. One per (org, agent), so re-running the installer is
    an update rather than a duplicate cadence."""
    return f"skylize-autonomous-{org_id}-{agent_id}"


def build_schedule(
    *, org_id: str, agent_id: str, task_queue: str, cron: str | None = None
) -> "tuple[str, Any]":
    """`(schedule_id, Schedule)` for one agent's cadence.

    Built here rather than in the installer script so the cadence, the workflow
    type and the id scheme live next to the workflow they start, and so a test can
    assert the shape without a Temporal connection.

    `cron` defaults to the pilot cadence, resolved lazily for the same reason the
    activity's imports are deferred — this module must not reach application code
    at import time.
    """
    from ....app.autonomy.pilot import PILOT_CRON
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleSpec,
    )

    cron = cron if cron is not None else PILOT_CRON
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
