"""Org autonomy mode — the parts provable without a database.

The RLS and migration-shape guarantees need real Postgres and live in
``tests/integration/test_org_autonomy_mode_pg.py``, which SKIPS without
``SKYLIZE_TEST_DB_URL`` / ``SKYLIZE_TEST_APP_DB_URL``. These tests run in the
unit gate unconditionally, so the fail-closed rule (owner ruling 7) is proven on
every CI run rather than only on a Postgres-equipped one.

Proven here:
  * a missing row resolves to ``observe``, and ``read_mode`` has no way to
    return anything else when the query comes back empty;
  * an explicit row, including an explicit ``observe``, reads back exactly;
  * ``read_configured_mode`` keeps "never set" distinguishable from "chose
    observe" — the distinction the console needs and enforcement must not use;
  * ``set_mode`` rejects a value outside the five modes BEFORE touching the DB;
  * the GET route reports the fail-closed mode with ``configured=false``;
  * both routes 503 on the memory backend rather than inventing a posture.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from skylize.contracts.base import DEFAULT_AUTONOMY_MODE
from skylize.dal.org_autonomy_mode import OrgAutonomyModeDAL
from skylize.edge.routes.autonomy import (
    AutonomyModeResponse,
    SetAutonomyModeRequest,
    get_autonomy_mode,
    set_autonomy_mode,
)


class _FakeConn:
    """Returns one canned value for the single fetchval the DAL issues."""

    def __init__(self, value: str | None) -> None:
        self.value = value
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, *args: object) -> str | None:
        self.queries.append((query, args))
        return self.value

    async def execute(self, query: str, *args: object) -> None:
        self.queries.append((query, args))


class _FakeDb:
    def __init__(self, value: str | None) -> None:
        self.conn = _FakeConn(value)
        self.bound_orgs: list[str] = []

    @asynccontextmanager
    async def tenant_session(self, org_id: str):
        self.bound_orgs.append(org_id)
        yield self.conn


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class _Ctx:
    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.correlation_id = uuid.uuid4()


class _Container:
    def __init__(self, dal: object | None, audit: object | None = None) -> None:
        self.autonomy_mode_dal = dal
        self.audit = audit


# ---------------------------------------------------------------------------
# DAL — fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_row_reads_as_observe() -> None:
    dal = OrgAutonomyModeDAL(_FakeDb(None))
    assert await dal.read_mode("org-1") == "observe"


@pytest.mark.asyncio
async def test_the_fail_closed_default_is_observe() -> None:
    """Pinned against the constant, so moving the default is a deliberate edit."""
    assert DEFAULT_AUTONOMY_MODE == "observe"


@pytest.mark.asyncio
async def test_missing_row_is_distinguishable_from_explicit_observe() -> None:
    unset = OrgAutonomyModeDAL(_FakeDb(None))
    chosen = OrgAutonomyModeDAL(_FakeDb("observe"))
    # Same posture to act on...
    assert await unset.read_mode("org-1") == await chosen.read_mode("org-1") == "observe"
    # ...different facts about whether anyone chose it.
    assert await unset.read_configured_mode("org-1") is None
    assert await chosen.read_configured_mode("org-1") == "observe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["observe", "propose", "act_within_budget", "act_and_reallocate", "act_governed"],
)
async def test_explicit_row_reads_back_exactly(mode: str) -> None:
    dal = OrgAutonomyModeDAL(_FakeDb(mode))
    assert await dal.read_mode("org-1") == mode


@pytest.mark.asyncio
async def test_read_is_bound_to_the_callers_org() -> None:
    db = _FakeDb("propose")
    dal = OrgAutonomyModeDAL(db)
    await dal.read_mode("org-7")
    assert db.bound_orgs == ["org-7"], "the read must run inside that org's session"


@pytest.mark.asyncio
async def test_read_resolves_the_latest_row_at_or_before_the_instant() -> None:
    db = _FakeDb("propose")
    dal = OrgAutonomyModeDAL(db)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await dal.read_mode("org-1", at)
    query, args = db.conn.queries[0]
    assert "effective_from <= $2" in query
    assert "ORDER BY effective_from DESC" in query
    assert args[1] == at


@pytest.mark.asyncio
async def test_set_mode_rejects_an_unknown_mode_before_touching_the_db() -> None:
    db = _FakeDb(None)
    dal = OrgAutonomyModeDAL(db)
    with pytest.raises(ValueError, match="autonomy_mode must be one of"):
        await dal.set_mode(
            org_id="org-1",
            autonomy_mode="full_send",  # type: ignore[arg-type]
            audit=_FakeAudit(),
            correlation_id=uuid.uuid4(),
        )
    assert db.conn.queries == [], "a rejected mode must not reach the database"


@pytest.mark.asyncio
async def test_set_mode_records_a_governance_audit_action() -> None:
    audit = _FakeAudit()
    dal = OrgAutonomyModeDAL(_FakeDb(None))
    result = await dal.set_mode(
        org_id="org-1",
        autonomy_mode="act_governed",
        audit=audit,
        correlation_id=uuid.uuid4(),
    )
    assert result == "act_governed"
    assert len(audit.records) == 1
    assert audit.records[0]["action_type"] == "governance.autonomy_mode_set"
    assert audit.records[0]["org_id"] == "org-1"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reports_the_fail_closed_mode_as_unconfigured() -> None:
    dal = OrgAutonomyModeDAL(_FakeDb(None))
    resp = await get_autonomy_mode(ctx=_Ctx(), container=_Container(dal))
    assert isinstance(resp, AutonomyModeResponse)
    assert resp.mode == "observe"
    assert resp.configured is False


@pytest.mark.asyncio
async def test_get_reports_an_explicit_mode_as_configured() -> None:
    dal = OrgAutonomyModeDAL(_FakeDb("act_within_budget"))
    resp = await get_autonomy_mode(ctx=_Ctx(), container=_Container(dal))
    assert resp.mode == "act_within_budget"
    assert resp.configured is True


@pytest.mark.asyncio
async def test_get_reports_an_explicit_observe_as_configured() -> None:
    """An owner who chose `observe` must not be told they never chose."""
    dal = OrgAutonomyModeDAL(_FakeDb("observe"))
    resp = await get_autonomy_mode(ctx=_Ctx(), container=_Container(dal))
    assert resp.mode == "observe"
    assert resp.configured is True


@pytest.mark.asyncio
async def test_put_sets_and_returns_the_new_mode() -> None:
    audit = _FakeAudit()
    dal = OrgAutonomyModeDAL(_FakeDb(None))
    resp = await set_autonomy_mode(
        body=SetAutonomyModeRequest(mode="act_and_reallocate"),
        ctx=_Ctx(),
        container=_Container(dal, audit),
    )
    assert resp.mode == "act_and_reallocate"
    assert resp.configured is True
    assert audit.records[0]["action_type"] == "governance.autonomy_mode_set"


@pytest.mark.asyncio
async def test_routes_503_without_the_postgres_backend() -> None:
    """No DAL means no posture store. Reporting a made-up mode would be worse
    than an error: the caller would act on a number nothing wrote."""
    for call in (
        get_autonomy_mode(ctx=_Ctx(), container=_Container(None)),
        set_autonomy_mode(
            body=SetAutonomyModeRequest(mode="propose"),
            ctx=_Ctx(),
            container=_Container(None),
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await call
        assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_put_rejects_a_mode_outside_the_five() -> None:
    """The request schema is the first of three gates; the DAL and the table's
    CHECK constraint are the other two."""
    with pytest.raises(Exception):
        SetAutonomyModeRequest(mode="full_send")  # type: ignore[arg-type]
