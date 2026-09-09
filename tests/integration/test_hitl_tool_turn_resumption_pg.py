"""Mid-loop suspension and VERBATIM resumption, against REAL Postgres.

The property this suite exists to prove, and which nothing in the codebase could
prove before it: **approving a HITL row executes the action the human reviewed**,
not a freshly sampled one. Every assertion is made against the real
``hitl_queue`` row read back out of Postgres and against the provider call count,
never against the object that wrote them.

The contract under test is a variant of ``infrastructure_executor`` whose manifest
names a purpose-built gated tool instead of the GCP one. Two reasons it is a
variant and not the production contract: the real contract carries
FIRST_EXTERNAL_LAUNCH, so it defers at stage 2.5 and never reaches the tool loop
this suite is about; and gating the real ``integration.gcp_stop_instance`` would
change production behaviour for a path that already has its own HITL story.

WHAT IS DELIBERATELY NOT ASSERTED HERE. That a rejected run's earlier turns are
undone. They are not, there is no compensation mechanism anywhere in this
codebase, and the design says so
(docs/architecture/hitl_approval_resumption_design.md section 4.2). Rejection
stops what has not happened; it does not reverse what has.

Real Postgres + Redis; skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.contracts.base import ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import agents as agents_routes
from skylize.edge.routes import hitl as hitl_routes
from skylize.tools.base import (
    ToolApprovalProfile,
    ToolContext,
    ToolDefinition,
)
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
    _cleanup,
    _deliverable_ids,
    _gen_key,
    _owner,
    _seed_ceiling,
    _seed_price,
    _seed_tenant,
)

pytestmark = pytest.mark.integration

AGENT = "infrastructure_executor"
GATED_TOOL = "test.gated_action"
UNGATED_TOOL = "test.ungated_action"
_INPUT = {
    "project": "proj-1",
    "zone": "europe-west1-b",
    "instance": "vm-1",
    "reason": "spend ceiling breached",
}
_FINAL = json.dumps({
    "fully_succeeded": True, "stopped": True, "ip_released": True,
    "partial": False, "summary": "done",
})


def _org() -> str:
    return f"resume_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# A gated tool, and an ungated one to prove per-turn atomicity covers both
# ---------------------------------------------------------------------------

class _ActionIn(BaseModel):
    target: str = Field(min_length=1)


class _ActionOut(BaseModel):
    performed_on: str


#: Every ToolContext the handlers were dispatched with, in order. This is how the
#: suite observes what actually reached the tool layer -- the tool_use id, and
#: whether the proxy was told the call came from a resumed turn.
DISPATCHED: list[tuple[str, dict, ToolContext]] = []


def _make_tool(tool_id: str, *, gated: bool) -> ToolDefinition:
    async def handler(data: _ActionIn, ctx: ToolContext) -> _ActionOut:
        DISPATCHED.append((tool_id, data.model_dump(), ctx))
        return _ActionOut(performed_on=data.target)

    return ToolDefinition(
        tool_id=tool_id,
        name=tool_id,
        description="test tool",
        input_schema=_ActionIn,
        output_schema=_ActionOut,
        category="integration",
        handler=handler,
        approval=(
            ToolApprovalProfile(reason="moves real money in the test contract")
            if gated else None
        ),
    )


@pytest.fixture()
def gated_contract() -> Iterator[None]:
    """A tool-loop contract that stage 2.5 APPROVES, so the run reaches the loop.

    Stripping human_in_loop_triggers is what makes this suite about the mid-loop
    gate rather than the request-level one: the evaluator approves, the model is
    sampled, and only then does the tool declaration suspend the turn.
    """
    original = MVP_REGISTRY.resolve(AGENT)
    MVP_REGISTRY.register_contract(original.model_copy(update={
        "human_in_loop_triggers": [],
        "allowed_tools": [
            ToolGrant(tool_id=GATED_TOOL, purpose="the gated action"),
            ToolGrant(tool_id=UNGATED_TOOL, purpose="an ordinary action"),
        ],
        "invocable_tools": [GATED_TOOL, UNGATED_TOOL],
    }))
    try:
        yield
    finally:
        MVP_REGISTRY.register_contract(original)


@pytest.fixture(autouse=True)
def _clear_dispatched() -> Iterator[None]:
    DISPATCHED.clear()
    yield
    DISPATCHED.clear()


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
async def _running(gov_org: str, base_url: str) -> AsyncIterator[tuple[AsyncClient, Container]]:
    settings = Settings(
        backend="postgres",
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        decision_engine_org_ids=[gov_org],
        anthropic_api_key="sk-test",
        anthropic_base_url=base_url,
        llm_demo_mode=False,
        governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL,
        llm_model_fast=MODEL,
        llm_model_reasoning=MODEL,
    )
    container = await build_container(settings)
    # The gated tools are registered into the container's own registry, so the
    # proxy resolves them exactly as it resolves a built-in one.
    registry = container.agent_execution._tools.registry  # type: ignore[union-attr]
    registry.register(_make_tool(GATED_TOOL, gated=True))
    registry.register(_make_tool(UNGATED_TOOL, gated=False))
    app = FastAPI()
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)
    app.include_router(hitl_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


async def _seed(admin_conn, app_db: Database, org: str) -> None:
    await _seed_price(admin_conn)
    await _seed_tenant(admin_conn, org)
    await _seed_ceiling(app_db, org)


async def _row(app_db: Database, org: str, hitl_id: uuid.UUID) -> dict | None:
    async with app_db.tenant_session(org) as conn:
        rec = await conn.fetchrow(
            "SELECT hitl_id, decision_id, status, trigger_reason, request_json, "
            "resumption_json FROM hitl_queue WHERE hitl_id=$1",
            hitl_id,
        )
    return dict(rec) if rec is not None else None


def _program_gated_turn(fake, *, tool_uses: list[dict] | None = None) -> None:
    fake.program(
        success(
            tool_uses=tool_uses or [{
                "id": "toolu_gated_1", "name": GATED_TOOL,
                "input": {"target": "vm-1"},
            }],
            message_id="msg_turn0",
        ),
        # Only reached AFTER an approval: the suspended run never samples again.
        success(text=_FINAL, message_id="msg_final"),
    )


async def _execute(client: AsyncClient, org: str) -> uuid.UUID:
    r = await client.post(
        "/api/v1/agents/execute",
        json={"agent_id": AGENT, "input": _INPUT},
        headers=_owner(org),
    )
    assert r.status_code == 202, r.text
    return uuid.UUID(r.json()["hitl_id"])


# ---------------------------------------------------------------------------
# 1. Suspension captures the exact turn
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_suspension_captures_the_exact_tool_call_and_prefix(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            before = fake.attempts
            hitl_id = await _execute(client, org)

            # EXACTLY ONE sampling call, and ZERO tool dispatches. This is the
            # new gate's own property, and it is not the stage-2.5 property:
            # an LLM call really did happen and really was billed.
            assert fake.attempts == before + 1
            assert DISPATCHED == []
            assert await _deliverable_ids(app_db, org) == []

            row = await _row(app_db, org, hitl_id)
            assert row is not None
            assert row["status"] == "pending"
            # The machine-readable trigger stays a vocabulary value; WHICH tool
            # tripped it is recorded on the parent decision, where the other
            # gate's reasons live too.
            assert row["trigger_reason"] == "TOOL_APPROVAL_REQUIRED"
            async with app_db.tenant_session(org) as conn:
                outcome_reason = await conn.fetchval(
                    "SELECT outcome_reason FROM decisions WHERE decision_id=$1",
                    row["decision_id"],
                )
            assert GATED_TOOL in outcome_reason

            # The snapshot, read back out of Postgres.
            resumption = row["resumption_json"]
            assert resumption is not None
            assert resumption["pending_tool_use_ids"] == ["toolu_gated_1"]
            assert resumption["iteration"] == 0
            assert resumption["tokens_used_so_far"] > 0

            # The prefix is the seed user turn plus the reviewed assistant turn,
            # and the assistant turn carries the block VERBATIM.
            messages = resumption["messages"]
            assert [m["role"] for m in messages] == ["user", "assistant"]
            blocks = [b for b in messages[-1]["content"] if b["kind"] == "tool_use"]
            assert len(blocks) == 1
            assert blocks[0]["tool_use_id"] == "toolu_gated_1"
            assert blocks[0]["tool_name"] == GATED_TOOL
            assert blocks[0]["tool_input"] == {"target": "vm-1"}

            # The envelope is untouched by any of this: its never-rewritten
            # invariant is preserved because the snapshot has its own column.
            assert row["request_json"]["agent_id"] == AGENT
            assert "resumption" not in row["request_json"]

            # The reviewer is told which of the two shapes this row is.
            r = await client.get("/api/v1/hitl", headers=_owner(org))
            item = r.json()["data"][0]
            assert item["approval_semantics"] == "resume_action"
            assert item["pending_tool_calls"] == [{
                "tool_use_id": "toolu_gated_1",
                "tool_name": GATED_TOOL,
                "tool_input": {"target": "vm-1"},
            }]
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 2. Approval resumes VERBATIM
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_approval_resumes_the_stored_call_without_re_sampling_it(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)
            after_defer = fake.attempts

            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json={"note": "go"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text

            # THE CENTRAL ASSERTION. The resumed run sampled exactly ONCE -- the
            # turn AFTER the approved one. The approved turn itself cost no
            # sampling call, which is what makes the dispatched call the one the
            # human reviewed rather than a re-decision that happens to look alike.
            assert fake.attempts == after_defer + 1

            assert len(DISPATCHED) == 1
            tool_id, data, ctx = DISPATCHED[0]
            assert tool_id == GATED_TOOL
            assert data == {"target": "vm-1"}
            # The provider's own block id round-tripped through Postgres and back
            # out to the tool layer, unchanged.
            assert ctx.tool_use_id == "toolu_gated_1"
            assert ctx.is_hitl_resumption is True
            assert ctx.hitl_id == hitl_id
            assert ctx.replay_key() is not None

            assert len(await _deliverable_ids(app_db, org)) == 1
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "approved"
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 3. Rejection
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_rejection_dispatches_nothing(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)
            after_defer = fake.attempts

            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/reject", json={"note": "no"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "rejected"

            # Nothing sampled, nothing dispatched, nothing produced.
            assert fake.attempts == after_defer
            assert DISPATCHED == []
            assert await _deliverable_ids(app_db, org) == []
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "rejected"
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 4. Per-turn atomicity (owner decision D4)
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_a_multi_call_turn_approves_as_one_unit(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """One gated call and one ungated call, sampled together: both or neither.

    The ungated call is the real question here. Dispatching it immediately and
    holding only the gated one would split one turn's side effects across a human
    review window, which is worse than either extreme.
    """
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake, tool_uses=[
                {"id": "toolu_a", "name": GATED_TOOL, "input": {"target": "a"}},
                {"id": "toolu_b", "name": UNGATED_TOOL, "input": {"target": "b"}},
            ])
            hitl_id = await _execute(client, org)

            # Neither ran, including the ungated one.
            assert DISPATCHED == []
            row = await _row(app_db, org, hitl_id)
            assert row is not None
            assert row["resumption_json"]["pending_tool_use_ids"] == ["toolu_a", "toolu_b"]

            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert r.status_code == 200, r.text

            # Both ran, in the sampled order, with their own ids.
            assert [d[0] for d in DISPATCHED] == [GATED_TOOL, UNGATED_TOOL]
            assert [d[2].tool_use_id for d in DISPATCHED] == ["toolu_a", "toolu_b"]
            assert [d[1]["target"] for d in DISPATCHED] == ["a", "b"]
            # Both carry the resumption flag: both were read from storage.
            assert all(d[2].is_hitl_resumption for d in DISPATCHED)
    finally:
        await _cleanup(admin_conn, org)


@requires_redis
@requires_app_role
async def test_a_multi_call_turn_rejects_as_one_unit(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake, tool_uses=[
                {"id": "toolu_a", "name": GATED_TOOL, "input": {"target": "a"}},
                {"id": "toolu_b", "name": UNGATED_TOOL, "input": {"target": "b"}},
            ])
            hitl_id = await _execute(client, org)
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/reject", json=None, headers=_owner(org)
            )
            assert r.status_code == 200, r.text
            assert DISPATCHED == []
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 5. Expiry
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_an_expired_snapshot_is_refused_not_downgraded(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """410, and nothing executes.

    The alternative -- falling back to re-running the agent from `input` -- was
    rejected deliberately: it would execute a run nobody can still review, using
    a row the queue has already declared dead.
    """
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)
            after_defer = fake.attempts

            # Age the row past the 48h window the enqueue stamped on it.
            async with app_db.tenant_session(org) as conn:
                await conn.execute(
                    "UPDATE hitl_queue SET expires_at=$2 WHERE hitl_id=$1",
                    hitl_id, datetime.now(timezone.utc) - timedelta(minutes=1),
                )

            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert r.status_code == 410, r.text
            assert fake.attempts == after_defer
            assert DISPATCHED == []
            assert await _deliverable_ids(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 6. Id derivation: two suspensions under one proposal do not clobber
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_two_suspensions_at_different_turns_get_distinct_rows(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """The discriminator, proven against real primary keys.

    Two runs are used because a single run cannot defer twice at this commit --
    the gate raises and the raise ends the run (see hitl_id_for's docstring). So
    the id-collision question is asked the only way the code allows it to be
    asked, and the assertion is on what the discriminator actually controls: two
    suspensions at DIFFERENT loop iterations of the SAME proposal id derive
    different hitl_id and decision_id values, which is what stops a second
    suspension from clobbering the first if a future edit lets one run defer
    twice.
    """
    from skylize.app.decision_engine.events import decision_id_for, hitl_id_for

    proposal_id = uuid.uuid4()
    first_hitl = hitl_id_for(proposal_id, discriminator="turn:0")
    second_hitl = hitl_id_for(proposal_id, discriminator="turn:1")
    first_decision = decision_id_for(proposal_id, discriminator="turn:0")
    second_decision = decision_id_for(proposal_id, discriminator="turn:1")
    assert len({first_hitl, second_hitl, first_decision, second_decision}) == 4
    # And the undiscriminated derivation is byte-identical to what it always was,
    # so no id already written to `decisions` or `hitl_queue` moved.
    assert hitl_id_for(proposal_id) == uuid.uuid5(
        hitl_id_for.__globals__["_DECISION_NS"], f"hitl:{proposal_id}"
    )

    # Live half: two independent suspensions really do write two distinct rows.
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            first = await _execute(client, org)
            _program_gated_turn(fake)
            second = await _execute(client, org)
            assert first != second
            rows = [await _row(app_db, org, first), await _row(app_db, org, second)]
            assert all(r is not None and r["status"] == "pending" for r in rows)
            assert rows[0]["decision_id"] != rows[1]["decision_id"]  # type: ignore[index]
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 7. Ledger identity across an approval retry
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_replay_key_is_stable_across_a_retried_approval(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """The identity fix/toolproxy-ledger-commit-accounting needs.

    A transient failure after the claim releases the row to 'pending' so a human
    can retry, and the retry re-runs the whole agent. What must NOT change across
    those two attempts is the identity of the logical tool call, or a ledger
    keyed on it would commit twice for one approved action.
    """
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)

            # Attempt 1: the turn AFTER the resumed one fails at the provider, so
            # the row is released back to 'pending'. The gated call itself has
            # already been dispatched by then.
            from tests.fakes.fake_provider_api import status as fake_status
            fake.program(fake_status(500))
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert r.status_code >= 400
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "pending"

            # Attempt 2 succeeds.
            fake.program(success(text=_FINAL, message_id="msg_final_retry"))
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert r.status_code == 200, r.text

            assert len(DISPATCHED) == 2  # the same logical call, twice
            first, second = DISPATCHED[0][2], DISPATCHED[1][2]
            # correlation_id is fresh per attempt -- which is exactly why it
            # cannot be the ledger key ...
            assert first.correlation_id != second.correlation_id
            # ... and replay_key is not, which is why it can.
            assert first.replay_key() is not None
            assert first.replay_key() == second.replay_key()
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 8. Pre-0027 rows keep today's semantics
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_a_row_with_no_snapshot_still_approves_by_re_running(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """The drain guarantee: resumption_json IS NULL means today's behaviour.

    Simulated the way a pre-deploy row actually looks -- the column NULLed on a
    real row -- rather than by constructing one, so what is exercised is the same
    branch an in-flight ticket takes after this deploy.
    """
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)

            async with app_db.tenant_session(org) as conn:
                await conn.execute(
                    "UPDATE hitl_queue SET resumption_json=NULL WHERE hitl_id=$1", hitl_id
                )

            # The reviewer surface flips to the other shape for this row.
            r = await client.get("/api/v1/hitl", headers=_owner(org))
            item = r.json()["data"][0]
            assert item["approval_semantics"] == "rerun_request"
            assert item["pending_tool_calls"] is None

            # Approval re-runs the agent from `input`: the model is sampled
            # again, so the gate fires again and the run defers a second time.
            # That is the honest old behaviour, and it is not an error.
            _program_gated_turn(fake)
            before = fake.attempts
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert fake.attempts > before  # it really re-sampled
            assert r.status_code >= 400  # the re-run deferred again
            assert DISPATCHED == []
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# 9. A corrupt snapshot is PERMANENT, not a retry loop
# ---------------------------------------------------------------------------

@requires_redis
@requires_app_role
async def test_a_tampered_snapshot_terminates_the_row(
    admin_conn, app_db, fake_provider, gated_contract
) -> None:
    """Editing the stored ids so they no longer match the stored turn.

    The row must not dispatch a different set of calls than the human approved,
    and it must not loop back to 'pending' forever either -- the corruption
    fails identically on every retry, so the disposition is terminal.
    """
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            _program_gated_turn(fake)
            hitl_id = await _execute(client, org)

            row = await _row(app_db, org, hitl_id)
            assert row is not None
            tampered = dict(row["resumption_json"])
            tampered["pending_tool_use_ids"] = ["toolu_something_else"]
            async with app_db.tenant_session(org) as conn:
                await conn.execute(
                    "UPDATE hitl_queue SET resumption_json=$2 WHERE hitl_id=$1",
                    hitl_id, json.dumps(tampered),
                )

            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve", json=None, headers=_owner(org)
            )
            assert r.status_code >= 400
            assert DISPATCHED == []
            after = await _row(app_db, org, hitl_id)
            assert after is not None and after["status"] == "expired"
    finally:
        await _cleanup(admin_conn, org)
