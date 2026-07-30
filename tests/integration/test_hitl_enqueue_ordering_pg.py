"""D3: the hitl_queue row is durable BEFORE the terminal deferred event.

``AgentExecutionService._govern`` used to call ``_emit_decision`` — which
publishes ``DecisionDeferredToHuman`` carrying ``hitl_id`` and writes the audit
record — BEFORE ``_enqueue_hitl`` inserted the ``decisions`` and ``hitl_queue``
rows. Two documented statements asserted the opposite ordering:

  * ``AgentDeferredToHuman``'s docstring: "A hitl_queue row is written first"
  * ``edge/routes/agents.py``: "the hitl_queue row was written before this
    response"

Failure modes that ordering produced: a subscriber reacting to the terminal
event raced an empty table; and when ``_enqueue_hitl`` raised (Postgres down, FK
violation, RLS refusal) the request 500'd while a terminal
"deferred, hitl_id=X" event and audit record were already published for a row
that would never exist.

Real Postgres throughout, with a REAL database failure — not a patched
repository. ``decisions.org_id`` is ``REFERENCES tenants(org_id)``
(0001_initial_schema.py:172) and ``PgHitlQueueRepository.enqueue`` inserts the
parent ``decisions`` row first (dal/hitl.py:74), so an org with no ``tenants``
row makes Postgres itself raise ``ForeignKeyViolationError`` from inside the
enqueue transaction. That is a genuine write failure at the real seam.

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio

from skylize.app.agents.execution import (
    AgentDeferredToHuman,
    AgentExecutionService,
)
from skylize.app.audit.service import AuditService
from skylize.app.decision_engine.evaluator import DecisionEvaluator
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.dal.hitl import PgHitlQueueRepository
from skylize.dal.memory import InMemoryAuditRepository, InMemoryCapitalRepository
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration

# hook_generator_agent's production contract declares FIRST_EXTERNAL_LAUNCH, so
# the evaluator defers — the outcome whose ordering is under test.
AGENT_ID = "hook_generator_agent"
_INPUT = {
    "brand_name": "Acme",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
    "tone": "energetic",
}


def _org() -> str:
    return f"ord_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> AsyncIterator[Database]:
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn: object, org: str) -> None:
    for sql in (
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


async def _hitl_ids(app_db: Database, org: str) -> list[uuid.UUID]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch("SELECT hitl_id FROM hitl_queue")
    return [r["hitl_id"] for r in rows]


class _ObservingBus(InMemoryEventBus):
    """An InMemoryEventBus that reads hitl_queue AT PUBLISH TIME.

    For each published event it records whether the row was already committed
    and visible to a SEPARATE database connection. That is the ordering claim
    stated as an observable fact rather than inferred from call order.
    """

    def __init__(self, app_db: Database, org: str) -> None:
        super().__init__()
        self._app_db = app_db
        self._org = org
        self.row_visible_at_publish: dict[str, bool] = {}

    async def publish(self, event: Any) -> str:
        async with self._app_db.tenant_session(self._org) as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM hitl_queue")
        # First observation per event type wins (the emit order is what matters).
        self.row_visible_at_publish.setdefault(event.type, int(n) > 0)
        return await super().publish(event)


def _service(
    *, org: str, app_db: Database, bus: InMemoryEventBus
) -> tuple[AgentExecutionService, MagicMock, MagicMock]:
    """The gate wired to a REAL PgHitlQueueRepository over real Postgres.

    The LLM and deliverable seams are fakes and asserted never to be touched —
    a deferral must not spend or persist anything.
    """
    llm = MagicMock()
    llm.generate = AsyncMock()
    deliverables = MagicMock()
    deliverables.create_deliverable = AsyncMock()
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=deliverables,
        audit=AuditService(bus, InMemoryAuditRepository()),
        evaluator=DecisionEvaluator(
            registry=MVP_REGISTRY, capital=InMemoryCapitalRepository()
        ),
        hitl=PgHitlQueueRepository(app_db),
        bus=bus,
        governed_org_ids=frozenset({org}),
    )
    return service, llm, deliverables


def _audit_action_types(bus: InMemoryEventBus) -> list[str]:
    return [e.payload.action_type for e in bus.published_of_type("audit.action_recorded")]


# ---------------------------------------------------------------------------
# The regression: a real enqueue failure publishes NO terminal deferred event
# ---------------------------------------------------------------------------

@requires_app_role
async def test_enqueue_failure_publishes_no_terminal_deferred_event(
    app_db, admin_conn
) -> None:
    """A real Postgres FK violation inside _enqueue_hitl leaves the bus silent.

    The org is deliberately NOT seeded into ``tenants``, so the ``decisions``
    INSERT that opens ``PgHitlQueueRepository.enqueue`` violates its foreign key
    and Postgres raises. Before the reorder, the terminal
    ``decision.deferred_to_human`` event and the ``decision.deferred_to_human``
    audit record had ALREADY been published at that point — announcing a
    ``hitl_id`` for a row that would never exist.
    """
    org = _org()  # never inserted into tenants -> FK violation on write
    bus = InMemoryEventBus()
    service, llm, deliverables = _service(org=org, app_db=app_db, bus=bus)
    try:
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await service.execute(
                org_id=org, agent_id=AGENT_ID, input_data=_INPUT, user_id="u1"
            )

        # NOTHING terminal was published for a row that does not exist.
        assert bus.published_of_type("decision.deferred_to_human") == []
        assert bus.published_of_type("decision.evaluated") == []
        assert "decision.deferred_to_human" not in _audit_action_types(bus)

        # And the failure really was at the write: no row landed either.
        async with app_db.tenant_session(org) as conn:
            assert int(await conn.fetchval("SELECT COUNT(*) FROM hitl_queue")) == 0
            assert int(await conn.fetchval("SELECT COUNT(*) FROM decisions")) == 0

        # A deferral never spends or persists.
        llm.generate.assert_not_called()
        deliverables.create_deliverable.assert_not_called()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_row_is_committed_before_the_terminal_event_is_published(
    app_db, admin_conn
) -> None:
    """Ordering as an observable fact: at publish time the row is already there.

    The bus reads ``hitl_queue`` on a separate connection each time it publishes.
    Under the old ordering both decision events were published while the table
    was still empty.
    """
    org = _org()
    bus = _ObservingBus(app_db, org)
    service, _llm, _deliverables = _service(org=org, app_db=app_db, bus=bus)
    try:
        await _seed_tenant(admin_conn, org)

        with pytest.raises(AgentDeferredToHuman) as ei:
            await service.execute(
                org_id=org, agent_id=AGENT_ID, input_data=_INPUT, user_id="u1"
            )

        assert bus.row_visible_at_publish["decision.evaluated"] is True
        assert bus.row_visible_at_publish["decision.deferred_to_human"] is True

        # The event names the row that exists, and the 202's id is that row's id.
        deferred = bus.published_of_type("decision.deferred_to_human")
        assert len(deferred) == 1
        assert deferred[0].payload.hitl_id == ei.value.hitl_id
        assert await _hitl_ids(app_db, org) == [ei.value.hitl_id]
        assert "decision.deferred_to_human" in _audit_action_types(bus)
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_emit_failure_after_a_written_row_fails_loud(
    app_db, admin_conn, caplog
) -> None:
    """Row written, emission fails: the failure surfaces and names the row.

    Behaviour (D3 step 11): the emission error propagates. The 'pending' row is
    left in place because it is real and actionable — a reviewer still sees it,
    and approving it re-emits a terminal decision event — whereas swallowing the
    error would report a decision as delivered that no subscriber received.
    """
    import logging

    org = _org()
    bus = InMemoryEventBus()
    service, _llm, _deliverables = _service(org=org, app_db=app_db, bus=bus)
    boom = RuntimeError("event bus unreachable")
    bus.publish = AsyncMock(side_effect=boom)  # type: ignore[method-assign]
    try:
        await _seed_tenant(admin_conn, org)

        with caplog.at_level(logging.ERROR, logger="skylize.app.agents.execution"):
            with pytest.raises(RuntimeError, match="event bus unreachable"):
                await service.execute(
                    org_id=org, agent_id=AGENT_ID, input_data=_INPUT, user_id="u1"
                )

        # The row survives, still actionable.
        rows = await _hitl_ids(app_db, org)
        assert len(rows) == 1
        async with app_db.tenant_session(org) as conn:
            status = await conn.fetchval(
                "SELECT status FROM hitl_queue WHERE hitl_id=$1", rows[0]
            )
        assert status == "pending"

        # ...and the orphaned-announcement condition is logged at ERROR, naming it.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            r.getMessage() == "hitl_row_written_but_decision_emit_failed"
            and getattr(r, "hitl_id", None) == str(rows[0])
            for r in errors
        ), f"emission failure was not logged with the row id: {[r.getMessage() for r in errors]}"
    finally:
        await _cleanup(admin_conn, org)
