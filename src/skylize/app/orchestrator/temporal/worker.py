"""Temporal worker entrypoint — the process that executes workflow activities.

Run it against a reachable Temporal server (dev server: `temporal server
start-dev`) with the postgres backend configured:

    SKYLIZE_BACKEND=postgres python -m skylize.app.orchestrator.temporal.worker

Composition is the documented recipe, not a redesign: the activities receive

  - ``LLMJudge(container.llm)`` — the verifier over the composition root's
    single shared content-gated gateway (never a bare provider adapter), and
  - ``PgWorkflowRepository(container.db)`` — the durable run-step audit trail
    (migration 0010).

The worker therefore requires the postgres backend: a durable orchestrator
writing its audit trail to a store that forgets on restart would be a lie, so
``build_activities`` fails closed on the memory backend instead.

ONE workflow definition is registered: `AutonomousAgentRunWorkflow`
(`autonomous.py`), the scheduled/triggered agent run. It is registered but NOT
started — a workflow definition is inert until something starts it, and the only
thing that does is a Temporal Schedule created by
`scripts/create_autonomous_schedule.py`, an explicit operator action against a
named org. Running this worker does not by itself cause any agent to run.

The LangGraph→Temporal engine's own workflow definitions still land later; this
worker continues to serve the activity task queue they will call.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from ....bootstrap import Container, build_container
from ....config import Settings, get_settings
from ....dal.workflows import PgWorkflowRepository
from .activities import WorkflowActivities
from .autonomous import (
    AutonomousActivities,
    AutonomousAgentRunWorkflow,
    sandbox_runner,
)
from .judge import LLMJudge

log = logging.getLogger("skylize.temporal_worker")


def build_activities(container: Container) -> WorkflowActivities:
    """Construct the activity set from a built container (the OVERNIGHT_REPORT
    recipe): judge over the shared guarded gateway, repo over the shared pool."""
    if container.db is None:
        raise RuntimeError(
            "Temporal worker requires the postgres backend (SKYLIZE_BACKEND=postgres): "
            "workflow_run_steps has no durable store on the memory backend."
        )
    return WorkflowActivities(
        repo=PgWorkflowRepository(container.db),
        # Graph builder + token minter land with the workflow definitions;
        # neither is consumed by the two activities registered today.
        builder=None,
        judge=LLMJudge(container.llm),
        minter=None,
    )


def register_worker(
    client: Client,
    activities: WorkflowActivities,
    settings: Settings,
    autonomous: AutonomousActivities | None = None,
) -> Worker:
    """Bind the workflow + activity methods to the task queue. Split from `run` so
    tests can validate registration against a lazy (unconnected) client.

    `autonomous` is optional so every pre-existing caller — and every test that
    asserts the judge/run-step registration — keeps working unchanged. When it is
    absent the autonomous ACTIVITY is not registered, which means a scheduled
    workflow would start and then block on an activity nobody serves rather than
    silently running with a half-built service. Failing visibly beats running
    wrong.
    """
    autonomous_activities = (
        [autonomous.run_autonomous_agent] if autonomous is not None else []
    )
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        # REQUIRED whenever a workflow lives under `skylize.app.*` — see
        # autonomous.sandbox_runner for what breaks without it.
        workflow_runner=sandbox_runner(),
        workflows=[AutonomousAgentRunWorkflow],
        activities=[
            activities.run_judge_verification,
            activities.write_run_step,
            *autonomous_activities,
        ],
    )


async def run(settings: Settings | None = None) -> None:
    """Build the container, connect to Temporal, and serve the task queue
    until cancelled. The container closes on the way out either way."""
    settings = settings or get_settings()
    container = await build_container(settings)
    try:
        activities = build_activities(container)
        # None only if the composition root failed to build it, which cannot
        # happen on the postgres backend this worker already requires.
        autonomous = (
            AutonomousActivities(container.autonomous_runs)
            if container.autonomous_runs is not None
            else None
        )
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        worker = register_worker(client, activities, settings, autonomous)
        log.info(
            "Temporal worker serving task_queue=%s at %s (namespace=%s)",
            settings.temporal_task_queue,
            settings.temporal_address,
            settings.temporal_namespace,
        )
        await worker.run()
    finally:
        await container.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
