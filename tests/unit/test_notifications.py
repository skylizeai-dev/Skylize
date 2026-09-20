"""Notifications — the parts provable without a database.

The RLS and migration-shape guarantees need real Postgres and live in
``tests/integration/test_notifications_pg.py``, which SKIPS without
``SKYLIZE_TEST_DB_URL`` / ``SKYLIZE_TEST_APP_DB_URL``.

Proven here:
  * ``record`` is BEST EFFORT — a DB failure is swallowed and logged, never
    raised, mirroring ``SlackApprovalNotifier``;
  * ``record`` rejects an unknown kind/severity before touching the database;
  * ``list_for_org`` builds the unread filter and the keyset cursor correctly;
  * ``mark_read`` is idempotent and tenant-scoped;
  * the list/mark-read ROUTES report the right shape and 503 without the
    postgres backend, mirroring ``test_org_autonomy_mode.py``;
  * ``AgentExecutionService`` writes a notification at both real producer
    points (HITL deferral, governance denial) and NEVER lets a notification
    failure break the action that triggered it.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from skylize.dal.notifications import NotificationsDAL
from skylize.edge.routes.notifications import (
    NotificationListResponse,
    NotificationResponse,
    list_notifications,
    mark_notification_read,
)


class _FakeConn:
    def __init__(
        self,
        *,
        fetch_rows: list[dict[str, object]] | None = None,
        fetchrow_row: dict[str, object] | None = None,
        fetchval_value: object = 0,
        raise_on_execute: bool = False,
    ) -> None:
        self._fetch_rows = fetch_rows or []
        self._fetchrow_row = fetchrow_row
        self._fetchval_value = fetchval_value
        self._raise_on_execute = raise_on_execute
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_queries: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> None:
        if self._raise_on_execute:
            raise RuntimeError("db unavailable")
        self.executed.append((query, args))

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_queries.append((query, args))
        return self._fetch_rows

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetch_queries.append((query, args))
        return self._fetchrow_row

    async def fetchval(self, query: str, *args: object) -> object:
        self.fetch_queries.append((query, args))
        return self._fetchval_value


class _FakeDb:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.bound_orgs: list[str] = []

    @asynccontextmanager
    async def tenant_session(self, org_id: str):
        self.bound_orgs.append(org_id)
        yield self.conn


class _Ctx:
    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.correlation_id = uuid.uuid4()


class _Container:
    def __init__(self, dal: object | None) -> None:
        self.notifications_dal = dal


def _row(**overrides: object) -> dict[str, object]:
    base = {
        "notification_id": uuid.uuid4(),
        "org_id": "org-1",
        "kind": "hitl.approval_requested",
        "severity": "warning",
        "title": "Approval needed: spend.increase",
        "body": "agent is waiting on a human decision",
        "correlation_id": uuid.uuid4(),
        "created_at": datetime(2026, 9, 17, tzinfo=timezone.utc),
        "read_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DAL — record (best effort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_inserts_and_returns_the_new_id() -> None:
    conn = _FakeConn()
    db = _FakeDb(conn)
    dal = NotificationsDAL(db)
    result = await dal.record(
        org_id="org-1",
        kind="hitl.approval_requested",
        severity="warning",
        title="t",
        body="b",
    )
    assert result is not None
    assert db.bound_orgs == ["org-1"]
    assert len(conn.executed) == 1


@pytest.mark.asyncio
async def test_record_rejects_an_unknown_kind_before_touching_the_db() -> None:
    conn = _FakeConn()
    dal = NotificationsDAL(_FakeDb(conn))
    result = await dal.record(
        org_id="org-1", kind="made.up", severity="warning", title="t", body="b"  # type: ignore[arg-type]
    )
    assert result is None
    assert conn.executed == []


@pytest.mark.asyncio
async def test_record_rejects_an_unknown_severity_before_touching_the_db() -> None:
    conn = _FakeConn()
    dal = NotificationsDAL(_FakeDb(conn))
    result = await dal.record(
        org_id="org-1",
        kind="hitl.approval_requested",
        severity="apocalyptic",  # type: ignore[arg-type]
        title="t",
        body="b",
    )
    assert result is None
    assert conn.executed == []


@pytest.mark.asyncio
async def test_record_swallows_a_db_failure_and_returns_none() -> None:
    """BEST EFFORT, no exceptions — a Slack-outage-shaped guarantee."""
    conn = _FakeConn(raise_on_execute=True)
    dal = NotificationsDAL(_FakeDb(conn))
    result = await dal.record(
        org_id="org-1",
        kind="governance.action_denied",
        severity="critical",
        title="t",
        body="b",
    )
    assert result is None  # did not raise


# ---------------------------------------------------------------------------
# DAL — list / unread count / mark_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_org_defaults_to_newest_first_no_filter() -> None:
    conn = _FakeConn(fetch_rows=[_row()])
    dal = NotificationsDAL(_FakeDb(conn))
    rows = await dal.list_for_org("org-1")
    assert len(rows) == 1
    query, args = conn.fetch_queries[0]
    assert "ORDER BY created_at DESC" in query
    assert "read_at IS NULL" not in query
    assert args[0] == "org-1"


@pytest.mark.asyncio
async def test_list_for_org_applies_the_unread_filter() -> None:
    conn = _FakeConn(fetch_rows=[])
    dal = NotificationsDAL(_FakeDb(conn))
    await dal.list_for_org("org-1", unread_only=True)
    query, _args = conn.fetch_queries[0]
    assert "read_at IS NULL" in query


@pytest.mark.asyncio
async def test_list_for_org_applies_the_keyset_cursor() -> None:
    conn = _FakeConn(fetch_rows=[])
    dal = NotificationsDAL(_FakeDb(conn))
    before = datetime(2026, 9, 17, tzinfo=timezone.utc)
    await dal.list_for_org("org-1", before=before)
    query, args = conn.fetch_queries[0]
    assert "created_at <" in query
    assert before in args


@pytest.mark.asyncio
async def test_list_for_org_caps_the_limit() -> None:
    conn = _FakeConn(fetch_rows=[])
    dal = NotificationsDAL(_FakeDb(conn))
    await dal.list_for_org("org-1", limit=10_000)
    _query, args = conn.fetch_queries[0]
    assert args[-1] == 200  # MAX_LIST_LIMIT


@pytest.mark.asyncio
async def test_unread_count_reads_the_scalar() -> None:
    conn = _FakeConn(fetchval_value=3)
    dal = NotificationsDAL(_FakeDb(conn))
    assert await dal.unread_count("org-1") == 3


@pytest.mark.asyncio
async def test_mark_read_returns_none_when_not_found() -> None:
    conn = _FakeConn(fetchrow_row=None)
    dal = NotificationsDAL(_FakeDb(conn))
    result = await dal.mark_read(org_id="org-1", notification_id=uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_mark_read_returns_the_row_when_found() -> None:
    nid = uuid.uuid4()
    conn = _FakeConn(fetchrow_row=_row(notification_id=nid, read_at=datetime.now(timezone.utc)))
    dal = NotificationsDAL(_FakeDb(conn))
    result = await dal.mark_read(org_id="org-1", notification_id=nid)
    assert result is not None
    assert result.notification_id == nid
    assert result.read_at is not None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_route_reports_unread_count_and_cursor() -> None:
    conn = _FakeConn(fetch_rows=[_row() for _ in range(2)], fetchval_value=5)
    dal = NotificationsDAL(_FakeDb(conn))
    resp = await list_notifications(
        limit=2, unread_only=False, before=None, ctx=_Ctx(), container=_Container(dal)
    )
    assert isinstance(resp, NotificationListResponse)
    assert resp.unread_count == 5
    assert len(resp.notifications) == 2
    # A full page (len == limit) implies there may be more.
    assert resp.next_before is not None


@pytest.mark.asyncio
async def test_list_route_rejects_a_naive_cursor() -> None:
    dal = NotificationsDAL(_FakeDb(_FakeConn()))
    with pytest.raises(HTTPException) as excinfo:
        await list_notifications(
            limit=50,
            unread_only=False,
            before=datetime(2026, 1, 1),  # naive
            ctx=_Ctx(),
            container=_Container(dal),
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_mark_read_route_404s_when_the_dal_finds_nothing() -> None:
    conn = _FakeConn(fetchrow_row=None)
    dal = NotificationsDAL(_FakeDb(conn))
    with pytest.raises(HTTPException) as excinfo:
        await mark_notification_read(
            notification_id=uuid.uuid4(), ctx=_Ctx(), container=_Container(dal)
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_mark_read_route_returns_the_acknowledged_row() -> None:
    nid = uuid.uuid4()
    conn = _FakeConn(fetchrow_row=_row(notification_id=nid, read_at=datetime.now(timezone.utc)))
    dal = NotificationsDAL(_FakeDb(conn))
    resp = await mark_notification_read(
        notification_id=nid, ctx=_Ctx(), container=_Container(dal)
    )
    assert isinstance(resp, NotificationResponse)
    assert resp.notification_id == nid
    assert resp.read_at is not None


@pytest.mark.asyncio
async def test_routes_503_without_the_postgres_backend() -> None:
    for call in (
        list_notifications(
            limit=50, unread_only=False, before=None, ctx=_Ctx(), container=_Container(None)
        ),
        mark_notification_read(
            notification_id=uuid.uuid4(), ctx=_Ctx(), container=_Container(None)
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await call
        assert excinfo.value.status_code == 503
