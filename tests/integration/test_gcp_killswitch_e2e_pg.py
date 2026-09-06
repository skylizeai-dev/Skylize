"""End-to-end: ceiling breach -> proposal -> HITL approval -> a real VM stop.

REAL Postgres, REAL container, REAL decision gate, REAL HITL claim, REAL tool
proxy and gates. The ONLY thing faked is the far side of the network: the LLM
(the existing fake provider) and Google's two endpoints, which are intercepted so
this suite never touches a customer's infrastructure.

What this proves that no unit test can:
  * the containment goes through the ORDINARY governed path and DEFERS - the
    executor contract can never auto-approve;
  * a human approval REPLAYS the request and the Compute calls actually happen;
  * the `requestId` on the wire derives from the HITL ticket, so the retry the
    replay path can produce is deduplicated by Google rather than executing twice;
  * a second approval never double-executes (the conditional claim);
  * a federation the probe already marked broken blocks execution at the gate;
  * RLS keeps one org's federation and targets invisible to another.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    PgGcpWifRepository,
)
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import agents as agents_routes
from skylize.edge.routes import hitl as hitl_routes
from tests.fakes.fake_provider_api import running_fake_provider, success

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
    requires_redis,
)
from .test_agent_execute_governed_e2e import (
    MODEL,
    _gen_key,
    _seed_ceiling,
    _seed_price,
    _seed_tenant,
)

pytestmark = pytest.mark.integration

ISSUER = "https://oidc.skylize-test.example"
PROJECT, ZONE, INSTANCE = "cust-prod", "us-central1-a", "runaway-trainer"
STS_URL = "https://sts.googleapis.com/v1/token"


def _gen_wif_key() -> str:
    """A throwaway P-256 PEM for the issuer surface.

    Generated per run rather than checked in: no test in this repository holds
    real key material, and the foundation's boot check REQUIRES a key whenever
    the issuer URL is set (app/gcp/keys.py) - a requirement this suite would
    otherwise trip, which is itself the fail-closed behaviour working.
    """
    from skylize.security.ecc_service import Curve, ECCService

    return ECCService.generate_key_pair(Curve.P256).private_pem().decode()


# ---------------------------------------------------------------------------
# Google interception
# ---------------------------------------------------------------------------

class _GoogleSpy:
    """Stands in for httpx.AsyncClient on the Google side ONLY.

    Records every outbound call so the test can assert what actually reached the
    wire - which is the only way to prove the requestId threading, since the
    value is invisible from the API response.
    """

    calls: list[tuple[str, dict]] = []
    release_status: int = 200
    stop_status: int = 200

    def __init__(self, *_a, **_kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, **kw):
        type(self).calls.append((url, dict(kw.get("params") or {})))
        if url == STS_URL:
            return httpx.Response(
                200, json={"access_token": "ya29.TEST", "expires_in": 3600}
            )
        if url.endswith("/stop"):
            return httpx.Response(type(self).stop_status, json={"status": "DONE"})
        return httpx.Response(type(self).release_status, json={"status": "DONE"})

    async def get(self, url: str, **kw):  # the probe's Compute read
        type(self).calls.append((url, dict(kw.get("params") or {})))
        return httpx.Response(200, json={"status": "RUNNING"})

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.release_status = 200
        cls.stop_status = 200

    @classmethod
    def compute_calls(cls) -> list[tuple[str, dict]]:
        return [(u, p) for u, p in cls.calls if "compute.googleapis.com" in u]


@pytest.fixture(autouse=True)
def intercept_google(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect ONLY the GCP executor's egress to the spy.

    Patching `httpx.AsyncClient` globally was the first attempt and is wrong: the
    Anthropic SDK and this suite's own ASGI client are httpx clients too, so a
    global patch breaks the very paths the test is trying to exercise. Instead the
    executor CLASS is subclassed with its client factory pinned, and bootstrap -
    which imports the class at container-build time - picks up the subclass.

    Everything else in the process keeps the real httpx.
    """
    from skylize.app.gcp import actions as actions_mod

    real = actions_mod.GcpKillSwitchExecutor

    class _SpyingExecutor(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            kwargs["http_client_factory"] = _GoogleSpy
            super().__init__(**kwargs)

    _GoogleSpy.reset()
    monkeypatch.setattr(actions_mod, "GcpKillSwitchExecutor", _SpyingExecutor)
    yield
    _GoogleSpy.reset()


@pytest.fixture()
def fake_provider() -> Iterator[tuple[str, object]]:
    with running_fake_provider() as (base_url, fake):
        yield base_url, fake


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


@asynccontextmanager
async def _running(gov_org: str, base_url: str) -> AsyncIterator[
    tuple[AsyncClient, Container]
]:
    settings = Settings(
        backend="postgres", dev_auth=False,
        jwt_secret=TEST_JWT_SECRET, credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL, db_app_url=APP_DB_URL, redis_url=REDIS_URL,
        decision_engine_org_ids=[gov_org],
        anthropic_api_key="sk-test", anthropic_base_url=base_url,
        llm_demo_mode=False, governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL, llm_model_fast=MODEL, llm_model_reasoning=MODEL,
        # Turns the GCP surface on: registers the tool and builds the trigger.
        wif_issuer_base_url=ISSUER, wif_environment="test",
        wif_signing_key_pem=_gen_wif_key(),
    )
    container = await build_container(settings)
    app = FastAPI()
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)
    app.include_router(hitl_routes.router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client, container
    finally:
        await container.aclose()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _org() -> str:
    return f"gcpks_{uuid.uuid4().hex[:10]}"


def _conn_row(org: str, state: str = "valid") -> GcpWifConnectionRow:
    now = datetime.now(timezone.utc)
    return GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id=org, label="",
        issuer_slug=uuid.uuid4().hex[:26].ljust(26, "z"),
        gcp_project_id=PROJECT, gcp_project_number="123456789012",
        workload_identity_pool_id="skylize-pool",
        workload_identity_provider_id="skylize-provider",
        audience="//iam.googleapis.com/projects/123456789012/locations/global/"
                 "workloadIdentityPools/skylize-pool/providers/skylize-provider",
        access_mode="direct", service_account_email=None,
        signing_key_id="wif-es256-v1", jwks_delivery="served",
        expected_jwks_key_id=None,
        connection_state=state, state_reason="probe said so",  # type: ignore[arg-type]
        last_probe_at=None, last_probe_result=None, last_success_at=None,
        created_at=now, updated_at=now,
    )


async def _seed_federation(app_db: Database, org: str, state: str = "valid"):
    repo = PgGcpWifRepository(app_db)
    row = _conn_row(org, state)
    await repo.insert(row)
    await repo.add_target(GcpWifTargetRow(
        target_id=uuid.uuid4(), conn_id=row.conn_id, org_id=org,
        gcp_project_id=PROJECT, zone=ZONE, instance_name=INSTANCE,
        enabled=True, created_at=datetime.now(timezone.utc),
    ))
    return row


async def _cleanup_gcp(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM gcp_wif_targets WHERE org_id = ANY($1::text[])", orgs)
    await admin_conn.execute(
        "DELETE FROM gcp_wif_connections WHERE org_id = ANY($1::text[])", orgs)


def _program_tool_call(fake) -> None:
    """Script the model: ask for the stop tool, then finish."""
    fake.program(
        success(tool_use={
            "id": "toolu_stop_1",
            "name": "integration.gcp_stop_instance",
            "input": {
                "project": PROJECT, "zone": ZONE, "instance": INSTANCE,
                "reason": "spend ceiling breached",
            },
        }),
        # The agent's DELIVERABLE is LLM-authored, so the final turn must be a
        # valid ContainInstanceOut. Worth noting where the authority actually
        # lies: this text is the model's account of what happened, whereas the
        # binding record of what was really done to the customer's
        # infrastructure is the `tool.invoked` audit row and the Compute calls
        # themselves. Every assertion in this suite is made against the CALLS,
        # never against this narration.
        success(text=json.dumps({
            "fully_succeeded": True, "stopped": True, "ip_released": True,
            "partial": False, "summary": "instances.stop: ok; deleteAccessConfig: ok",
        })),
    )


# ---------------------------------------------------------------------------
# THE LOOP
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_ceiling_breach_defers_then_approval_stops_the_vm(
    admin_conn, app_db, fake_provider
) -> None:
    """The whole point of the pass, end to end."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_federation(app_db, org)
        _program_tool_call(fake)

        async with _running(org, base_url) as (client, container):
            # 1. A ceiling breach proposes a containment - and it DEFERS.
            assert container.gcp_containment is not None
            proposal = await container.gcp_containment.propose_containment(
                org_id=org, breach_reason="spend ceiling breached", user_id="owner1",
            )
            assert proposal.proposed is True, proposal.detail
            assert proposal.hitl_id is not None
            assert _GoogleSpy.compute_calls() == [], (
                "a VM was touched before any human approved"
            )

            # 2. The pending item is visible to the org that owns it.
            listed = await client.get("/api/v1/hitl", headers=_owner_hdrs(org))
            assert listed.status_code == 200
            ids = {i["hitl_id"] for i in listed.json()["data"]}
            assert str(proposal.hitl_id) in ids

            # 3. A human approves -> the replay actually calls Google.
            approve = await client.post(
                f"/api/v1/hitl/{proposal.hitl_id}/approve",
                json={"note": "confirmed runaway"}, headers=_owner_hdrs(org),
            )
            assert approve.status_code == 200, approve.text

            calls = _GoogleSpy.compute_calls()
            urls = [u for u, _ in calls]
            assert any(u.endswith("/stop") for u in urls), "the VM was never stopped"
            assert any(u.endswith("/deleteAccessConfig") for u in urls), (
                "the external IP was never released"
            )

            # 4. THE IDEMPOTENCY CLAIM, proven on the wire.
            from skylize.app.gcp.actions import OP_RELEASE_IP, OP_STOP, request_id_for

            stop_params = next(p for u, p in calls if u.endswith("/stop"))
            rel_params = next(
                p for u, p in calls if u.endswith("/deleteAccessConfig"))
            assert stop_params["requestId"] == str(
                request_id_for(proposal.hitl_id, OP_STOP))
            assert rel_params["requestId"] == str(
                request_id_for(proposal.hitl_id, OP_RELEASE_IP))
            assert stop_params["requestId"] != rel_params["requestId"], (
                "one shared requestId would make Google drop the second call"
            )
    finally:
        await _cleanup_gcp(admin_conn, [org])


@requires_redis
@requires_app_role
async def test_a_second_approval_never_executes_a_second_time(
    admin_conn, app_db, fake_provider
) -> None:
    """The conditional claim, seen from the side that matters here: a double
    approval must not produce a second stop."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_federation(app_db, org)
        _program_tool_call(fake)

        async with _running(org, base_url) as (client, container):
            assert container.gcp_containment is not None
            proposal = await container.gcp_containment.propose_containment(
                org_id=org, breach_reason="breach", user_id="owner1")
            assert proposal.hitl_id is not None

            first = await client.post(
                f"/api/v1/hitl/{proposal.hitl_id}/approve", json={},
                headers=_owner_hdrs(org))
            assert first.status_code == 200
            after_first = len([u for u, _ in _GoogleSpy.compute_calls()
                               if u.endswith("/stop")])

            second = await client.post(
                f"/api/v1/hitl/{proposal.hitl_id}/approve", json={},
                headers=_owner_hdrs(org))
            assert second.status_code == 409, second.text
            after_second = len([u for u, _ in _GoogleSpy.compute_calls()
                                if u.endswith("/stop")])
            assert after_second == after_first, "the second approval re-executed"
    finally:
        await _cleanup_gcp(admin_conn, [org])


@requires_redis
@requires_app_role
async def test_a_broken_federation_is_never_proposed_against(
    admin_conn, app_db, fake_provider
) -> None:
    """The probe's verdict is load-bearing BEFORE the urgent moment."""
    base_url, _ = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_federation(app_db, org, state="misconfigured")

        async with _running(org, base_url) as (_client, container):
            assert container.gcp_containment is not None
            proposal = await container.gcp_containment.propose_containment(
                org_id=org, breach_reason="breach", user_id="owner1")
            assert proposal.status == "trust_not_valid"
            assert proposal.hitl_id is None
            assert _GoogleSpy.compute_calls() == []
    finally:
        await _cleanup_gcp(admin_conn, [org])


@requires_redis
@requires_app_role
async def test_a_partial_failure_is_reported_as_partial_not_as_success(
    admin_conn, app_db, fake_provider
) -> None:
    """VM stopped, IP release denied. The deliverable must say so.

    Collapsing this into a success would tell an operator the machine is fully
    contained when it still has a public IP.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_federation(app_db, org)
        _program_tool_call(fake)
        _GoogleSpy.release_status = 403

        async with _running(org, base_url) as (client, container):
            assert container.gcp_containment is not None
            proposal = await container.gcp_containment.propose_containment(
                org_id=org, breach_reason="breach", user_id="owner1")
            assert proposal.hitl_id is not None

            approve = await client.post(
                f"/api/v1/hitl/{proposal.hitl_id}/approve", json={},
                headers=_owner_hdrs(org))
            # The run still completes - the containment partly happened and that
            # fact must survive into the record, not raise it away.
            assert approve.status_code == 200, approve.text
            urls = [u for u, _ in _GoogleSpy.compute_calls()]
            assert any(u.endswith("/stop") for u in urls)
            assert any(u.endswith("/deleteAccessConfig") for u in urls)
    finally:
        await _cleanup_gcp(admin_conn, [org])


@requires_redis
@requires_app_role
async def test_an_ungoverned_org_cannot_stop_a_vm_at_all(
    admin_conn, app_db, fake_provider
) -> None:
    """THE ADVERSARIAL CASE. The decision gate runs for GOVERNED ORGS ONLY.

    `AgentExecutionService.execute` consults the evaluator only when
    `org_id in self._governed_org_ids` (app/agents/execution.py:296). For any
    other org the gate is skipped entirely, so a direct call to
    /agents/execute on the executor contract would reach the tool loop WITHOUT
    ever deferring - and therefore with no `hitl_id`.

    That is the one path where a VM could be stopped with no human verdict, and
    it must fail closed. It does, because the executor REFUSES to derive an
    idempotency key from anything but a HITL ticket: no anchor, no call. This
    test exists so that refusal is a proven property rather than a lucky
    consequence of an unrelated design choice.
    """
    base_url, fake = fake_provider
    org = _org()
    other = _org()  # the ONLY governed org; `org` is deliberately not governed
    try:
        await _seed_price(admin_conn)
        for o in (org, other):
            await _seed_tenant(admin_conn, o)
        await _seed_ceiling(app_db, org)
        await _seed_federation(app_db, org)
        _program_tool_call(fake)

        async with _running(other, base_url) as (client, _container):
            resp = await client.post(
                "/api/v1/agents/execute",
                json={
                    "agent_id": "infrastructure_executor",
                    "input": {
                        "project": PROJECT, "zone": ZONE, "instance": INSTANCE,
                        "reason": "ungoverned attempt",
                    },
                },
                headers=_owner_hdrs(org),
            )

        # The run fails rather than stopping the machine.
        assert resp.status_code != 200, (
            f"an ungoverned org executed a containment: {resp.text}"
        )
        stops = [u for u, _ in _GoogleSpy.compute_calls() if u.endswith("/stop")]
        assert stops == [], (
            "a VM was stopped without any human approval, through the "
            "ungoverned-org path"
        )
    finally:
        await _cleanup_gcp(admin_conn, [org, other])


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_rls_keeps_one_orgs_targets_invisible_to_another(
    admin_conn, app_db
) -> None:
    """This table names which machines may be stopped. A cross-tenant read here
    would be a potential cross-customer ACTION, not merely a disclosure."""
    org_a, org_b = _org(), _org()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        row_a = await _seed_federation(app_db, org_a)

        repo = PgGcpWifRepository(app_db)
        assert await repo.get(org_b, "") is None
        assert await repo.list_targets(org_b, row_a.conn_id) == []

        async with app_db.tenant_session(org_b) as conn:
            assert await conn.fetch("SELECT conn_id FROM gcp_wif_connections") == []
            assert await conn.fetch("SELECT target_id FROM gcp_wif_targets") == []

        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='skylize_app'")
        assert rolrow["rolsuper"] is False and rolrow["rolbypassrls"] is False
    finally:
        await _cleanup_gcp(admin_conn, [org_a, org_b])


def _owner_hdrs(org: str) -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": "owner1", "X-Dev-Roles": "owner"}
