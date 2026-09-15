"""`DeliverableContentSignalDAL` against REAL Postgres.

THE THING A FAKE CANNOT PROVE is "has this deliverable already been reviewed".
That is not a flag on a row -- it is a NOT EXISTS join against the reviewer's own
past deliverables, matching `metadata_json->'input'->>'brief_id'` to the
candidate's id. A fake repository asked the same question would answer from
whatever its author believed the join did. These cases make Postgres answer.

What is measured here:
  1. it selects only content from the NAMED producing agents -- never the
     reviewer's own verdicts, and never the fraud sweep's records;
  2. a deliverable the reviewer has already ruled on disappears from the queue,
     derived purely from the metadata the live path already writes;
  3. oldest first, so a backlog drains in order instead of starving its tail;
  4. the lookback window is half-open at both ends;
  5. one org's sweep cannot see another org's content (RLS, as the app role);
  6. nothing to review is None, not an empty-ish row.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.content_signals import DeliverableContentSignalDAL

from .conftest import APP_DB_URL, purge_tenants, requires_app_role

pytestmark = [pytest.mark.integration, requires_app_role]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
REVIEWER = "brand_guardian_agent"
PRODUCERS = [
    "hook_generator_agent",
    "ad_copy_agent",
    "caption_writer_agent",
    "script_writer_agent",
]


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture()
def orgs() -> tuple[str, str]:
    stamp = uuid.uuid4().hex[:10]
    return f"cntorg_a_{stamp}", f"cntorg_b_{stamp}"


async def _tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _deliverable(
    admin_conn,
    org: str,
    *,
    agent_id: str,
    created_at: datetime,
    content: str = "copy under review",
    deliverable_type: str = "ad_creative",
    metadata: dict | None = None,
) -> uuid.UUID:
    """One `deliverables` row in the shape `create_deliverable` writes.

    `metadata_json` carries `{"input": ...}` because that is exactly what
    `AgentExecutionService` persists (execution.py:496-497) -- the DAL's
    already-reviewed join reads that key, so a fixture that omitted it would be
    testing a different schema from the one the live path produces.
    """
    did = uuid.uuid4()
    await admin_conn.execute(
        """
        INSERT INTO deliverables (id, org_id, agent_id, deliverable_type, title,
                                  content_markdown, status, version,
                                  metadata_json, created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,'draft',1,$7,$8,$8)
        """,
        did, org, agent_id, deliverable_type, f"{agent_id} output",
        content, json.dumps(metadata or {"input": {}}), created_at,
    )
    return did


async def _read(app_db, org: str, *, since_hours: int = 24):
    return await DeliverableContentSignalDAL(app_db).read_oldest_unchecked(
        org_id=org,
        producer_agent_ids=PRODUCERS,
        reviewer_agent_id=REVIEWER,
        since=NOW - timedelta(hours=since_hours),
        until=NOW + timedelta(seconds=1),
    )


# --------------------------------------------------------------------------- #

async def test_it_selects_authored_content_and_not_the_reviewers_own_output(
    app_db, admin_conn, orgs
) -> None:
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        wanted = await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(minutes=30), content="Buy now, pay never.",
        )
        # The reviewer's OWN verdict, and the fraud sweep's record. Feeding
        # either back to the reviewer would be a brand check on a brand check.
        await _deliverable(
            admin_conn, org, agent_id=REVIEWER,
            created_at=NOW - timedelta(minutes=29), deliverable_type="other",
        )
        await _deliverable(
            admin_conn, org, agent_id="fraud_detection_agent",
            created_at=NOW - timedelta(minutes=28), deliverable_type="other",
        )

        row = await _read(app_db, org)
        assert row is not None
        assert row.deliverable_id == wanted
        assert row.producing_agent_id == "ad_copy_agent"
        assert row.content_markdown == "Buy now, pay never."
        assert row.backlog == 1
    finally:
        await purge_tenants(admin_conn, [org])


async def test_a_deliverable_the_reviewer_already_ruled_on_leaves_the_queue(
    app_db, admin_conn, orgs
) -> None:
    """THE JOIN. No new column: the reviewer's own run recorded the id it judged.
    """
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        checked = await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(minutes=40), content="already reviewed",
        )
        unchecked = await _deliverable(
            admin_conn, org, agent_id="caption_writer_agent",
            created_at=NOW - timedelta(minutes=30), content="not yet reviewed",
        )
        # Before the verdict exists, the older one is next in line.
        first = await _read(app_db, org)
        assert first is not None and first.deliverable_id == checked
        assert first.backlog == 2

        # The reviewer's verdict, written exactly as `execute()` writes it.
        await _deliverable(
            admin_conn, org, agent_id=REVIEWER,
            created_at=NOW - timedelta(minutes=35), deliverable_type="other",
            metadata={"input": {"brief_id": str(checked), "content_kind": "copy"}},
        )

        after = await _read(app_db, org)
        assert after is not None
        assert after.deliverable_id == unchecked, "the reviewed one must drop out"
        assert after.backlog == 1
    finally:
        await purge_tenants(admin_conn, [org])


async def test_oldest_first_so_a_backlog_drains_instead_of_starving(
    app_db, admin_conn, orgs
) -> None:
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        oldest = await _deliverable(
            admin_conn, org, agent_id="script_writer_agent",
            created_at=NOW - timedelta(hours=6), content="oldest",
        )
        await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(hours=2), content="middle",
        )
        await _deliverable(
            admin_conn, org, agent_id="hook_generator_agent",
            created_at=NOW - timedelta(minutes=5), content="newest",
        )

        row = await _read(app_db, org)
        assert row is not None
        assert row.deliverable_id == oldest
        assert row.content_markdown == "oldest"
        # The backlog is what tells an operator the cadence is not keeping up.
        assert row.backlog == 3
    finally:
        await purge_tenants(admin_conn, [org])


async def test_content_older_than_the_lookback_is_let_go_not_reviewed_late(
    app_db, admin_conn, orgs
) -> None:
    """Half-open at the floor: a brand verdict on week-old shipped copy is
    archaeology, and the window says so rather than draining forever."""
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(days=3), content="ancient",
        )
        assert await _read(app_db, org, since_hours=24) is None
        # Widen the lookback and the same row is reachable -- so the None above
        # is the WINDOW, not a broken query.
        assert await _read(app_db, org, since_hours=24 * 7) is not None
    finally:
        await purge_tenants(admin_conn, [org])


async def test_content_created_in_the_same_tick_as_the_sweep_is_not_skipped(
    app_db, admin_conn, orgs
) -> None:
    """The upper bound is INCLUSIVE, and this is why.

    `datetime.now()` has ~15.6ms granularity on Windows, so a deliverable
    persisted and a sweep started in the same tick carry the IDENTICAL timestamp.
    Under a half-open `created_at < until` that row is invisible -- a
    millisecond-wide hole that a review triggered right after a publish falls
    into. Nothing is double-reviewed as a result: the NOT EXISTS join, not the
    window, is what prevents that.
    """
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        instant = NOW
        did = await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=instant, content="published this instant",
        )
        row = await DeliverableContentSignalDAL(app_db).read_oldest_unchecked(
            org_id=org,
            producer_agent_ids=PRODUCERS,
            reviewer_agent_id=REVIEWER,
            since=instant - timedelta(hours=24),
            until=instant,  # EXACTLY the row's own created_at
        )
        assert row is not None, "a same-tick deliverable must not fall in a hole"
        assert row.deliverable_id == did
    finally:
        await purge_tenants(admin_conn, [org])


async def test_one_orgs_sweep_cannot_see_another_orgs_content(
    app_db, admin_conn, orgs
) -> None:
    """RLS, read as the NOBYPASSRLS app role -- a real boundary, not a WHERE."""
    org_a, org_b = orgs
    try:
        await _tenant(admin_conn, org_a)
        await _tenant(admin_conn, org_b)
        await _deliverable(
            admin_conn, org_b, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(minutes=10), content="org B's copy",
        )
        assert await _read(app_db, org_a) is None

        b_row = await _read(app_db, org_b)
        assert b_row is not None and b_row.content_markdown == "org B's copy"
    finally:
        await purge_tenants(admin_conn, [org_a, org_b])


async def test_nothing_to_review_is_none(app_db, admin_conn, orgs) -> None:
    """Deliberately NOT a zeroed row, unlike the audit window's quiet hour: there
    is no brand verdict to give about content that does not exist."""
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        assert await _read(app_db, org) is None
    finally:
        await purge_tenants(admin_conn, [org])


async def test_an_empty_producer_allowlist_selects_nothing_rather_than_everything(
    app_db, admin_conn, orgs
) -> None:
    """A mis-wired allowlist must not silently widen the sweep to every row."""
    org, _ = orgs
    try:
        await _tenant(admin_conn, org)
        await _deliverable(
            admin_conn, org, agent_id="ad_copy_agent",
            created_at=NOW - timedelta(minutes=10),
        )
        got = await DeliverableContentSignalDAL(app_db).read_oldest_unchecked(
            org_id=org, producer_agent_ids=[], reviewer_agent_id=REVIEWER,
            since=NOW - timedelta(hours=24), until=NOW + timedelta(seconds=1),
        )
        assert got is None
    finally:
        await purge_tenants(admin_conn, [org])
