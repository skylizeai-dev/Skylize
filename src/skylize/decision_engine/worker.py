"""OPA Decision Engine worker entrypoint — the process that decides proposals.

Run it against a reachable Postgres, Redis, and OPA server:

    SKYLIZE_DECISION_ENGINE=opa python -m skylize.decision_engine.worker

Two long-lived tasks serve one environment (decision_flow.md §3, §8):

  - ``DecisionEngineConsumer.run`` — subscribes ``evt:{org}:{department}`` for
    every configured org × served department, runs each proposal through the
    six-stage OPA pipeline, and durably records the outcome (decisions row +
    decision_outbox row in one transaction, plus a hitl_queue row on a defer);
  - ``OutboxPoller.run`` — relays those committed outbox rows onto Redis.

Both are required for a decision to reach the bus, so either one dying takes the
whole worker down rather than leaving a half-working process: a consumer with a
dead poller commits decisions nothing ever publishes, which looks healthy and
is not. Kubernetes/systemd restarts; the outbox makes that safe.

This is a SEPARATE PROCESS from the API composition root, not a second engine in
it. ``bootstrap.build_container`` still wires only the inline engine and still
fails closed on ``SKYLIZE_DECISION_ENGINE=opa`` — per environment exactly one
engine emits terminal ``decision.*`` events (decision_engine.md §2), so the flag
selects which process is allowed to run, and this worker refuses to start unless
it names ``opa``. It therefore composes its own concretes from
``DecisionEngineSettings`` instead of reusing the container, which would be the
inline engine's.

Two long-lived tasks, one composition: the consumer serves BOTH inbound paths —
proposals to the six-stage pipeline, and ``governance.human_approval_received``
verdicts to ``HITLResumeHandler`` (see ``build_consumer``).

NOT YET WIRED FOR PRODUCTION. The flag stays ``inline`` until real Rego replaces
the placeholder policy and a live OPA server exists. (The HITL resume path,
previously listed here as a third blocker, has landed — ``resume.py`` +
``consumer._handle_resume``.) Until then this entrypoint is runnable and tested
but deliberately unreachable from the default config.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

from ..config import Settings, get_settings
from ..dal.connection import Database
from ..dal.decision_stores import PgProcessedEventStore
from ..events.bus import EventBus
from ..events.redis_adapter import RedisEventBus
from .capital_dal import CapitalDAL
from .config import DecisionEngineSettings
from .consumer import DecisionEngineConsumer
from .hitl_writer import HITLQueueWriter
from .opa_client import OPAClient
from .orchestrator import DecisionOrchestrator
from .outbox_poller import OutboxPoller
from .pipeline import EvaluationPipeline
from .publisher import DecisionEventPublisher
from .resume import HITLResumeHandler
from .scoring import ScoringEngine

log = logging.getLogger("skylize.decision_engine.worker")

Closer = Callable[[], Awaitable[None]]


def require_opa_engine(settings: Settings) -> None:
    """Refuse to start unless this environment selects the OPA engine.

    The mirror of ``bootstrap``'s guard, pointing the other way. Two engines
    emitting terminal ``decision.*`` events for one tenant would double-decide
    every proposal, so the flag is the interlock and both sides fail closed on
    it rather than assuming they are the chosen one.
    """
    if settings.decision_engine != "opa":
        raise RuntimeError(
            f"SKYLIZE_DECISION_ENGINE={settings.decision_engine!r} does not select the "
            "OPA decision engine; this worker would double-decide alongside the inline "
            "engine the API process runs. Set SKYLIZE_DECISION_ENGINE=opa to run it."
        )


def build_orchestrator(
    de_settings: DecisionEngineSettings,
    *,
    db: Database,
    redis: aioredis.Redis,
    opa: OPAClient,
    bus: EventBus,
) -> DecisionOrchestrator:
    """Compose evaluate → persist/enqueue → escalate. Split from ``run`` so tests
    can assert the wiring without reaching Postgres, Redis, or OPA."""
    pipeline = EvaluationPipeline(
        opa_client=opa,
        scoring_engine=ScoringEngine(de_settings),
        capital_dal=CapitalDAL(db, de_settings),
        settings=de_settings,
        # Sink for the per-stage audit mirrors onto `evt:{tenant}:audit`.
        event_bus=bus,
    )
    return DecisionOrchestrator(
        pipeline,
        DecisionEventPublisher(db=db, settings=de_settings),
        HITLQueueWriter(db=db, redis=redis, settings=de_settings),
    )


def build_consumer(
    de_settings: DecisionEngineSettings,
    orchestrator: DecisionOrchestrator,
    *,
    db: Database,
    bus: EventBus,
) -> DecisionEngineConsumer:
    """Bind the transport to BOTH inbound paths.

    ``orchestrator.process`` decides proposals; ``HITLResumeHandler.resume``
    finishes the ones a human was asked about. Wiring both here is what makes
    subscribing to the ``governance`` channel safe — a consumer that heard a
    verdict with no resume handler would raise rather than drop it, but a worker
    that never wires one would just DLQ every human decision.

    Idempotency is the durable Pg store, never the in-memory default: a worker
    that forgot its processed set on restart would re-decide every in-flight
    proposal it had already committed.
    """
    return DecisionEngineConsumer(
        bus,
        de_settings,
        orchestrator.process,
        resume_fn=HITLResumeHandler(db, de_settings).resume,
        processed=PgProcessedEventStore(db),
    )


async def run(settings: Settings | None = None) -> None:
    """Build the worker's dependencies and serve until cancelled or a task dies."""
    settings = settings or get_settings()
    require_opa_engine(settings)
    de_settings = DecisionEngineSettings()

    closers: list[Closer] = []
    try:
        db = Database(de_settings.database_url)
        await db.connect()
        closers.append(db.close)

        # Raw client for the two writers that predate the bus port: the HITL
        # governance-stream XADD and the outbox relay, both of which address
        # streams by an explicit key rather than by (tenant, department).
        redis: aioredis.Redis = aioredis.from_url(
            de_settings.redis_url, decode_responses=True
        )
        closers.append(redis.aclose)

        # redis_idle_time_ms is the PEL reclaim window: how long a delivered-but-
        # unacked message must sit before another pass re-delivers it.
        bus = RedisEventBus(
            de_settings.redis_url,
            reclaim_min_idle_ms=de_settings.redis_idle_time_ms,
        )
        closers.append(bus.close)

        opa = OPAClient(de_settings)
        closers.append(opa.close)

        orchestrator = build_orchestrator(
            de_settings, db=db, redis=redis, opa=opa, bus=bus
        )
        consumer = build_consumer(de_settings, orchestrator, db=db, bus=bus)
        poller = OutboxPoller(db, redis, de_settings)

        # Registered before the tasks start so a failure during startup still
        # unwinds the subscriptions the consumer may already have spawned.
        closers.append(consumer.stop)

        log.info(
            "decision_engine_worker_starting orgs=%s opa=%s",
            settings.decision_engine_org_ids,
            de_settings.opa_url,
        )
        await _serve(consumer, poller, settings.decision_engine_org_ids)
    finally:
        # LIFO, like the container's: the consumer/poller stop before the pools
        # and clients they read from are closed.
        for closer in reversed(closers):
            try:
                await closer()
            except Exception:  # noqa: BLE001 — one bad closer must not strand the rest
                log.warning("decision_engine_worker_closer_failed", exc_info=True)


async def _serve(
    consumer: DecisionEngineConsumer,
    poller: OutboxPoller,
    org_ids: list[str],
) -> None:
    """Run both tasks until one finishes, then cancel the other and surface why."""
    tasks = [
        asyncio.create_task(consumer.run(org_ids), name="decision_engine_consumer"),
        asyncio.create_task(poller.run(), name="decision_outbox_poller"),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        # Awaited, not just cancelled: a re-raise here runs the closers in
        # ``run``, and those close the pools these tasks are still unwinding on.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for task in pending:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    for task in done:
        # Re-raises whatever killed it; neither task returns normally in a
        # healthy worker, so a clean return is itself worth surfacing.
        task.result()
        log.error("decision_engine_worker_task_exited name=%s", task.get_name())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
