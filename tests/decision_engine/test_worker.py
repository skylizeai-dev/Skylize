"""OPA Decision Engine worker entrypoint — flag interlock, wiring, supervision.

``run`` itself is not exercised here (it opens a Postgres pool, a Redis pool and
an OPA client); the parts worth pinning are split out of it precisely so they can
be: the flag guard, the composition, and the two-task supervision.
"""
from __future__ import annotations

import asyncio

import pytest

from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.decision_stores import PgProcessedEventStore
from skylize.decision_engine import worker
from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.opa_client import OPAClient
from skylize.decision_engine.orchestrator import DecisionOrchestrator
from skylize.events.memory_bus import InMemoryEventBus


# ---------------------------------------------------------------------------
# The flag is an interlock, and both sides fail closed on it
# ---------------------------------------------------------------------------


def test_worker_refuses_to_start_under_the_inline_engine():
    with pytest.raises(RuntimeError, match="does not select the OPA decision engine"):
        worker.require_opa_engine(Settings(backend="memory", decision_engine="inline"))


def test_worker_starts_under_the_opa_engine():
    worker.require_opa_engine(Settings(backend="memory", decision_engine="opa"))


def test_bootstrap_refuses_the_flag_the_worker_requires():
    """The other half of the interlock: exactly one engine per environment may
    emit terminal decision.* events, so bootstrap must reject what the worker
    demands. If both ever accepted 'opa', every proposal would be decided twice."""
    from skylize.bootstrap import build_container

    with pytest.raises(RuntimeError, match="only the inline engine"):
        asyncio.run(build_container(Settings(backend="memory", decision_engine="opa")))


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_build_consumer_uses_the_durable_processed_store(settings, mock_redis):
    """An in-memory idempotency store would re-decide every in-flight proposal
    after a restart."""
    db = Database("postgresql://unused/unused")  # constructor opens nothing
    bus = InMemoryEventBus()
    opa = OPAClient(settings)
    orchestrator = worker.build_orchestrator(
        settings, db=db, redis=mock_redis, opa=opa, bus=bus
    )
    consumer = worker.build_consumer(settings, orchestrator, db=db, bus=bus)

    assert isinstance(consumer, DecisionEngineConsumer)
    assert isinstance(consumer._processed, PgProcessedEventStore)
    assert consumer._bus is bus


def test_build_orchestrator_feeds_the_consumer(settings, mock_redis):
    """`process` is the pipeline_fn — the seam the whole rebuild exists to close."""
    db = Database("postgresql://unused/unused")
    bus = InMemoryEventBus()
    orchestrator = worker.build_orchestrator(
        settings, db=db, redis=mock_redis, opa=OPAClient(settings), bus=bus
    )
    consumer = worker.build_consumer(settings, orchestrator, db=db, bus=bus)

    assert isinstance(orchestrator, DecisionOrchestrator)
    assert consumer._pipeline_fn == orchestrator.process


# ---------------------------------------------------------------------------
# Supervision: neither task may outlive the other
# ---------------------------------------------------------------------------


class _FakeConsumer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started = False
        self.stopped = False
        self.cancelled = False

    async def run(self, org_ids):
        self.started = True
        if self.fail:
            raise RuntimeError("consumer died")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def stop(self) -> None:
        self.stopped = True


class _FakePoller:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cancelled = False

    async def run(self) -> None:
        if self.fail:
            raise RuntimeError("poller died")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_a_dead_poller_takes_the_consumer_down():
    """A consumer with a dead poller commits decisions nothing ever publishes —
    a process that looks healthy and silently stops delivering."""
    consumer = _FakeConsumer()
    poller = _FakePoller(fail=True)

    with pytest.raises(RuntimeError, match="poller died"):
        await worker._serve(consumer, poller, ["org_a"])

    assert consumer.cancelled


async def test_a_dead_consumer_takes_the_poller_down():
    consumer = _FakeConsumer(fail=True)
    poller = _FakePoller()

    with pytest.raises(RuntimeError, match="consumer died"):
        await worker._serve(consumer, poller, ["org_a"])

    assert poller.cancelled


async def test_serve_cancels_both_tasks_on_shutdown():
    consumer = _FakeConsumer()
    poller = _FakePoller()

    task = asyncio.create_task(worker._serve(consumer, poller, ["org_a"]))
    for _ in range(50):
        if consumer.started:
            break
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert consumer.cancelled
    assert poller.cancelled
