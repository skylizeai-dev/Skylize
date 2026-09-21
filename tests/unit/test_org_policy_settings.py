"""Org policy settings — the parts provable without a database.

The RLS and migration-shape guarantees need real Postgres and live in
``tests/integration/test_org_policy_settings_pg.py``, which SKIPS without
``SKYLIZE_TEST_DB_URL`` / ``SKYLIZE_TEST_APP_DB_URL``. These tests run in the
unit gate unconditionally, so the fail-closed defaults are proven on every CI
run rather than only on a Postgres-equipped one.

Proven here:
  * a missing row resolves to the fail-closed defaults (every guardrail at its
    safest value, retention at the documented compliance floor), and
    ``read_settings`` has no way to return anything else when the query comes
    back empty;
  * an explicit row, including one that matches the defaults, reads back
    exactly and stays distinguishable from "never configured" via
    ``read_configured_settings``;
  * ``set_settings`` rejects a ``retention_days`` outside the CHECK bounds
    BEFORE touching the DB;
  * the GET route reports the fail-closed settings with ``configured=False``
    and every guardrail's real (today: all False) ``enforced`` flag;
  * the PUT route is validated by the Pydantic schema for out-of-range
    retention;
  * both routes 503 on the memory backend rather than inventing settings.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from skylize.dal.org_policy_settings import (
    DEFAULT_POLICY_SETTINGS,
    RETENTION_MAX_DAYS,
    RETENTION_MIN_DAYS,
    OrgPolicySettings,
    OrgPolicySettingsDAL,
)
from skylize.edge.routes.org_policy_settings import (
    OrgPolicySettingsResponse,
    SetOrgPolicySettingsRequest,
    get_org_policy_settings,
    set_org_policy_settings,
)


class _FakeConn:
    """Returns one canned row for the single fetchrow the DAL issues."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.queries.append((query, args))
        return self.row

    async def execute(self, query: str, *args: object) -> None:
        self.queries.append((query, args))


class _FakeDb:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.conn = _FakeConn(row)
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
        self.policy_settings_dal = dal
        self.audit = audit


_EXPLICIT_ROW = {
    "spend_cap_alert_enabled": False,
    "email_domain_restriction_enabled": False,
    "pii_redaction_enabled": False,
    "silent_fallback_suppressed": False,
    "retention_days": 3000,
}
_EXPLICIT_SETTINGS = OrgPolicySettings(
    spend_cap_alert_enabled=False,
    email_domain_restriction_enabled=False,
    pii_redaction_enabled=False,
    silent_fallback_suppressed=False,
    retention_days=3000,
)


# ---------------------------------------------------------------------------
# DAL — fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_row_reads_as_fail_closed_defaults() -> None:
    dal = OrgPolicySettingsDAL(_FakeDb(None))
    assert await dal.read_settings("org-1") == DEFAULT_POLICY_SETTINGS


def test_every_guardrail_default_is_its_safest_value() -> None:
    """Pinned against the constants, so moving a default is a deliberate edit."""
    assert DEFAULT_POLICY_SETTINGS.spend_cap_alert_enabled is True
    assert DEFAULT_POLICY_SETTINGS.email_domain_restriction_enabled is True
    assert DEFAULT_POLICY_SETTINGS.pii_redaction_enabled is True
    assert DEFAULT_POLICY_SETTINGS.silent_fallback_suppressed is True


def test_retention_default_is_the_documented_compliance_floor() -> None:
    assert DEFAULT_POLICY_SETTINGS.retention_days == RETENTION_MIN_DAYS
    assert RETENTION_MIN_DAYS == 2555


@pytest.mark.asyncio
async def test_missing_row_is_distinguishable_from_explicit_matching_row() -> None:
    unset = OrgPolicySettingsDAL(_FakeDb(None))
    default_row = {
        "spend_cap_alert_enabled": True,
        "email_domain_restriction_enabled": True,
        "pii_redaction_enabled": True,
        "silent_fallback_suppressed": True,
        "retention_days": RETENTION_MIN_DAYS,
    }
    chosen = OrgPolicySettingsDAL(_FakeDb(default_row))
    # Same values to act on...
    assert await unset.read_settings("org-1") == await chosen.read_settings("org-1")
    # ...different facts about whether anyone chose them.
    assert await unset.read_configured_settings("org-1") is None
    assert await chosen.read_configured_settings("org-1") == DEFAULT_POLICY_SETTINGS


@pytest.mark.asyncio
async def test_explicit_row_reads_back_exactly() -> None:
    dal = OrgPolicySettingsDAL(_FakeDb(_EXPLICIT_ROW))
    assert await dal.read_settings("org-1") == _EXPLICIT_SETTINGS


@pytest.mark.asyncio
async def test_read_is_bound_to_the_callers_org() -> None:
    db = _FakeDb(_EXPLICIT_ROW)
    dal = OrgPolicySettingsDAL(db)
    await dal.read_settings("org-7")
    assert db.bound_orgs == ["org-7"], "the read must run inside that org's session"


@pytest.mark.asyncio
async def test_read_resolves_the_latest_row_at_or_before_the_instant() -> None:
    db = _FakeDb(_EXPLICIT_ROW)
    dal = OrgPolicySettingsDAL(db)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await dal.read_settings("org-1", at)
    query, args = db.conn.queries[0]
    assert "effective_from <= $2" in query
    assert "ORDER BY effective_from DESC" in query
    assert args[1] == at


@pytest.mark.asyncio
async def test_set_settings_rejects_retention_below_the_floor_before_touching_db() -> None:
    db = _FakeDb(None)
    dal = OrgPolicySettingsDAL(db)
    with pytest.raises(ValueError, match="retention_days must be between"):
        await dal.set_settings(
            org_id="org-1",
            spend_cap_alert_enabled=True,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=True,
            silent_fallback_suppressed=True,
            retention_days=RETENTION_MIN_DAYS - 1,
            audit=_FakeAudit(),
            correlation_id=uuid.uuid4(),
        )
    assert db.conn.queries == [], "a rejected value must not reach the database"


@pytest.mark.asyncio
async def test_set_settings_rejects_retention_above_the_ceiling() -> None:
    db = _FakeDb(None)
    dal = OrgPolicySettingsDAL(db)
    with pytest.raises(ValueError, match="retention_days must be between"):
        await dal.set_settings(
            org_id="org-1",
            spend_cap_alert_enabled=True,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=True,
            silent_fallback_suppressed=True,
            retention_days=RETENTION_MAX_DAYS + 1,
            audit=_FakeAudit(),
            correlation_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_set_settings_records_a_governance_audit_action() -> None:
    audit = _FakeAudit()
    dal = OrgPolicySettingsDAL(_FakeDb(None))
    result = await dal.set_settings(
        org_id="org-1",
        spend_cap_alert_enabled=False,
        email_domain_restriction_enabled=False,
        pii_redaction_enabled=False,
        silent_fallback_suppressed=False,
        retention_days=3000,
        audit=audit,
        correlation_id=uuid.uuid4(),
    )
    assert result == _EXPLICIT_SETTINGS
    assert len(audit.records) == 1
    assert audit.records[0]["action_type"] == "governance.org_policy_settings_set"
    assert audit.records[0]["org_id"] == "org-1"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reports_the_fail_closed_settings_as_unconfigured() -> None:
    dal = OrgPolicySettingsDAL(_FakeDb(None))
    resp = await get_org_policy_settings(ctx=_Ctx(), container=_Container(dal))
    assert isinstance(resp, OrgPolicySettingsResponse)
    assert resp.configured is False
    assert resp.spend_cap_alert_enabled.value is True
    assert resp.retention_days == RETENTION_MIN_DAYS


@pytest.mark.asyncio
async def test_get_reports_no_guardrail_as_enforced_today() -> None:
    """Honesty gate: none of the four guardrails is wired to enforcement yet."""
    dal = OrgPolicySettingsDAL(_FakeDb(None))
    resp = await get_org_policy_settings(ctx=_Ctx(), container=_Container(dal))
    assert resp.spend_cap_alert_enabled.enforced is False
    assert resp.email_domain_restriction_enabled.enforced is False
    assert resp.pii_redaction_enabled.enforced is False
    assert resp.silent_fallback_suppressed.enforced is False


@pytest.mark.asyncio
async def test_get_reports_an_explicit_row_as_configured() -> None:
    dal = OrgPolicySettingsDAL(_FakeDb(_EXPLICIT_ROW))
    resp = await get_org_policy_settings(ctx=_Ctx(), container=_Container(dal))
    assert resp.configured is True
    assert resp.spend_cap_alert_enabled.value is False
    assert resp.retention_days == 3000


@pytest.mark.asyncio
async def test_put_sets_and_returns_the_new_settings() -> None:
    audit = _FakeAudit()
    dal = OrgPolicySettingsDAL(_FakeDb(None))
    resp = await set_org_policy_settings(
        body=SetOrgPolicySettingsRequest(
            spend_cap_alert_enabled=False,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=False,
            silent_fallback_suppressed=True,
            retention_days=2600,
        ),
        ctx=_Ctx(),
        container=_Container(dal, audit),
    )
    assert resp.configured is True
    assert resp.spend_cap_alert_enabled.value is False
    assert resp.email_domain_restriction_enabled.value is True
    assert resp.retention_days == 2600
    assert audit.records[0]["action_type"] == "governance.org_policy_settings_set"


@pytest.mark.asyncio
async def test_routes_503_without_the_postgres_backend() -> None:
    """No DAL means no policy store. Reporting made-up settings would be worse
    than an error: the caller would act on values nothing wrote."""
    for call in (
        get_org_policy_settings(ctx=_Ctx(), container=_Container(None)),
        set_org_policy_settings(
            body=SetOrgPolicySettingsRequest(
                spend_cap_alert_enabled=True,
                email_domain_restriction_enabled=True,
                pii_redaction_enabled=True,
                silent_fallback_suppressed=True,
                retention_days=RETENTION_MIN_DAYS,
            ),
            ctx=_Ctx(),
            container=_Container(None),
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await call
        assert excinfo.value.status_code == 503


def test_put_rejects_retention_outside_the_schema_bounds() -> None:
    """The request schema is the first of two gates; the DAL and the table's
    CHECK constraint are the other two."""
    with pytest.raises(ValidationError):
        SetOrgPolicySettingsRequest(
            spend_cap_alert_enabled=True,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=True,
            silent_fallback_suppressed=True,
            retention_days=RETENTION_MIN_DAYS - 1,
        )
    with pytest.raises(ValidationError):
        SetOrgPolicySettingsRequest(
            spend_cap_alert_enabled=True,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=True,
            silent_fallback_suppressed=True,
            retention_days=RETENTION_MAX_DAYS + 1,
        )
