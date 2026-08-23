"""Deferred co-work action -> human approval, against REAL Postgres.

Q3, both halves, measured:

  (a) THE BINDING PERSISTS. The principal whose authority the turn ran under is
      captured in `hitl_queue.request_json` at defer time -- asserted by reading
      the column back out of the database, not by trusting the object that wrote
      it. It goes in request_json and not proposal_json because
      HitlQueueService.approve rebuilds the execute() call purely from the
      envelope (app/hitl/service.py) -- a binding in proposal_json would be
      durable and unreachable.

  (b) AUTHORITY IS RECOMPILED, NOT RESTORED. Only the principal ID is stored, so
      the approval has no way to express "they used to be allowed". mint() calls
      _gate_principal_scope -> snapshot_for -> compile_authority against the
      grants as they are AT APPROVAL TIME, through PgPrincipalRepository. Revoking
      the grant, or suspending the human, BETWEEN defer and approve therefore
      makes the approval REFUSE -- and it is refused here by really deleting the
      row from really Postgres, not by simulating a failure.

WHY THIS DOES NOT CONFLICT WITH "APPROVAL SKIPS THE EVALUATOR". The gate at
app/agents/execution.py skips the DecisionEvaluator when a HitlApprovalContext is
present -- re-running it would defer forever. mint() carries no such condition and
runs on every execute(). The human verdict resolves the POLICY question the
evaluator deferred; it does not vouch for authority that has since been withdrawn.
Two different questions, answered by two different components.

A cowork_agent VARIANT carrying FIRST_EXTERNAL_LAUNCH is registered for these
cases, because that is the one branch stage 2.5 still defers on unconditionally
(`defers_on_trigger_presence=False` deliberately stops the others) -- so this is
also the live proof that the opt-out did not disable the external-publication
defer. The registry is restored in a finally.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.audit.service import AuditService
from skylize.app.hitl.service import HitlReplayInvalid
from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.contracts.base import HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.edge.errors import install_error_handlers
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import cowork as cowork_routes
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.hitl import HitlReplayEnvelope

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
)
from ..fakes.fake_provider_api.app import success
from ..fakes.fake_provider_api.server import running_fake_provider

pytestmark = pytest.mark.integration

MODEL = "fake-cowork-replay"
SYNTH_RATE = 1_000
BIG_CEILING = 10_000_000_000
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
PRINCIPAL = "employee_devon"
MANIFEST = ("llm.generate", "memory.search")
TURN = {"message": "publish the launch announcement"}


def _org() -> str:
    return f"replay_{uuid.uuid4().hex[:10]}"


def _headers(org: str) -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": PRINCIPAL, "X-Dev-Roles": "owner"}


def _gen_key() -> str:
    key = ec.generate_private_key(ec.SECP384R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


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


@pytest.fixture()
def defers_externally() -> Iterator[None]:
    """Register a cowork_agent variant that stage 2.5 still defers on."""
    original = MVP_REGISTRY.resolve("cowork_agent")
    MVP_REGISTRY.register_contract(
        original.model_copy(
            update={"human_in_loop_triggers": [HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH]}
        )
    )
    try:
        yield
    finally:
        MVP_REGISTRY.register_contract(original)


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_price(admin_conn) -> None:
    await admin_conn.execute(
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, 'anthropic', $1, $2, $2, 'USD', 1, $3)
        ON CONFLICT DO NOTHING
        """,
        MODEL, SYNTH_RATE, _EPOCH,
    )


async def _seed_ceiling(app_db: Database, org: str) -> None:
    await OrgSpendCeilingDAL(app_db).set_ceiling(
        org_id=org, billing_period=_period(), ceiling_micros=BIG_CEILING,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _seed_principal(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO principal (principal_id, org_id, display_name, authority_level) "
        "VALUES ($1,$2,$3,'executive')",
        PRINCIPAL, org, "Devon",
    )
    for scope in MANIFEST:
        await admin_conn.execute(
            "INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by) "
            "VALUES ($1,$2,$3,'position','test')",
            org, PRINCIPAL, scope,
        )


async def _revoke_all_grants(admin_conn, org: str) -> int:
    """Really delete the rows. This is the 'descoped between defer and approve'
    event, not a stub of it."""
    status = await admin_conn.execute(
        "DELETE FROM principal_grant WHERE org_id=$1 AND principal_id=$2", org, PRINCIPAL
    )
    return int(str(status).split()[-1])


async def _suspend_principal(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "UPDATE principal SET suspended_at=now() WHERE org_id=$1 AND principal_id=$2",
        org, PRINCIPAL,
    )


async def _cleanup(admin_conn, org: str) -> None:
    await admin_conn.execute("TRUNCATE ai_cost_ledger")
    await admin_conn.execute("TRUNCATE work_journal")
    for sql in (
        "DELETE FROM journal_cursor WHERE org_id=$1",
        "DELETE FROM principal_grant WHERE org_id=$1",
        "DELETE FROM principal WHERE org_id=$1",
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
        "DELETE FROM deliverables WHERE org_id=$1",
        "DELETE FROM org_spend_ceiling WHERE org_id=$1",
        "DELETE FROM governance_tokens WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        await admin_conn.execute(sql, org)
    await admin_conn.execute("DELETE FROM model_pricing WHERE model=$1", MODEL)


async def _build(base_url: str, org: str):
    settings = Settings(
        backend="postgres", dev_auth=False, jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL, db_app_url=APP_DB_URL, redis_url=REDIS_URL,
        decision_engine_org_ids=[org],
        anthropic_api_key="sk-test", anthropic_base_url=base_url,
        llm_demo_mode=False, governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL, llm_model_fast=MODEL, llm_model_reasoning=MODEL,
    )
    container = await build_container(settings)
    app = FastAPI()
    install_error_handlers(app)
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(cowork_routes.router)
    return app, container


async def _defer_a_turn(app, org: str) -> uuid.UUID:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        r = await client.post("/api/v1/cowork/turns", json=TURN, headers=_headers(org))
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "deferred_to_human"
    return uuid.UUID(r.json()["hitl_id"])


async def _request_json(app_db: Database, org: str, hitl_id: uuid.UUID) -> dict:
    async with app_db.tenant_session(org) as conn:
        raw = await conn.fetchval(
            "SELECT request_json FROM hitl_queue WHERE hitl_id=$1", hitl_id
        )
    return raw if isinstance(raw, dict) else json.loads(raw)


async def _status(app_db: Database, org: str, hitl_id: uuid.UUID) -> str:
    async with app_db.tenant_session(org) as conn:
        return str(await conn.fetchval(
            "SELECT status FROM hitl_queue WHERE hitl_id=$1", hitl_id
        ))


async def _deliverable_count(app_db: Database, org: str) -> int:
    async with app_db.tenant_session(org) as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM deliverables WHERE org_id=$1", org
        ))


async def _journal_rows(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT principal_id, actor_kind, kind FROM work_journal "
            "WHERE org_id=$1 ORDER BY seq",
            org,
        )
    return [dict(r) for r in rows]


# ── (a) the binding is captured at defer time ────────────────────────────────

@requires_app_role
async def test_defer_captures_the_principal_binding_in_request_json(
    app_db, admin_conn, fake_provider, defers_externally
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org)

        app, container = await _build(base_url, org)
        fake.program(success(text=json.dumps({"reply": "never reached"})))
        before = fake.attempts
        try:
            hitl_id = await _defer_a_turn(app, org)

            stored = await _request_json(app_db, org, hitl_id)
            assert stored["on_behalf_of_principal"] == PRINCIPAL
            # Round-trips through the typed envelope, so approve() will read it.
            envelope = HitlReplayEnvelope.model_validate(stored)
            assert envelope.on_behalf_of_principal == PRINCIPAL
            assert envelope.agent_id == "cowork_agent"

            # A defer means nothing ran.
            assert fake.attempts == before
            assert await _deliverable_count(app_db, org) == 0
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_approval_executes_with_the_binding_intact(
    app_db, admin_conn, fake_provider, defers_externally
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org)

        app, container = await _build(base_url, org)
        fake.program(success(text=json.dumps({"reply": "Announcement drafted."})))
        try:
            hitl_id = await _defer_a_turn(app, org)

            row, deliverable = await container.hitl.approve(
                org_id=org, hitl_id=hitl_id, reviewed_by="owner_alice"
            )
            assert row.hitl_id == hitl_id
            assert deliverable.agent_id == "cowork_agent"
            assert await _deliverable_count(app_db, org) == 1

            # The replay ran on a PRINCIPAL-BOUND token, so the journal entry is
            # scoped to that human and marked as an interactive-session action.
            rows = await _journal_rows(app_db, org)
            assert len(rows) == 1, rows
            assert rows[0]["principal_id"] == PRINCIPAL
            assert rows[0]["actor_kind"] == "agent_cowork"
            assert rows[0]["kind"] == "cowork.turn_approved"
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# ── (b) authority is recompiled at approval time ─────────────────────────────

@requires_app_role
async def test_grant_revoked_between_defer_and_approve_refuses(
    app_db, admin_conn, fake_provider, defers_externally
) -> None:
    """The load-bearing case. Grants are really DELETED from real Postgres between
    the 202 and the approval; the approval must refuse rather than execute on
    authority that no longer exists."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org)

        app, container = await _build(base_url, org)
        fake.program(success(text=json.dumps({"reply": "must never be produced"})))
        try:
            hitl_id = await _defer_a_turn(app, org)
            attempts_at_defer = fake.attempts

            deleted = await _revoke_all_grants(admin_conn, org)
            assert deleted == len(MANIFEST), f"expected to delete {len(MANIFEST)} grants"

            with pytest.raises(HitlReplayInvalid):
                await container.hitl.approve(
                    org_id=org, hitl_id=hitl_id, reviewed_by="owner_alice"
                )

            # Nothing executed, nothing was charged, no provider call.
            assert await _deliverable_count(app_db, org) == 0
            assert fake.attempts == attempts_at_defer
            # PERMANENT disposition: terminal 'expired', NOT released to 'pending'
            # -- the stored binding names one human and would fail identically on
            # every future approval, which is the loop this avoids.
            assert await _status(app_db, org, hitl_id) == "expired"
            assert await _journal_rows(app_db, org) == []
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_principal_suspended_between_defer_and_approve_refuses(
    app_db, admin_conn, fake_provider, defers_externally
) -> None:
    """Offboarding, as opposed to descoping. compile_authority raises
    PrincipalSuspended before any scope arithmetic happens, so a deactivated human
    loses authority immediately rather than at grant expiry."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org)

        app, container = await _build(base_url, org)
        fake.program(success(text=json.dumps({"reply": "must never be produced"})))
        try:
            hitl_id = await _defer_a_turn(app, org)
            attempts_at_defer = fake.attempts

            await _suspend_principal(admin_conn, org)

            with pytest.raises(HitlReplayInvalid):
                await container.hitl.approve(
                    org_id=org, hitl_id=hitl_id, reviewed_by="owner_alice"
                )
            assert await _deliverable_count(app_db, org) == 0
            assert fake.attempts == attempts_at_defer
            assert await _status(app_db, org, hitl_id) == "expired"
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_the_stored_envelope_carries_no_authority_only_an_id(
    app_db, admin_conn, fake_provider, defers_externally
) -> None:
    """The structural reason (b) cannot be circumvented.

    If the envelope stored the compiled scope set or the authority_fingerprint, an
    approval could execute against authority that has since been withdrawn. Assert
    the stored payload contains NEITHER -- so recompilation is the only thing the
    replay can possibly do.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org)

        app, container = await _build(base_url, org)
        fake.program(success(text=json.dumps({"reply": "never reached"})))
        try:
            hitl_id = await _defer_a_turn(app, org)
            stored = await _request_json(app_db, org, hitl_id)

            assert set(stored) == {
                "agent_id", "input", "user_id", "correlation_id",
                "on_behalf_of_principal",
            }, stored
            flat = json.dumps(stored)
            assert "authority_fingerprint" not in flat
            assert "scopes" not in flat
            assert "llm.generate" not in flat, "a compiled scope leaked into the envelope"
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)
