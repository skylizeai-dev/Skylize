"""A real triggered run -> a real journal row -> a real brief. REAL Postgres.

This is the proof the unit tests cannot give. Everything below runs against a
live database, a live governance mint, and a fake provider API that returns real
token usage priced by a real `model_pricing` row -- so `cost_minor` is money the
system actually computed, not a number a test handed it.

WHAT IS MEASURED
  1. HAPPY PATH. A scheduled run of the pilot agent, in an UNGOVERNED org,
     completes; a `work_journal` row is read back out of Postgres with
     actor_kind='agent_autonomous', principal_id equal to the ORG OWNER'S
     users.user_id, and cost_minor > 0 matching SUM(ai_cost_ledger) for the run's
     correlation_id. GET /api/v1/me/brief as that human returns it in
     `done_while_away`.
  2. BOUNDARY PATH. The same run whose verdict trips a declared HITL trigger is
     flagged, and the brief returns it in `needs_attention`.
  3. GOVERNED PATH. In a GOVERNED org the stage-2.5 gate defers before any LLM
     call (fraud_detection_agent declares two triggers and does not opt out of
     trigger-presence deferral), and the brief shows `needs_attention` carrying
     the hitl_id.
  4. FAILURE PATH. A provider failure does NOT vanish: a `failed` row is durable
     and reaches the brief.
  5. IDENTITY GAP. An org with no owner refuses before dispatch -- nothing is
     executed and nothing is written.

THE IDENTITY CHAIN THIS PINS DOWN
Migration 0020 declared the derivation `principal.principal_id = users.user_id::text`
and warned that any future provisioning path must use it. The autonomous runner
IS such a path, so test 1 asserts the three ids are the same string end to end:
the `users` row, the journal's principal_id, and the `ctx.user_id` the brief
endpoint scopes on.
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
from skylize.app.autonomy.errors import PrincipalUnresolvable
from skylize.app.autonomy.pilot import PILOT_AGENT_ID
from skylize.app.autonomy.runner import (
    KIND_COMPLETED,
    KIND_DEFERRED,
    KIND_FAILED,
)
from skylize.app.autonomy.triggers import run_scheduled
from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.cost_ledger import micros_to_minor
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.edge.errors import install_error_handlers
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import brief as brief_routes
from skylize.events.memory_bus import InMemoryEventBus

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
from ..fakes.fake_provider_api.app import status, success
from ..fakes.fake_provider_api.server import running_fake_provider

pytestmark = pytest.mark.integration

MODEL = "fake-autonomous-run"
#: Synthetic price, in micro-USD per Mtok, chosen so ONE run costs a whole number
#: of cents. The fake provider reports 11 input + 7 output tokens, so
#: cost_micros = 18 * SYNTH_RATE / 1e6 = 180_000 micros = 18 cents.
#:
#: The rate is deliberately large. At a realistic rate those 18 tokens cost a
#: fraction of one cent, `micros_to_minor` would correctly round the run to 0,
#: and "cost_minor is real money" would be an assertion that 0 == 0 -- true, and
#: proving nothing. This is what lets the test distinguish "the cost was threaded
#: through" from "the cost field defaulted".
SYNTH_RATE = 10_000_000_000
BIG_CEILING = 10_000_000_000
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCHEDULE_ID = "pilot-hourly"

#: fraud_detection_agent's manifest (contracts/mvp/security.py:20-23). The owner
#: must hold these for the per-employee mint intersection to be non-empty.
MANIFEST = ("llm.generate", "memory.search")

#: A clean verdict: outcome='allow' with high confidence trips neither declared
#: trigger, so the run must land in done_while_away UNflagged.
CLEAN_VERDICT = {
    "entity_id": "org_periodic_sweep",
    "outcome": "allow",
    "confidence": 0.96,
    "reasons": ["no anomalies in window"],
}
#: Trips SECURITY_SEVERITY_HIGH via outcome != 'allow'.
FLAGGED_VERDICT = {
    "entity_id": "org_periodic_sweep",
    "outcome": "review",
    "confidence": 0.93,
    "reasons": ["velocity spike on 3 entities"],
}


def _org() -> str:
    return f"auto_{uuid.uuid4().hex[:10]}"


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


# --------------------------------------------------------------------------- #
# Seeding — a real org with a real owner, provisioned the way migration 0020 does
# --------------------------------------------------------------------------- #

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


async def _seed_owner(admin_conn, org: str) -> uuid.UUID:
    """One org owner, exactly as registration would create them.

    `roles` carries 'owner', which is the predicate migration 0017's partial
    unique index and migration 0020's seed both key on -- so this is the real
    shape the resolver reads, not a fixture tailored to it.
    """
    user_id = uuid.uuid4()
    await admin_conn.execute(
        """
        INSERT INTO users (user_id, org_id, email, password_hash, display_name,
                           roles, is_active, created_at)
        VALUES ($1,$2,$3,'x','Owner',ARRAY['owner'],true,now())
        """,
        user_id, org, f"owner-{user_id}@example.test",
    )
    # The principal record + grants migration 0020 derives from that users row.
    await admin_conn.execute(
        "INSERT INTO principal (principal_id, org_id, display_name, authority_level) "
        "VALUES ($1,$2,'Owner','executive')",
        str(user_id), org,
    )
    for scope in MANIFEST:
        await admin_conn.execute(
            "INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by) "
            "VALUES ($1,$2,$3,'position','test')",
            org, str(user_id), scope,
        )
    return user_id


async def _seed_non_owner(admin_conn, org: str) -> None:
    await admin_conn.execute(
        """
        INSERT INTO users (user_id, org_id, email, password_hash, display_name,
                           roles, is_active, created_at)
        VALUES ($1,$2,$3,'x','Analyst',ARRAY['analyst'],true,now())
        """,
        uuid.uuid4(), org, f"analyst-{uuid.uuid4()}@example.test",
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
        "DELETE FROM users WHERE org_id=$1",
        "DELETE FROM org_spend_ceiling WHERE org_id=$1",
        "DELETE FROM governance_tokens WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        await admin_conn.execute(sql, org)
    await admin_conn.execute("DELETE FROM model_pricing WHERE model=$1", MODEL)


async def _build(base_url: str, org: str, *, governed: bool):
    settings = Settings(
        backend="postgres", dev_auth=False, jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL, db_app_url=APP_DB_URL, redis_url=REDIS_URL,
        decision_engine_org_ids=[org] if governed else [],
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
    app.include_router(brief_routes.router)
    return app, container


# --------------------------------------------------------------------------- #
# Read-back helpers — everything asserted comes OUT of Postgres
# --------------------------------------------------------------------------- #

async def _journal_rows(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT seq, principal_id, actor_kind, actor_id, correlation_id, kind, "
            "headline, detail, cost_minor, requires_attention "
            "FROM work_journal WHERE org_id=$1 ORDER BY seq",
            org,
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["detail"], str):
            d["detail"] = json.loads(d["detail"])
        out.append(d)
    return out


async def _ledger_micros(app_db: Database, org: str, correlation_id: uuid.UUID) -> int:
    async with app_db.tenant_session(org) as conn:
        return int(await conn.fetchval(
            "SELECT COALESCE(SUM(cost_micros),0) FROM ai_cost_ledger "
            "WHERE correlation_id=$1",
            correlation_id,
        ))


async def _get_brief(app, org: str, principal_id: str) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        r = await client.get(
            "/api/v1/me/brief",
            headers={
                "X-Dev-Org": org,
                "X-Dev-User": principal_id,
                "X-Dev-Roles": "owner",
            },
        )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# 1. Happy path
# --------------------------------------------------------------------------- #

@requires_redis
@requires_app_role
async def test_scheduled_run_journals_real_cost_and_reaches_the_owners_brief(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        owner_id = await _seed_owner(admin_conn, org)
        # A second, NON-owner user, so a resolver that simply took the first user
        # in the org would pick the wrong human and fail this test.
        await _seed_non_owner(admin_conn, org)

        app, container = await _build(base_url, org, governed=False)
        try:
            # The agent run, then the brief's own summarization call.
            fake.program(
                success(text=json.dumps(CLEAN_VERDICT)),
                success(text=json.dumps({"summary": "All quiet."})),
            )
            outcome = await run_scheduled(
                container.autonomous_runs,
                org_id=org,
                agent_id=PILOT_AGENT_ID,
                schedule_id=SCHEDULE_ID,
            )

            assert outcome.status == "completed", outcome.reasons
            # THE IDENTITY CHAIN: the human at the end of escalation_path is the
            # org owner's users.user_id, rendered exactly as migration 0020 does.
            assert outcome.principal_id == str(owner_id)

            rows = await _journal_rows(app_db, org)
            assert len(rows) == 1
            row = rows[0]
            assert row["actor_kind"] == "agent_autonomous"
            assert row["actor_id"] == PILOT_AGENT_ID
            assert row["principal_id"] == str(owner_id)
            assert row["kind"] == KIND_COMPLETED
            assert row["correlation_id"] == outcome.correlation_id
            assert row["requires_attention"] is False
            assert row["detail"]["trigger"] == f"schedule:{SCHEDULE_ID}"
            assert row["detail"]["session_kind"] == "autonomous"

            # MONEY: the row's cost is the ledger's own sum for this run, not a
            # number this test supplied.
            micros = await _ledger_micros(app_db, org, outcome.correlation_id)
            assert micros > 0, "the run should have produced a priced ledger row"
            # Compared against the ledger's OWN conversion, not a reimplementation
            # of it here -- ADR-0006 fixes the rounding mode, and a test that did
            # its own arithmetic could disagree with the money path and be wrong.
            assert row["cost_minor"] == int(micros_to_minor(micros))
            assert row["cost_minor"] > 0, "a priced run must not journal as free"

            # THE BRIEF: the same human, through the real endpoint.
            brief = await _get_brief(app, org, str(owner_id))
            assert brief["entry_count"] == 1
            assert brief["needs_attention"] == []
            headlines = [e["headline"] for e in brief["done_while_away"]]
            assert len(headlines) == 1
            assert PILOT_AGENT_ID in headlines[0]
            assert brief["total_cost_minor"] == row["cost_minor"]
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# --------------------------------------------------------------------------- #
# 2. Boundary path
# --------------------------------------------------------------------------- #

@requires_redis
@requires_app_role
async def test_a_flagged_verdict_lands_in_needs_attention(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        owner_id = await _seed_owner(admin_conn, org)

        app, container = await _build(base_url, org, governed=False)
        try:
            fake.program(
                success(text=json.dumps(FLAGGED_VERDICT)),
                success(text=json.dumps({"summary": "Something to look at."})),
            )
            outcome = await run_scheduled(
                container.autonomous_runs,
                org_id=org, agent_id=PILOT_AGENT_ID, schedule_id=SCHEDULE_ID,
            )
            # It COMPLETED and still needs attention: the flag comes from the
            # verdict, not from the run having failed.
            assert outcome.status == "completed"
            assert outcome.requires_attention is True

            rows = await _journal_rows(app_db, org)
            assert rows[0]["requires_attention"] is True
            assert any(
                "security_severity_high" in r for r in rows[0]["detail"]["reasons"]
            )

            brief = await _get_brief(app, org, str(owner_id))
            assert len(brief["needs_attention"]) == 1
            assert brief["needs_attention"][0]["kind"] == KIND_COMPLETED
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# --------------------------------------------------------------------------- #
# 3. Governed path — the gate defers before any spend
# --------------------------------------------------------------------------- #

@requires_redis
@requires_app_role
async def test_in_a_governed_org_the_run_defers_and_the_brief_shows_the_hitl(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        owner_id = await _seed_owner(admin_conn, org)

        app, container = await _build(base_url, org, governed=True)
        try:
            fake.program(success(text=json.dumps({"summary": "Awaiting you."})))
            before = fake.attempts
            outcome = await run_scheduled(
                container.autonomous_runs,
                org_id=org, agent_id=PILOT_AGENT_ID, schedule_id=SCHEDULE_ID,
            )

            assert outcome.status == "deferred"
            assert outcome.hitl_id is not None
            # Stage 2.5 runs BEFORE the model: the agent call never reached the
            # provider, so nothing was spent on a run a human still has to approve.
            assert fake.attempts == before
            assert outcome.cost_minor == 0

            rows = await _journal_rows(app_db, org)
            assert rows[0]["kind"] == KIND_DEFERRED
            assert rows[0]["requires_attention"] is True
            assert rows[0]["detail"]["hitl_id"] == str(outcome.hitl_id)

            # The hitl_queue row really exists (the runner did not invent the id).
            async with app_db.tenant_session(org) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM hitl_queue WHERE hitl_id=$1", outcome.hitl_id
                ) == 1

            brief = await _get_brief(app, org, str(owner_id))
            assert len(brief["needs_attention"]) == 1
            assert brief["needs_attention"][0]["kind"] == KIND_DEFERRED
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# --------------------------------------------------------------------------- #
# 4. Failure path — a broken run must not vanish
# --------------------------------------------------------------------------- #

@requires_redis
@requires_app_role
async def test_a_failing_run_leaves_a_durable_trace_in_the_brief(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        owner_id = await _seed_owner(admin_conn, org)

        app, container = await _build(base_url, org, governed=False)
        try:
            # The provider is down for the agent call; the brief's own call works.
            fake.program(
                status(503),
                status(503),
                status(503),
                status(503),
                success(text=json.dumps({"summary": "A job failed."})),
            )
            outcome = await run_scheduled(
                container.autonomous_runs,
                org_id=org, agent_id=PILOT_AGENT_ID, schedule_id=SCHEDULE_ID,
            )

            assert outcome.status == "failed"
            assert outcome.requires_attention is True

            rows = await _journal_rows(app_db, org)
            assert len(rows) == 1, "the failure must be recorded exactly once"
            assert rows[0]["kind"] == KIND_FAILED
            assert rows[0]["requires_attention"] is True
            assert rows[0]["detail"]["reasons"]

            brief = await _get_brief(app, org, str(owner_id))
            assert len(brief["needs_attention"]) == 1
            assert brief["needs_attention"][0]["kind"] == KIND_FAILED
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# --------------------------------------------------------------------------- #
# 5. The identity gap refuses rather than substituting
# --------------------------------------------------------------------------- #

@requires_redis
@requires_app_role
async def test_an_org_with_no_owner_refuses_before_anything_runs(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        # Users exist, but none of them is the owner.
        await _seed_non_owner(admin_conn, org)

        _, container = await _build(base_url, org, governed=False)
        try:
            fake.program(success(text=json.dumps(CLEAN_VERDICT)))
            before = fake.attempts
            with pytest.raises(PrincipalUnresolvable):
                await run_scheduled(
                    container.autonomous_runs,
                    org_id=org, agent_id=PILOT_AGENT_ID, schedule_id=SCHEDULE_ID,
                )
            # Nothing executed, nothing spent, nothing written.
            assert fake.attempts == before
            assert await _journal_rows(app_db, org) == []
            async with app_db.tenant_session(org) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM deliverables WHERE org_id=$1", org
                ) == 0
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)
