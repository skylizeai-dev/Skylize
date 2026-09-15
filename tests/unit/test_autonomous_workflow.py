"""The Temporal binding: sandbox validity, correlation stability, schedule shape.

THE TEST THAT EARNED ITS PLACE is `test_the_workflow_passes_sandbox_validation`.
Building a `Worker` needs a reachable Temporal server, so nothing in the default
suite would otherwise construct one -- and the failure this catches happens at
WORKER CONSTRUCTION, before any workflow runs, with a message naming
`random.getrandbits` rather than the import chain that reached it. It was found
by running a real worker against a real Temporal dev server and would have shipped
as "the scheduled agent never ran" otherwise.

`SandboxedWorkflowRunner.prepare_workflow` is exactly the call `_WorkflowWorker`
makes during `Worker.__init__`, so this reproduces that check with no server.
"""

from __future__ import annotations

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from skylize.app.autonomy.pilot import PILOT_AGENT_ID, PILOT_CRON
from skylize.app.orchestrator.temporal.autonomous import (
    AUTONOMOUS_AGENT_RUN_WORKFLOW,
    RUN_AUTONOMOUS_AGENT,
    AutonomousAgentRunWorkflow,
    build_schedule,
    correlation_for,
    sandbox_runner,
    schedule_id_for,
)

ORG = "org_test"
QUEUE = "skylize-workflows"


def _defn() -> object:
    return workflow._Definition.must_from_class(AutonomousAgentRunWorkflow)


async def test_the_workflow_passes_sandbox_validation() -> None:
    """With `sandbox_runner()`, a worker can be constructed for this workflow."""
    sandbox_runner().prepare_workflow(_defn())  # must not raise


async def test_without_the_skylize_passthrough_the_sandbox_rejects_it() -> None:
    """Pins WHY `sandbox_runner` marks `skylize` passthrough, not as ceremony.

    The sandbox imports a workflow by its full dotted path, so it re-executes every
    PARENT package. `app/orchestrator/__init__.py:5` reaches, via the orchestrator
    and its runner, `app/deliverables/service.py:19`'s `import structlog` --
    and structlog pulls `rich.style`, which runs `count(getrandbits(24))` at module
    scope. `random` is restricted at IMPORT time (it is not `only_runtime`, unlike
    `time` and `os`), so re-importing the graph is rejected. That is the rule this
    test is here to hold.

    WHY `cryptography` IS PASSED THROUGH: to isolate that restriction as the single
    variable under test. It is not what `sandbox_runner` is for.
    `app/governance/authority.py:23` imports `cryptography.hazmat.bindings._rust`,
    a NATIVE extension, and CPython cannot re-exec one against the sandbox's
    substituted module dict -- `exec_dynamic` raises `SystemError` before any
    restriction is consulted. Left in the sandbox, this test would assert a CPython
    implementation accident instead of the determinism rule, which is exactly how
    it broke: #13 deleted `runtime.agent_runner` from the orchestrator's `__init__`,
    which reordered the chain so `cryptography` was reached first and the
    `SystemError` masked the restriction. The restriction never stopped being true.
    """
    from temporalio.worker.workflow_sandbox import SandboxRestrictions
    from temporalio.worker.workflow_sandbox._restrictions import (
        RestrictedWorkflowAccessError,
    )

    with pytest.raises(RestrictedWorkflowAccessError, match="random.getrandbits"):
        SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "cryptography"
            )
        ).prepare_workflow(_defn())


def test_the_correlation_id_is_stable_across_retries() -> None:
    """One scheduled firing is ONE correlation id, however many attempts it takes.

    A fresh id per attempt would put two rows in a human's brief for one sweep and
    split its cost across two correlations.
    """
    wf_id = "skylize-autonomous-org_a-fraud_detection_agent-2026-09-14T06:00:00Z"
    assert correlation_for(wf_id) == correlation_for(wf_id)
    assert correlation_for(wf_id) != correlation_for(wf_id + "x")


def test_schedule_ids_are_per_org_and_agent() -> None:
    """Re-running the installer updates one cadence; it never stacks a second."""
    assert schedule_id_for("org_a", "x") == schedule_id_for("org_a", "x")
    assert schedule_id_for("org_a", "x") != schedule_id_for("org_b", "x")
    assert schedule_id_for("org_a", "x") != schedule_id_for("org_a", "y")


def test_the_schedule_starts_the_registered_workflow_on_the_pilot_cadence() -> None:
    from temporalio.client import ScheduleOverlapPolicy

    sched_id, schedule = build_schedule(
        org_id=ORG, agent_id=PILOT_AGENT_ID, task_queue=QUEUE
    )
    assert sched_id == schedule_id_for(ORG, PILOT_AGENT_ID)
    # The name the worker registers, not a Python symbol that a rename could
    # silently decouple from schedules already registered against it.
    assert schedule.action.workflow == AUTONOMOUS_AGENT_RUN_WORKFLOW
    assert schedule.action.task_queue == QUEUE
    assert schedule.spec.cron_expressions == [PILOT_CRON]
    # SKIP, so an outage cannot queue a backlog of paid runs that all report on
    # the same stale window.
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP

    args = schedule.action.args
    assert args[0].org_id == ORG
    assert args[0].agent_id == PILOT_AGENT_ID


def test_the_activity_name_is_pinned() -> None:
    """Schedules and workers agree on a string, not on a function identity."""
    assert RUN_AUTONOMOUS_AGENT == "run_autonomous_agent"
    assert AUTONOMOUS_AGENT_RUN_WORKFLOW == "AutonomousAgentRunWorkflow"
