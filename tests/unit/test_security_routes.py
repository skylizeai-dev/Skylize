"""Security activity route + its DAL read — counts and rows, and NO score.

TWO THINGS ARE PINNED HERE. First the ordinary mechanics: RBAC, the window, the
zero-filled result vocabulary, the truncation flag, and that the DAL read runs
inside the caller's own tenant session. Second, and the reason this file is
worth more than its mechanics, the ABSENCES: the console's security screen was
mocked with a posture score, an eight-row control inventory and four compliance
badges, and none of the three has any source of truth in this backend. A test
that asserts a field is missing looks strange until somebody adds the field back
for the sake of a nicer-looking screen; then it is the only thing standing
between a mock and a claim.

The SQL these fakes stand in for is exercised against real Postgres in
tests/integration/test_security_activity_pg.py — counting rows and enforcing RLS
are precisely what a fake cannot prove.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from skylize.dal.activity_signals import (
    ATTENTION_RESULTS,
    AUDIT_RESULTS,
    AuditActivitySignalDAL,
)
from skylize.edge.gateway import create_app

_OWNER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_ADMIN_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u3", "X-Dev-Roles": "admin"}
_VIEWER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"}

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _event_row(result: str, *, offset_minutes: int = 0) -> dict[str, object]:
    return {
        "event_id": uuid.uuid4(),
        "correlation_id": uuid.uuid4(),
        "action_type": "agent.executed",
        "result": result,
        "occurred_at": NOW - timedelta(minutes=offset_minutes),
        "source_agent_id": "hook_generator_agent",
        "authority_level": "L1",
        "governance_token_id": uuid.uuid4(),
        "result_reason": "authority ceiling exceeded",
    }


class _FakeConn:
    def __init__(self, count_row: dict[str, object], event_rows: list[dict[str, object]]):
        self._count_row = count_row
        self._event_rows = event_rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
        self.queries.append((query, args))
        return self._count_row

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append((query, args))
        limit = args[4] if len(args) > 4 else len(self._event_rows)
        assert isinstance(limit, int)
        return self._event_rows[:limit]


class _FakeDb:
    def __init__(
        self,
        count_row: dict[str, object] | None = None,
        event_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.conn = _FakeConn(
            count_row
            or {
                "total": 10,
                "n_success": 6,
                "n_denied": 3,
                "n_escalated": 0,
                "n_failed": 1,
                "n_action_types": 4,
                "n_agents": 2,
            },
            event_rows if event_rows is not None else [_event_row("denied")],
        )
        self.bound_orgs: list[str] = []

    @asynccontextmanager
    async def tenant_session(self, org_id: str):
        self.bound_orgs.append(org_id)
        yield self.conn


# ---------------------------------------------------------------------------
# DAL — the governance-event read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_event_read_runs_in_the_callers_tenant_session() -> None:
    """RLS is the tenant boundary here. A read outside `tenant_session` would
    run with no `skylize.org_id` set and the policy would match nothing — or,
    worse, would match everything if the role ever gained BYPASSRLS."""
    db = _FakeDb()
    dal = AuditActivitySignalDAL(db)
    await dal.read_recent_governance_events(
        org_id="org_7", since=NOW - timedelta(hours=1), until=NOW
    )
    assert db.bound_orgs == ["org_7"]


@pytest.mark.asyncio
async def test_the_event_read_excludes_success_and_orders_newest_first() -> None:
    db = _FakeDb()
    dal = AuditActivitySignalDAL(db)
    since = NOW - timedelta(hours=1)
    await dal.read_recent_governance_events(org_id="org_a", since=since, until=NOW)
    query, args = db.conn.queries[0]
    assert "ORDER BY occurred_at DESC" in query
    # Half-open, exactly like read_window: the same window from both angles.
    assert "occurred_at >= $2" in query and "occurred_at < $3" in query
    assert args[1] is since and args[2] is NOW
    assert args[3] == list(ATTENTION_RESULTS)
    assert "success" not in args[3], (
        "a feed of successes is a throughput report, not a governance one"
    )


@pytest.mark.asyncio
async def test_the_event_read_projects_no_content_hashes() -> None:
    """`inputs_hash`/`outputs_hash` are SHA-256 CONTENT hashes, not signatures.
    Shown under the word 'security' they would read as proof of who acted."""
    db = _FakeDb()
    dal = AuditActivitySignalDAL(db)
    await dal.read_recent_governance_events(
        org_id="org_a", since=NOW - timedelta(hours=1), until=NOW
    )
    query, _ = db.conn.queries[0]
    assert "inputs_hash" not in query and "outputs_hash" not in query


@pytest.mark.asyncio
async def test_an_empty_window_is_an_empty_list_not_an_error() -> None:
    db = _FakeDb(event_rows=[])
    dal = AuditActivitySignalDAL(db)
    rows = await dal.read_recent_governance_events(
        org_id="org_a", since=NOW - timedelta(hours=1), until=NOW
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@contextmanager
def _client_with(dal: object | None):
    """The REAL memory-backend container with only `security_activity_dal`
    swapped.

    A hand-rolled fake container was the obvious move and the wrong one: auth
    resolves `container.settings` and `container.api_keys` before the route body
    ever runs, so a stub narrow enough to be readable turns every RBAC test into
    an AttributeError that looks like a 500. Overriding the one field on the
    real container keeps the auth path genuine — which is the half of this file
    that has to be genuine — while the DAL underneath stays a fake, because the
    memory backend has no `audit_log` to read.
    """
    with TestClient(create_app()) as client:
        client.app.state.container.security_activity_dal = dal
        yield client


def test_requires_admin_or_owner() -> None:
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        assert c.get("/api/v1/security/activity", headers=_VIEWER_A).status_code == 403
        assert c.get("/api/v1/security/activity", headers=_OWNER_A).status_code == 200
        assert c.get("/api/v1/security/activity", headers=_ADMIN_A).status_code == 200


def test_memory_backend_says_so_rather_than_reporting_zero() -> None:
    """A zeroed response would assert that nothing happened. The truth on the
    memory backend is that nothing was RECORDED — there is no audit_log."""
    with _client_with(None) as c:
        resp = c.get("/api/v1/security/activity", headers=_OWNER_A)
    assert resp.status_code == 503
    assert "postgres" in resp.json()["detail"]


def test_the_read_is_scoped_to_the_authenticated_org_not_a_parameter() -> None:
    db = _FakeDb()
    with _client_with(AuditActivitySignalDAL(db)) as c:
        resp = c.get(
            "/api/v1/security/activity?org_id=org_b", headers=_OWNER_A
        )
    assert resp.status_code == 200
    assert set(db.bound_orgs) == {"org_a"}, (
        "org_id must come from the RequestContext, never from the query string"
    )


def test_counts_are_zero_filled_across_the_whole_result_vocabulary() -> None:
    """A missing key and a zero are indistinguishable to a reader. `escalated`
    is 0 in the fixture and must still be present."""
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    assert set(body["by_result"]) == set(AUDIT_RESULTS)
    assert body["by_result"]["escalated"] == 0
    assert body["by_result"]["denied"] == 3
    assert body["total_actions"] == 10


def test_the_window_is_reported_and_honours_the_requested_hours() -> None:
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get(
            "/api/v1/security/activity?window_hours=6", headers=_OWNER_A
        ).json()
    start = datetime.fromisoformat(body["window_start"])
    end = datetime.fromisoformat(body["window_end"])
    assert end - start == timedelta(hours=6)


def test_truncation_is_declared_rather_than_silently_hiding_rows() -> None:
    rows = [_event_row("denied", offset_minutes=i) for i in range(5)]
    with _client_with(AuditActivitySignalDAL(_FakeDb(event_rows=rows))) as c:
        body = c.get("/api/v1/security/activity?limit=2", headers=_OWNER_A).json()
    assert len(body["recent_events"]) == 2
    assert body["recent_events_truncated"] is True
    # ...and the COUNTS are never truncated, so the sample can always be
    # recognised as a sample.
    assert body["total_actions"] == 10


def test_an_untruncated_feed_says_so() -> None:
    with _client_with(AuditActivitySignalDAL(_FakeDb(event_rows=[_event_row("failed")]))) as c:
        body = c.get("/api/v1/security/activity?limit=50", headers=_OWNER_A).json()
    assert len(body["recent_events"]) == 1
    assert body["recent_events_truncated"] is False


def test_events_carry_provenance_and_never_a_content_hash() -> None:
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    event = body["recent_events"][0]
    assert event["result"] == "denied"
    assert event["governance_token_id"] is not None
    assert event["source_agent_id"] == "hook_generator_agent"
    assert "inputs_hash" not in event and "outputs_hash" not in event
    # `source_agent_id` is an AGENT, never a person — there is no actor field.
    assert "actor" not in event and "user_id" not in event


def test_out_of_range_window_and_limit_are_refused() -> None:
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        assert (
            c.get("/api/v1/security/activity?window_hours=0", headers=_OWNER_A).status_code
            == 422
        )
        assert (
            c.get("/api/v1/security/activity?limit=500", headers=_OWNER_A).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# The absences — the whole point of the scope decision
# ---------------------------------------------------------------------------


def test_the_response_carries_no_security_score() -> None:
    """The mock hardcoded `secScore = 94`. No scoring methodology exists in this
    system — not a table, not an ADR, not a decision. A number here would be
    invented in the request path and then read as a measurement."""
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    for forbidden in ("score", "security_score", "sec_score", "posture_score", "grade"):
        assert forbidden not in body, f"{forbidden!r} has no source of truth"


def test_the_response_carries_no_control_inventory() -> None:
    """The mock listed SSO, SCIM, encryption-at-rest, data-residency, HITL,
    PII-redaction, sandbox-isolation and pen-test. Nothing records the state of
    any of them."""
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    for forbidden in ("controls", "control_status", "checks", "posture"):
        assert forbidden not in body, f"{forbidden!r} has no source of truth"


def test_the_response_carries_no_compliance_badges() -> None:
    """SOC 2, ISO 27001, GDPR and HIPAA-READY are assertions about audits an
    auditor performed. This system cannot produce one, and a badge emitted from
    an API is a false claim about a third party."""
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    for forbidden in ("compliance", "badges", "certifications", "attestations"):
        assert forbidden not in body, f"{forbidden!r} has no source of truth"


def test_every_response_field_traces_to_an_audit_log_column_or_a_count() -> None:
    """The positive form of the three tests above: an exhaustive allow-list, so
    a new invented field fails here even if it avoids every name guessed above."""
    with _client_with(AuditActivitySignalDAL(_FakeDb())) as c:
        body = c.get("/api/v1/security/activity", headers=_OWNER_A).json()
    assert set(body) == {
        "window_start",
        "window_end",
        "total_actions",
        "by_result",
        "distinct_action_types",
        "distinct_agents",
        "recent_events",
        "recent_events_truncated",
    }
    assert set(body["recent_events"][0]) == {
        "event_id",
        "correlation_id",
        "action_type",
        "result",
        "occurred_at",
        "source_agent_id",
        "authority_level",
        "governance_token_id",
        "result_reason",
    }
