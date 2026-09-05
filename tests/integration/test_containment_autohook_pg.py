"""The auto-hook against a REAL container and a REAL ceiling, on live Postgres.

The unit suite proves the hook's mechanics against a recording double. This one
proves the wiring: a genuine `spend_envelope` breach, through the container's own
`ToolProxy`, reaching the container's own `SpendCeilingContainmentTrigger`, which
runs the real `AgentExecutionService` and leaves a real `hitl_queue` row - with
nobody having called `propose_containment`.

NOTE ON THE TOOL. No shipped tool declares a `ToolSpendProfile` today, so the
breach is driven through the container's real proxy with a synthetic
spend-capable tool definition. Everything downstream of the breach - the trigger,
the decision gate, the HITL write, the database - is the real thing.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    PgGcpWifRepository,
)
from skylize.tools.base import ToolSpendDeferredToHuman, ToolSpendProfile
from skylize.tools.proxy import ToolProxy
from tests.fakes.fake_provider_api import running_fake_provider

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    requires_app_role,
)
from .test_agent_execute_governed_e2e import MODEL, _gen_key
from .test_gcp_killswitch_e2e_pg import (
    INSTANCE,
    ISSUER,
    PROJECT,
    ZONE,
    _GoogleSpy,
    _gen_wif_key,
)

pytestmark = pytest.mark.integration

PRINCIPAL = "person_alice"


@pytest.fixture(autouse=True)
def intercept_google(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Nothing in this module may reach Google, even by accident."""
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
async def _container(gov_org: str, base_url: str) -> AsyncIterator[Container]:
    settings = Settings(
        backend="postgres", dev_auth=False,
        jwt_secret=TEST_JWT_SECRET, credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL, db_app_url=APP_DB_URL, redis_url=REDIS_URL,
        decision_engine_org_ids=[gov_org],
        anthropic_api_key="sk-test", anthropic_base_url=base_url,
        llm_demo_mode=False, governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL, llm_model_fast=MODEL, llm_model_reasoning=MODEL,
        wif_issuer_base_url=ISSUER, wif_environment="test",
        wif_signing_key_pem=_gen_wif_key(),
    )
    container = await build_container(settings)
    try:
        yield container
    finally:
        await container.aclose()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

async def _seed_org_with_tiny_ceiling(admin_conn, org: str) -> None:
    now = datetime.now(timezone.utc)
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )
    await admin_conn.execute(
        """INSERT INTO spend_envelope (org_id, principal_id, currency,
             ceiling_minor, period_start, period_end, over_ceiling_behavior)
           VALUES ($1,$2,'USD',$3,$4,$5,'defer_to_human')""",
        org, PRINCIPAL, 100,  # 100 cents: the reservation below blows straight past
        now - timedelta(days=1), now + timedelta(days=30),
    )


async def _seed_federation(app_db: Database, org: str) -> GcpWifConnectionRow:
    now = datetime.now(timezone.utc)
    row = GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id=org, label="",
        issuer_slug=uuid.uuid4().hex[:26].ljust(26, "z"),
        gcp_project_id=PROJECT, gcp_project_number="123456789012",
        workload_identity_pool_id="pool", workload_identity_provider_id="prov",
        audience="//iam.googleapis.com/projects/1/locations/global/x/y",
        access_mode="direct", service_account_email=None,
        signing_key_id="wif-es256-v1", jwks_delivery="served",
        expected_jwks_key_id=None, connection_state="valid", state_reason=None,
        last_probe_at=None, last_probe_result=None, last_success_at=None,
        created_at=now, updated_at=now,
    )
    repo = PgGcpWifRepository(app_db)
    await repo.insert(row)
    await repo.add_target(GcpWifTargetRow(
        target_id=uuid.uuid4(), conn_id=row.conn_id, org_id=org,
        gcp_project_id=PROJECT, zone=ZONE, instance_name=INSTANCE,
        enabled=True, created_at=now,
    ))
    return row


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    for table in ("gcp_wif_targets", "gcp_wif_connections", "spend_reservation",
                  "spend_envelope"):
        await admin_conn.execute(
            f"DELETE FROM {table} WHERE org_id = ANY($1::text[])", orgs)


async def _pending_containments(app_db: Database, org: str) -> int:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT request_json FROM hitl_queue WHERE org_id=$1", org)
    return sum(
        1 for r in rows
        if r["request_json"] and "infrastructure_executor" in str(r["request_json"])
    )


# ---------------------------------------------------------------------------
# Driving a real breach through the container's own proxy
# ---------------------------------------------------------------------------

class _SpendInput:
    amount_minor = 999_999  # far past the 100-cent ceiling seeded above


class _OnBehalfOf:
    principal_id = PRINCIPAL


class _Token:
    token_id = uuid.uuid4()
    token_version = "1.1"
    on_behalf_of = _OnBehalfOf()


class _SpendTool:
    tool_id = "integration.synthetic_spend_tool"
    spend = ToolSpendProfile(currency="USD", amount_field="amount_minor")


def _real_contract():
    """A REAL registered contract, not a stub.

    The first version used a hand-rolled object and it lacked `authority_level`,
    which `_audit_call` reads - so the denial audit blew up inside the hook. A
    real contract cannot drift away from what the proxy actually needs.
    """
    from skylize.contracts.registry import MVP_REGISTRY

    return MVP_REGISTRY.resolve("director_growth")


async def _breach(container: Container, org: str) -> None:
    """Drive a genuine ceiling breach through the container's REAL ToolProxy."""
    proxy = container.agent_execution._tools  # type: ignore[attr-defined]
    assert isinstance(proxy, ToolProxy)
    with pytest.raises(ToolSpendDeferredToHuman):
        await ToolProxy._reserve_spend(
            proxy, tool=_SpendTool(), validated_input=_SpendInput(),  # type: ignore[arg-type]
            contract=_real_contract(), org_id=org,
            correlation_id=uuid.uuid4(), governance_token=_Token(),  # type: ignore[arg-type]
        )
    # Let the fire-and-forget proposal finish before asserting.
    while proxy._containment_tasks:  # type: ignore[attr-defined]
        await asyncio.gather(
            *list(proxy._containment_tasks),  # type: ignore[attr-defined]
            return_exceptions=True,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@requires_app_role
async def test_a_real_ceiling_breach_queues_a_containment_with_nobody_calling_it(
    admin_conn, app_db, fake_provider
) -> None:
    """THE POINT OF THIS PASS.

    Nothing in this test calls `propose_containment`. A tool call breaches a real
    `spend_envelope`, and a real HITL row for `infrastructure_executor` exists
    afterwards.
    """
    base_url, _ = fake_provider
    org = f"hook_{uuid.uuid4().hex[:10]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        await _seed_federation(app_db, org)

        async with _container(org, base_url) as container:
            assert await _pending_containments(app_db, org) == 0
            await _breach(container, org)
            assert await _pending_containments(app_db, org) == 1, (
                "a ceiling breach did not auto-queue a containment"
            )
            # And nothing was stopped - the proposal awaits a human.
            assert _GoogleSpy.compute_calls() == []
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_no_federation_means_a_breach_queues_nothing(
    admin_conn, app_db, fake_provider
) -> None:
    """An org with no GCP connection must be unaffected by the hook.

    This is the case that keeps the auto-hook from becoming a tax on every
    customer: no federation, no proposal, no queue row, no behaviour change.
    """
    base_url, _ = fake_provider
    org = f"hook_nogcp_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        # deliberately NO _seed_federation

        async with _container(org, base_url) as container:
            await _breach(container, org)
            assert await _pending_containments(app_db, org) == 0
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_a_broken_federation_queues_nothing(
    admin_conn, app_db, fake_provider
) -> None:
    """The probe's verdict still gates the auto-hook, not just the manual path."""
    base_url, _ = fake_provider
    org = f"hook_broken_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        row = await _seed_federation(app_db, org)
        await PgGcpWifRepository(app_db).set_connection_state(
            conn_id=row.conn_id, org_id=org, state="misconfigured",
            reason="IAM binding removed",
        )

        async with _container(org, base_url) as container:
            await _breach(container, org)
            assert await _pending_containments(app_db, org) == 0
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_repeated_breaches_queue_exactly_one_containment(
    admin_conn, app_db, fake_provider
) -> None:
    """A runaway agent breaches on every call; the reviewer gets one decision."""
    base_url, _ = fake_provider
    org = f"hook_burst_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        await _seed_federation(app_db, org)

        async with _container(org, base_url) as container:
            for _ in range(4):
                await _breach(container, org)
            assert await _pending_containments(app_db, org) == 1
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_the_explicit_entry_point_still_works(
    admin_conn, app_db, fake_provider
) -> None:
    """The manual override survives the auto-hook.

    An operator must still be able to propose a containment without waiting for
    a breach - the auto-hook adds a caller, it does not replace the seam.
    """
    base_url, _ = fake_provider
    org = f"hook_manual_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        await _seed_federation(app_db, org)

        async with _container(org, base_url) as container:
            assert container.gcp_containment is not None
            result = await container.gcp_containment.propose_containment(
                org_id=org, breach_reason="operator judgement", user_id="owner1",
            )
            assert result.proposed is True
            assert await _pending_containments(app_db, org) == 1
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_the_proxy_and_the_container_share_one_trigger_instance(
    admin_conn, app_db, fake_provider
) -> None:
    """The late-binding wiring actually happened.

    If `set_containment_trigger` were ever dropped from bootstrap the auto-hook
    would silently become a no-op - the proxy would hold None and every breach
    would pass unremarked. This asserts the assignment, not just its effect.
    """
    base_url, _ = fake_provider
    org = f"hook_wire_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_org_with_tiny_ceiling(admin_conn, org)
        async with _container(org, base_url) as container:
            proxy = container.agent_execution._tools  # type: ignore[attr-defined]
            assert proxy._containment is not None, (
                "bootstrap did not late-bind the containment trigger"
            )
            assert proxy._containment is container.gcp_containment
    finally:
        await _cleanup(admin_conn, [org])
