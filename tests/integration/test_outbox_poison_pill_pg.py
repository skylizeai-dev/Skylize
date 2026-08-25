"""Outbox poison-pill guard on REAL Postgres + REAL Redis (D1 regression).

This suite exists because the guard it proves was once deleted. A JSONB-codec
change removed ``_publish_row``'s payload-shape check on the reasoning that the
codec makes malformed JSON unrepresentable. That is true and beside the point:
the guard protected against a payload that is VALID JSONB but NOT a JSON OBJECT
— an array, a scalar, or null. ``_flatten_for_stream`` calls ``payload.items()``
on it and raises ``AttributeError``; that escapes ``_publish_row``'s except
blocks (which wrap the XADD only) and ``_poll_and_publish`` (no handler) and
lands in ``run()``'s blanket handler, which sleeps and re-polls. The batch query
orders by ``created_at ASC``, so the same row is fetched first forever and no
decision event ever leaves the outbox again — silently.

The deleted test (``test_payload_deserialize_failure_marks_failed``) ran against
an ``AsyncMock`` connection, which is exactly why a payload shape real Postgres
can store went unnoticed. Everything here writes real rows through real asyncpg
and relays through a real Redis stream, so the payload under test is one the
database genuinely accepts and the codec genuinely returns.

The poller connects as the ADMIN/superuser role: ``decision_outbox`` carries
FORCE ROW LEVEL SECURITY (migration 0009), and the relay is a system-level
service that must see every tenant's rows.

Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_REDIS_URL are set.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from skylize.dal.connection import Database

# A TEST may import decision_engine; only the live request path may not (K3).
from skylize.decision_engine.outbox_poller import OutboxPoller

from .conftest import DB_URL, REDIS_URL, requires_pg, requires_redis

pytestmark = pytest.mark.integration

_STREAM_SUFFIX = "decision"


def _org() -> str:
    return f"poison_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture()
async def admin_db(migrated_public: None) -> AsyncIterator[Database]:
    """A ``Database`` as the admin role — how the relay's service role connects."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")
    db = Database(DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture()
async def real_redis() -> AsyncIterator[object]:
    """A real Redis client on a flushed db — the relay's actual XADD target."""
    if not REDIS_URL:
        pytest.skip("SKYLIZE_TEST_REDIS_URL not set")
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _insert_outbox_row(
    admin_conn: object,
    org: str,
    *,
    payload_json: str,
    created_at: datetime,
    row_key: str,
    retry_count: int = 0,
) -> uuid.UUID:
    """Write one decision_outbox row with a literal JSONB document.

    ``payload_json`` is passed as text and cast with ``::jsonb``, so Postgres
    itself validates it — a payload that lands here is one the column genuinely
    accepts, not a shape only a fake connection could produce.
    """
    row_id = uuid.uuid4()
    await admin_conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO decision_outbox (
            id, tenant_id, stream_key, event_type, payload, outbox_row_id,
            created_at, retry_count
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
        """,
        row_id, org, f"evt:{org}:{_STREAM_SUFFIX}", "decision.approved",
        payload_json, row_key, created_at, retry_count,
    )
    return row_id


async def _row_state(admin_conn: object, row_id: uuid.UUID) -> dict:
    rec = await admin_conn.fetchrow(  # type: ignore[attr-defined]
        "SELECT published_at, failed_at, retry_count FROM decision_outbox WHERE id=$1",
        row_id,
    )
    return dict(rec)


async def _cleanup(admin_conn: object, org: str) -> None:
    for sql in (
        "DELETE FROM decision_outbox WHERE tenant_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


# ---------------------------------------------------------------------------
# The regression: a valid-JSONB, non-object payload must not stall the relay
# ---------------------------------------------------------------------------

@requires_pg
@requires_redis
async def test_json_array_payload_marks_failed_and_relay_continues(
    admin_db, admin_conn, real_redis, caplog
) -> None:
    """A JSON ARRAY payload is stamped failed; the next row still publishes.

    Row order matters: the poison row is written with the EARLIER created_at, so
    the ``ORDER BY created_at ASC`` batch fetches it first — exactly the position
    from which an unhandled AttributeError would block every row behind it.
    """
    org = _org()
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    stream = f"evt:{org}:{_STREAM_SUFFIX}"
    try:
        await _seed_tenant(admin_conn, org)
        poison_id = await _insert_outbox_row(
            admin_conn, org,
            # Valid JSONB. Not an object. Postgres stores it; the codec returns
            # a Python list; `.items()` does not exist on a list.
            payload_json=json.dumps(["decision.approved", {"decision_id": "d1"}]),
            created_at=base,
            row_key=f"{uuid.uuid4().hex[:13]}-0001",
        )
        good_id = await _insert_outbox_row(
            admin_conn, org,
            payload_json=json.dumps({"event_id": "e2", "payload": {"decision_id": "d2"}}),
            created_at=base + timedelta(seconds=1),
            row_key=f"{uuid.uuid4().hex[:13]}-0002",
        )

        poller = OutboxPoller(db=admin_db, redis=real_redis, settings=MagicMock())
        with caplog.at_level(logging.ERROR, logger="skylize.decision_engine.outbox_poller"):
            await poller._poll_and_publish()

        # The poison row is settled as failed, with its retry count preserved
        # (not incremented): it is permanently unrelayable, not retryable.
        poison = await _row_state(admin_conn, poison_id)
        assert poison["failed_at"] is not None, "poison row was not stamped failed"
        assert poison["published_at"] is None, "poison row must never be marked published"
        assert poison["retry_count"] == 0

        # The relay did NOT stop at the poison row.
        good = await _row_state(admin_conn, good_id)
        assert good["published_at"] is not None, "row behind the poison pill never published"
        assert good["failed_at"] is None

        # ...and the good row's event really reached the real stream, alone.
        entries = await real_redis.xrange(stream)
        assert len(entries) == 1
        _entry_id, fields = entries[0]
        assert fields["event_id"] == "e2"
        assert fields["payload.decision_id"] == "d2"
        assert fields["event_type"] == "decision.approved"

        # ERROR logged, naming the row.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "no ERROR was logged for the unrelayable payload"
        assert any(
            getattr(r, "outbox_row_id", None) is not None
            and getattr(r, "payload_type", None) == "list"
            for r in errors
        ), f"ERROR did not carry the row id and payload type: {[r.__dict__ for r in errors]}"

        # A second poll fetches NEITHER row: the poison row is out of the batch
        # predicate (failed_at IS NOT NULL) instead of being served first forever.
        await poller._poll_and_publish()
        assert len(await real_redis.xrange(stream)) == 1
        remaining = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM decision_outbox WHERE tenant_id=$1 "
            "AND published_at IS NULL AND failed_at IS NULL",
            org,
        )
        assert remaining == 0
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
@requires_redis
@pytest.mark.parametrize(
    ("label", "payload_json"),
    [
        ("scalar_string", '"decision.approved"'),
        ("scalar_int", "42"),
        ("json_null", "null"),
    ],
)
async def test_non_object_payloads_all_mark_failed(
    admin_db, admin_conn, real_redis, label: str, payload_json: str
) -> None:
    """Every non-object JSONB value the column accepts settles as failed.

    ``null`` is the sharpest case: the column is NOT NULL, but a JSON ``null``
    is a non-null JSONB VALUE, so it stores fine and the codec returns Python
    ``None``.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        row_id = await _insert_outbox_row(
            admin_conn, org,
            payload_json=payload_json,
            created_at=datetime.now(timezone.utc),
            row_key=f"{uuid.uuid4().hex[:13]}-{label[:4]}",
        )

        poller = OutboxPoller(db=admin_db, redis=real_redis, settings=MagicMock())
        await poller._poll_and_publish()

        state = await _row_state(admin_conn, row_id)
        assert state["failed_at"] is not None, f"{label}: not stamped failed"
        assert state["published_at"] is None
        assert await real_redis.xrange(f"evt:{org}:{_STREAM_SUFFIX}") == []
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
@requires_redis
async def test_object_payload_still_publishes_unchanged(
    admin_db, admin_conn, real_redis
) -> None:
    """The guard must not alter the happy path: an object payload still relays."""
    org = _org()
    payload = {"event_id": "e1", "payload": {"decision_id": "d1"}, "tags": ["a", "b"]}
    try:
        await _seed_tenant(admin_conn, org)
        row_id = await _insert_outbox_row(
            admin_conn, org,
            payload_json=json.dumps(payload),
            created_at=datetime.now(timezone.utc),
            row_key=f"{uuid.uuid4().hex[:13]}-0003",
        )

        poller = OutboxPoller(db=admin_db, redis=real_redis, settings=MagicMock())
        await poller._poll_and_publish()

        state = await _row_state(admin_conn, row_id)
        assert state["published_at"] is not None
        assert state["failed_at"] is None

        entries = await real_redis.xrange(f"evt:{org}:{_STREAM_SUFFIX}")
        assert len(entries) == 1
        _entry_id, fields = entries[0]
        assert fields["event_id"] == "e1"
        assert fields["payload.decision_id"] == "d1"
        # Lists are JSON-encoded into a single field (unchanged flatten behaviour).
        assert json.loads(fields["tags"]) == ["a", "b"]
    finally:
        await _cleanup(admin_conn, org)
