"""JSONB read-back on REAL Postgres for every site the codec change touched.

The deliverables 500 existed because no test ever read a JSONB row back
through the DAL on the postgres backend — asyncpg returned ``str`` and every
site coped (or crashed) on its own. The pool now registers a json/jsonb codec
(``dal.connection._init_connection``), so JSONB decodes to Python objects
uniformly. Each test here writes a row with non-empty JSONB, reads it back
through the DAL, and asserts the decoded shape:

  * deliverables.metadata_json  — the read that 500'd (dal/deliverables.py)
  * hitl_queue proposal/request/verdict_json (dal/hitl.py)
  * org_credentials.metadata_json — str contract preserved (dal/credentials.py)
  * decision_outbox.payload via the OutboxPoller (decision_engine/outbox_poller.py)

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.credentials import CredentialRow, PgCredentialRepository
from skylize.dal.deliverables import PgDeliverableRepository
from skylize.dal.hitl import PgHitlQueueRepository
from skylize.dal.ports import DeliverableRow, HitlEscalation

# A TEST may import decision_engine; only the live request path may not (K3).
from skylize.decision_engine.outbox_poller import OutboxPoller

from .conftest import APP_DB_URL, DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

_META = {
    "input": {"brand_name": "Acme", "count": 3},
    "flags": ["a", "b"],
    "nested": {"deep": {"k": "v"}},
}


def _org() -> str:
    return f"jsonb_{uuid.uuid4().hex[:8]}"


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn: object, org: str) -> None:
    for sql in (
        "DELETE FROM deliverables WHERE org_id=$1",
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
        "DELETE FROM org_credentials WHERE org_id=$1",
        "DELETE FROM decision_outbox WHERE tenant_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)  # type: ignore[attr-defined]
        except Exception:
            pass


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


@pytest_asyncio.fixture()
async def admin_db(migrated_public: None) -> AsyncIterator[Database]:
    """A `Database` as the admin role — how the OutboxPoller's service role
    connects (it must see all tenants' outbox rows; RLS bypass by design)."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")
    db = Database(DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _deliverable(org: str, **overrides: object) -> DeliverableRow:
    now = datetime.now(timezone.utc)
    defaults: dict = dict(
        id=uuid.uuid4(),
        org_id=org,
        agent_id="hook_generator_agent",
        deliverable_type="marketing_copy",
        title="t",
        content_markdown="# c",
        summary="s",
        status="draft",
        version=1,
        created_at=now,
        updated_at=now,
        metadata_json=dict(_META),
    )
    defaults.update(overrides)
    return DeliverableRow(**defaults)


# ---------------------------------------------------------------------------
# deliverables.metadata_json — the exact read that returned 500
# ---------------------------------------------------------------------------

@requires_app_role
async def test_deliverable_metadata_json_reads_back_decoded(app_db, admin_conn) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        repo = PgDeliverableRepository(app_db)
        v1 = _deliverable(org)
        await repo.create(v1)

        got = await repo.get_by_id(v1.id, org)
        assert got is not None
        assert got.metadata_json == _META

        rows, total = await repo.list_by_org(org)
        assert total == 1
        assert rows[0].metadata_json == _META

        v2 = _deliverable(org, version=2, parent_id=v1.id, metadata_json={"rev": 2})
        await repo.create(v2)
        chain = await repo.list_versions(org, v2.id)
        assert [r.metadata_json for r in chain] == [_META, {"rev": 2}]
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_deliverable_empty_metadata_reads_back_empty_dict(app_db, admin_conn) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        repo = PgDeliverableRepository(app_db)
        row = _deliverable(org, metadata_json={})
        await repo.create(row)
        got = await repo.get_by_id(row.id, org)
        assert got is not None
        assert got.metadata_json == {}
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# hitl_queue proposal/request/verdict_json through the full defer -> claim chain
# ---------------------------------------------------------------------------

@requires_app_role
async def test_hitl_jsonb_columns_read_back_decoded(app_db, admin_conn) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        now = datetime.now(timezone.utc)
        proposal = {"proposal_id": "p1", "action_kind": "agent.execute", "nested": {"k": 1}}
        request = {"agent_id": "hook_generator_agent", "input": {"brand_name": "Acme"}}
        hitl_id = uuid.uuid4()
        repo = PgHitlQueueRepository(app_db)
        await repo.enqueue(
            HitlEscalation(
                decision_id=uuid.uuid4(),
                org_id=org,
                correlation_id=uuid.uuid4(),
                causation_event_id=None,
                partition_key=f"pk_{hitl_id}",
                proposing_agent="hook_generator_agent",
                authority_level="worker",
                action_kind="agent.execute",
                proposal_json=proposal,
                outcome="deferred_to_human",
                outcome_reason="external_publication",
                policy_version="mvp-inline-1.0",
                score_json=None,
                governance_token_id=None,
                hitl_id=hitl_id,
                trigger_reason="first_external_launch",
                expires_at=now + timedelta(hours=1),
                created_at=now,
                request_json=request,
            )
        )

        item = await repo.get(hitl_id, org)
        assert item is not None
        assert item.proposal_json == proposal
        assert item.request_json == request
        assert item.verdict_json is None

        pending, total = await repo.list_pending(org)
        assert total == 1
        assert pending[0].proposal_json == proposal
        assert pending[0].request_json == request

        verdict = {"approved": True, "note": "ok"}
        claimed = await repo.claim(
            hitl_id, org,
            status_to="approved", verdict_by="u1",
            verdict_json=verdict, verdict_at=now, require_request=True,
        )
        assert claimed is not None
        assert claimed.verdict_json == verdict
        assert claimed.proposal_json == proposal
        assert claimed.request_json == request
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# org_credentials.metadata_json — the row contract stays a JSON-encoded str
# ---------------------------------------------------------------------------

@requires_app_role
async def test_credential_metadata_json_str_contract_roundtrips(app_db, admin_conn) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        meta = {"region": "eu", "kid": "k1"}
        row = CredentialRow(
            cred_id=uuid.uuid4(),
            org_id=org,
            provider="anthropic",
            label="",
            encrypted_value="enc:abc",
            metadata_json=json.dumps(meta),
            created_at=datetime.now(timezone.utc),
            rotated_at=None,
        )
        repo = PgCredentialRepository(app_db)
        await repo.insert(row)

        got = await repo.get(org, "anthropic", "")
        assert got is not None
        assert isinstance(got.metadata_json, str)
        assert json.loads(got.metadata_json) == meta

        by_id = await repo.get_by_id(row.cred_id, org)
        assert by_id is not None
        assert json.loads(by_id.metadata_json) == meta
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# decision_outbox.payload — decoded dict through the pool, relayed flattened
# ---------------------------------------------------------------------------

class _RecordingRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def xadd(self, name: str, fields: dict) -> str:
        self.calls.append((name, dict(fields)))
        return "1-1"


@requires_pg
async def test_outbox_payload_reads_back_decoded_and_relays(admin_db, admin_conn) -> None:
    org = _org()
    row_id = uuid.uuid4()
    payload = {
        "event_id": "e1",
        "event_type": "decision.approved",
        "payload": {"decision_id": "d1"},
    }
    try:
        await _seed_tenant(admin_conn, org)
        await admin_conn.execute(
            """
            INSERT INTO decision_outbox (
                id, tenant_id, stream_key, event_type, payload, outbox_row_id
            ) VALUES ($1,$2,$3,$4,$5::jsonb,$6)
            """,
            row_id, org, f"evt:{org}:decision", "decision.approved",
            json.dumps(payload), "1700000000000-0001",
        )

        # Read back through the pool: the codec must yield the decoded dict.
        async with admin_db.admin_session() as conn:
            rec = await conn.fetchrow(
                "SELECT id, tenant_id, stream_key, event_type, payload, "
                "outbox_row_id, retry_count FROM decision_outbox WHERE id=$1",
                row_id,
            )
        assert rec is not None
        assert rec["payload"] == payload

        # The poller consumes that decoded row and relays it flattened.
        redis = _RecordingRedis()
        poller = OutboxPoller(db=admin_db, redis=redis, settings=MagicMock())
        await poller._publish_row(rec)

        assert len(redis.calls) == 1
        stream, fields = redis.calls[0]
        assert stream == f"evt:{org}:decision"
        assert fields["event_id"] == "e1"
        assert fields["payload.decision_id"] == "d1"
        assert fields["event_type"] == "decision.approved"

        published_at = await admin_conn.fetchval(
            "SELECT published_at FROM decision_outbox WHERE id=$1", row_id
        )
        assert published_at is not None
    finally:
        await _cleanup(admin_conn, org)
