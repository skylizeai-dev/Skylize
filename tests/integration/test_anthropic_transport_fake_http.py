"""AnthropicAdapter transport tests over a REAL local HTTP server (key-ready proof).

These replace the SDK-mocked retry/failure tests: the adapter's ``base_url`` is
pointed at a real ASGI server (``tests/fakes/fake_provider_api``) on an ephemeral
port, so the Anthropic SDK genuinely opens a socket, sends a real request, parses
a real response, and maps real error statuses. Nothing here mocks the SDK client,
patches its transport, or stubs ``messages.create``. The request recorder proves
the SDK reached the socket, and each test shows the configured ``base_url``.

Real Postgres is wired (cost ledger + spend ceiling) exactly as bootstrap wires
it on the postgres backend, so the ledger-write seam and the ceiling gate run for
real. A missing ceiling row refuses every call, so each test SEEDS a ceiling for
its org via the AUDITED DAL setter (not a raw INSERT) — the real path (step 10).

Two facts keep these deterministic WITHOUT hiding any HTTP:
  * the adapter builds BOTH SDK clients with ``max_retries=0`` (owner decision
    D1 — the adapter is the SOLE retry authority), so the server-side request
    count IS the adapter's attempt count. NO test sends ``x-should-retry: false``
    (the header that previously suppressed the SDK's own internal retry, default
    max_retries=2): with the header absent, every attempt count below is direct
    proof the SDK's internal retry stays disabled.
  * a ``_retry_delay`` spy that records the delay the adapter WOULD sleep and then
    sleeps 0 — instead of patching the process-global ``asyncio.sleep`` (which
    would corrupt the in-process async server's own ``await asyncio.sleep``).

Demo mode is force-disabled and every adapter under test is asserted to be a real
AnthropicAdapter, never DemoLLMAdapter — a demo adapter would pass while proving
nothing (step 6).

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set. All prices are
EXPLICITLY SYNTHETIC (1 micro-USD/token), never real provider prices.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import anthropic
import pytest
import pytest_asyncio

from skylize.adapters.llm.anthropic_adapter import (
    AnthropicAdapter,
    _generate_input_chars,
)
from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.adapters.llm.gateway import (
    LLMAuthenticationError,
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateWithToolsRequest,
    LLMMalformedResponse,
    LLMMessage,
    LLMProviderUnavailable,
    LLMRateLimited,
    LLMTimeout,
)
from skylize.adapters.llm.spend_ceiling import (
    OrgSpendCeilingExceeded,
    SpendCeilingEnforcer,
    estimate_max_micros,
)
from skylize.app.audit.service import AuditService
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.cost_ledger import CostLedgerDAL, compute_cost_micros
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolDefinition
from tests.fakes.fake_provider_api import (
    hang,
    malformed,
    running_fake_provider,
    status,
    success,
)

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration

# Synthetic price: 1 micro-USD per token. NOT a real provider price.
SYNTH_RATE = 1_000_000  # micro-USD per 1e6 tokens == 1 micro-USD / token
_PROVIDER = "anthropic"
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)
# A ceiling far above any test call, so the gate always ALLOWS (we test transport,
# not the ceiling — the ceiling gate has its own suite).
BIG_CEILING = 10**12
# The concrete model id the adapter resolves "default" to. The fake echoes it back
# as message.model, so it must have a seeded model_pricing row.
MODEL = "fake-sonnet-mvp"


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _org() -> str:
    return f"http_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _demo_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force demo mode OFF for this suite (the root conftest defaults it ON).

    If it stayed on, a DemoLLMAdapter could be built and these tests would pass
    without ever reaching the fake server. Every adapter here is also constructed
    directly as AnthropicAdapter and asserted (see ``_make_adapter``)."""
    monkeypatch.setenv("SKYLIZE_LLM_DEMO_MODE", "false")


@pytest.fixture()
def fake_provider() -> Iterator[tuple[str, object]]:
    """A REAL fake Anthropic server on an ephemeral port; yields (base_url, fake)."""
    with running_fake_provider() as (base_url, fake):
        yield base_url, fake


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> "Iterator[Database]":
    """A Database pool connected as the NON-SUPERUSER app role (RLS-subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(base_url: str, *, api_key: str = "sk-test", **ov: object) -> Settings:
    d: dict[str, object] = dict(
        anthropic_api_key=api_key,
        anthropic_base_url=base_url,
        llm_demo_mode=False,
        llm_model_default=MODEL,
        llm_model_fast="fake-haiku-mvp",
        llm_model_reasoning="fake-opus-mvp",
        llm_price_sonnet_in=3.0,
        llm_price_sonnet_out=15.0,
        llm_price_haiku_in=0.80,
        llm_price_haiku_out=4.0,
        llm_price_opus_in=15.0,
        llm_price_opus_out=75.0,
        llm_retry_max_attempts=3,
        llm_retry_base_delay_seconds=1.0,
        llm_retry_max_delay_seconds=30.0,
        llm_retry_jitter_seconds=0.5,
    )
    d.update(ov)
    return Settings(**d)  # type: ignore[arg-type]


def _make_adapter(
    app_db: Database,
    base_url: str,
    *,
    api_key: str = "sk-test",
    tracer: object = None,
    **settings_ov: object,
) -> tuple[AnthropicAdapter, InMemoryEventBus, InMemoryAuditRepository, CostLedgerDAL]:
    """A PG-backed AnthropicAdapter wired like bootstrap does, pointed at the fake.

    Asserts the adapter is a real AnthropicAdapter and NOT a DemoLLMAdapter, so a
    test can never silently exercise the demo path (step 6)."""
    bus = InMemoryEventBus()
    repo = InMemoryAuditRepository()
    cost_ledger = CostLedgerDAL(app_db)
    enforcer = SpendCeilingEnforcer(
        ceiling_dal=OrgSpendCeilingDAL(app_db),
        cost_ledger=cost_ledger,
        audit=AuditService(bus, repo),
        bus=bus,
    )
    adapter = AnthropicAdapter(
        settings=_settings(base_url, api_key=api_key, **settings_ov),
        cost_ledger=cost_ledger,
        spend_ceiling=enforcer,
        tracer=tracer,
    )
    assert isinstance(adapter, AnthropicAdapter)
    assert not isinstance(adapter, DemoLLMAdapter)
    # The configured base_url is what points the SDK at the local fake.
    assert adapter._base_url == base_url
    assert adapter._client_kwargs()["base_url"] == base_url
    return adapter, bus, repo, cost_ledger


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_price(admin_conn: object, model: str, rate: int = SYNTH_RATE) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, $1, $2, $3, $3, 'USD', 1, $4)
        """,
        _PROVIDER, model, rate, _EPOCH,
    )


async def _seed_ceiling_audited(app_db: Database, org: str, micros: int) -> None:
    """Seed the ceiling via the AUDITED DAL setter (the real path), not raw SQL."""
    dal = OrgSpendCeilingDAL(app_db)
    await dal.set_ceiling(
        org_id=org,
        billing_period=_period(),
        ceiling_micros=micros,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _seed_all(app_db: Database, admin_conn: object, org: str) -> None:
    await _seed_tenant(admin_conn, org)
    await _seed_price(admin_conn, MODEL)
    await _seed_ceiling_audited(app_db, org, BIG_CEILING)


async def _cleanup(admin_conn: object, org: str) -> None:
    await admin_conn.execute("TRUNCATE ai_cost_ledger")  # type: ignore[attr-defined]
    await admin_conn.execute(  # type: ignore[attr-defined]
        "DELETE FROM org_spend_ceiling WHERE org_id = $1", org
    )
    await admin_conn.execute(  # type: ignore[attr-defined]
        "DELETE FROM model_pricing WHERE model = $1", MODEL
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = $1", org)  # type: ignore[attr-defined]


async def _ledger_rows(app_db: Database, org: str) -> list[dict[str, object]]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT model, idempotency_key, cost_micros FROM ai_cost_ledger ORDER BY created_at"
        )
    return [dict(r) for r in rows]


async def _ledger_rows_priced(app_db: Database, org: str) -> list[dict[str, object]]:
    """Ledger rows including the FROZEN price columns — the rate actually applied."""
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT model, idempotency_key, cost_micros, input_tokens, output_tokens, "
            "input_price_micros_per_mtok, output_price_micros_per_mtok "
            "FROM ai_cost_ledger ORDER BY created_at"
        )
    return [dict(r) for r in rows]


def _request(org: str, **kw: object) -> LLMGenerateRequest:
    d: dict[str, object] = dict(
        model="default",
        prompt="Summarize the meeting notes, please.",
        requested_max_tokens=64,
        governance_token_id=uuid.uuid4(),
        org_id=org,
        correlation_id=uuid.uuid4(),
        agent_id="agent_http",
    )
    d.update(kw)
    return LLMGenerateRequest(**d)  # type: ignore[arg-type]


def _tools_request(org: str, messages: list[LLMMessage], **kw: object) -> LLMGenerateWithToolsRequest:
    d: dict[str, object] = dict(
        model="default",
        messages=messages,
        requested_max_tokens=64,
        governance_token_id=uuid.uuid4(),
        org_id=org,
        correlation_id=uuid.uuid4(),
        agent_id="agent_http",
    )
    d.update(kw)
    return LLMGenerateWithToolsRequest(**d)  # type: ignore[arg-type]


@contextlib.contextmanager
def _delay_spy(adapter: AnthropicAdapter) -> Iterator[list[float]]:
    """Capture the delays the adapter WOULD sleep between retries; sleep 0 instead.

    Records ``_retry_delay(...)`` return values without touching the process-global
    ``asyncio.sleep`` (which the in-process async fake server also uses)."""
    real = adapter._retry_delay
    captured: list[float] = []

    def spy(attempt: int, exc: anthropic.APIStatusError) -> float:
        captured.append(round(real(attempt, exc), 4))
        return 0.0

    with patch.object(adapter, "_retry_delay", side_effect=spy):
        yield captured


# A minimal tool for the tool-use round-trip (never actually invoked here; the
# adapter only serializes its schema and the test builds the tool_result itself).
from pydantic import BaseModel  # noqa: E402


class _ToolIn(BaseModel):
    query: str


class _ToolOut(BaseModel):
    answer: str


async def _tool_handler(_inp: BaseModel, _ctx: object) -> BaseModel:  # pragma: no cover
    return _ToolOut(answer="unused")


def _demo_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="do_thing",
        name="do_thing",
        description="Does a thing for the test.",
        input_schema=_ToolIn,
        output_schema=_ToolOut,
        category="compute",
        handler=_tool_handler,  # type: ignore[arg-type]
    )


# ===========================================================================
# Step 6 gate — the adapter under test is a real AnthropicAdapter, not demo
# ===========================================================================


@requires_app_role
async def test_adapter_under_test_is_anthropic_not_demo(app_db, fake_provider, admin_conn) -> None:
    import os

    assert os.environ.get("SKYLIZE_LLM_DEMO_MODE") == "false"
    base_url, _fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        assert type(adapter).__name__ == "AnthropicAdapter"
        assert isinstance(adapter, AnthropicAdapter)
        assert not isinstance(adapter, DemoLLMAdapter)
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# REAL HTTP evidence — the SDK opened a socket to the configured base_url
# ===========================================================================


@requires_app_role
async def test_real_http_the_sdk_hits_the_local_server(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        assert adapter._base_url == base_url  # the configured base_url IS the fake

        fake.program(success(text="hi", message_id="msg_evidence"))
        resp = await adapter.generate(_request(org))
        assert resp.text == "hi"

        # The request recorder proves a genuine HTTP request reached the socket.
        assert fake.attempts == 1
        rec = fake.message_requests[0]
        assert rec.method == "POST"
        assert rec.path == "/v1/messages"  # the SDK appended /v1/messages to base_url
        # A genuine Anthropic SDK request carries these headers over the wire.
        assert "anthropic-version" in rec.headers
        assert "anthropic" in rec.headers.get("user-agent", "").lower()
        assert rec.json_body is not None and rec.json_body["model"] == MODEL
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G1 — non-streaming success; usage parsed; ONE ledger row (resolved model id +
#      response id as idempotency key)
# ===========================================================================


@requires_app_role
async def test_g1_success_usage_parsed_one_ledger_row(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(success(
            text="Meeting summary.", input_tokens=123, output_tokens=45,
            message_id="msg_g1_resp",
        ))
        resp = await adapter.generate(_request(org))

        assert resp.text == "Meeting summary."
        assert resp.usage.prompt_tokens == 123
        assert resp.usage.completion_tokens == 45
        assert resp.usage.total_tokens == 168
        assert resp.concrete_model == MODEL
        assert fake.attempts == 1  # real single HTTP round trip

        rows = await _ledger_rows(app_db, org)
        assert len(rows) == 1  # exactly one ledger row written
        assert rows[0]["model"] == MODEL  # the RESOLVED model id off the response
        assert rows[0]["idempotency_key"] == "msg_g1_resp"  # the response id
        # 123 in + 45 out at 1 micro/token synthetic rate == 168 micro-USD.
        assert rows[0]["cost_micros"] == 168
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G2 — tool_use round-trip: request with tools -> tool_use -> tool_result
#      continuation -> final text
# ===========================================================================


@requires_app_role
async def test_g2_tool_use_round_trip(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        tool = _demo_tool()

        # Turn 1: the model asks to use the tool.
        fake.program(
            success(tool_use={"id": "toolu_g2", "name": "do_thing", "input": {"query": "hi"}},
                    message_id="msg_g2_a"),
            success(text="All done: 42.", message_id="msg_g2_b"),
        )
        first = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="please do the thing")]),
        ])
        r1 = await adapter.generate_with_tools(first, tools=[tool])
        assert r1.stop_reason == "tool_use"
        tool_blocks = [b for b in r1.content if b.kind == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].tool_use_id == "toolu_g2"
        assert tool_blocks[0].tool_name == "do_thing"  # mapped back from the sanitized name
        assert tool_blocks[0].tool_input == {"query": "hi"}

        # Turn 2: the runtime returns a tool_result and the model gives final text.
        second = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="please do the thing")]),
            LLMMessage(role="assistant", content=list(r1.content)),
            LLMMessage(role="user", content=[LLMContentBlock(
                kind="tool_result", tool_use_id="toolu_g2", tool_output=json.dumps({"answer": "42"}))]),
        ])
        r2 = await adapter.generate_with_tools(second, tools=[tool])
        assert r2.text == "All done: 42."
        assert r2.stop_reason == "end_turn"

        # The recorder proves the tool_result actually crossed the wire on turn 2.
        assert fake.attempts == 2
        sent2 = fake.message_requests[1].json_body
        assert sent2 is not None
        result_blocks = [
            blk
            for msg in sent2["messages"]
            for blk in msg["content"]
            if blk.get("type") == "tool_result"
        ]
        assert len(result_blocks) == 1
        assert result_blocks[0]["tool_use_id"] == "toolu_g2"
        assert "42" in json.dumps(result_blocks[0]["content"])

        # Both served calls wrote a ledger row (distinct response ids => distinct keys).
        rows = await _ledger_rows(app_db, org)
        assert {r["idempotency_key"] for r in rows} == {"msg_g2_a", "msg_g2_b"}
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G3 — multi-turn conversation state arrives intact at the server
# ===========================================================================


@requires_app_role
async def test_g3_multi_turn_state_arrives_intact(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(success(text="ack", message_id="msg_g3"))
        history = [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="first user turn")]),
            LLMMessage(role="assistant", content=[LLMContentBlock(kind="text", text="assistant reply")]),
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="second user turn")]),
        ]
        await adapter.generate_with_tools(_tools_request(org, history), tools=[_demo_tool()])

        sent = fake.message_requests[0].json_body
        assert sent is not None
        roles = [m["role"] for m in sent["messages"]]
        texts = [m["content"][0]["text"] for m in sent["messages"]]
        assert roles == ["user", "assistant", "user"]
        assert texts == ["first user turn", "assistant reply", "second user turn"]
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G5 — 429 with Retry-After: assert attempts that reached the server and that the
#      delay honoured the header
# ===========================================================================


@requires_app_role
async def test_g5_429_retry_after_attempts_and_delay(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        # No x-should-retry header: max_retries=0 (D1) already guarantees the
        # server count equals the ADAPTER's attempts. Retry-After=3 must drive
        # the delay.
        fake.program(
            status(429, error_type="rate_limit_error", headers={"retry-after": "3"}),
            status(429, error_type="rate_limit_error", headers={"retry-after": "3"}),
            success(text="finally ok", message_id="msg_g5_ok"),
        )
        with _delay_spy(adapter) as delays:
            resp = await adapter.generate(_request(org))

        assert resp.text == "finally ok"
        assert fake.attempts == 3  # two 429s then success reached the socket
        # The adapter slept the Retry-After header value (3s), NOT jittered backoff.
        assert delays == [3.0, 3.0]

        rows = await _ledger_rows(app_db, org)
        assert len(rows) == 1  # only the served (final) call recorded
        assert rows[0]["idempotency_key"] == "msg_g5_ok"
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_g5b_429_exhausted_rate_limited_no_ledger_row(app_db, fake_provider, admin_conn) -> None:
    """429 that never clears: bounded attempts, LLMRateLimited, and NO ledger row."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_retry_max_attempts=2)

        fake.program(status(429, error_type="rate_limit_error",
                            headers={"retry-after": "1"}))
        with _delay_spy(adapter) as delays:
            with pytest.raises(LLMRateLimited):
                await adapter.generate(_request(org))

        assert fake.attempts == 2  # bounded by llm_retry_max_attempts, no SDK amplification
        assert delays == [1.0]  # one sleep between the two attempts, Retry-After honoured
        assert await _ledger_rows(app_db, org) == []  # nothing served => no ledger row
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G5/G6 on the ASYNC (generate_with_tools) egress — the retry helper is shared
# by both egresses, so this proves the async SDK client's real-HTTP retry too.
# ===========================================================================


@requires_app_role
async def test_async_egress_429_retried_then_succeeds(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(
            status(429, error_type="rate_limit_error", headers={"retry-after": "2"}),
            success(text="tools ok", message_id="msg_async_ok"),
        )
        req = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")]),
        ])
        with _delay_spy(adapter) as delays:
            resp = await adapter.generate_with_tools(req, tools=[_demo_tool()])

        assert resp.text == "tools ok"
        assert fake.attempts == 2  # one 429 then success on the async client
        assert delays == [2.0]  # Retry-After honoured on the async egress too
        rows = await _ledger_rows(app_db, org)
        assert [r["idempotency_key"] for r in rows] == ["msg_async_ok"]
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_async_egress_5xx_exhausted_provider_unavailable(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url, llm_retry_max_attempts=2)

        fake.program(status(503, error_type="api_error"))
        req = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")]),
        ])
        with _delay_spy(adapter):
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate_with_tools(req, tools=[_demo_tool()])

        assert fake.attempts == 2  # bounded on the async egress
        assert await _ledger_rows(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G6 — 5xx: bounded retries, then LLMProviderUnavailable. With x-should-retry
# ABSENT, total HTTP attempts == llm_retry_max_attempts EXACTLY — the direct
# proof that no SDK-internal retry runs underneath the adapter's policy (D1).
# ===========================================================================


@requires_app_role
async def test_g6_5xx_bounded_then_provider_unavailable(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)  # max_attempts=3

        fake.program(status(500, error_type="api_error"))
        with _delay_spy(adapter) as delays:
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate(_request(org))

        # EXACTLY llm_retry_max_attempts requests reached the socket, no more:
        # the SDK contributed zero hidden attempts under the adapter's three.
        assert fake.attempts == 3
        assert len(delays) == 2  # two jittered backoff sleeps between three attempts
        assert all(d > 0 for d in delays)  # 5xx uses backoff, not a zero delay
        assert await _ledger_rows(app_db, org) == []  # nothing served
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G7 — 400 invalid_request: typed error, exactly ONE attempt
# ===========================================================================


@requires_app_role
async def test_g7_400_typed_error_one_attempt(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(status(400, error_type="invalid_request_error", message="bad request"))
        with pytest.raises(anthropic.BadRequestError):  # the SDK's typed 400 error
            await adapter.generate(_request(org))

        assert fake.attempts == 1  # 400 is never retried (adapter re-raises immediately)
        assert await _ledger_rows(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G8 — 401: fail closed; no key material in the exception, __cause__, logs, or
#      trace attributes; no ledger row
# ===========================================================================


@requires_app_role
async def test_g8_401_fail_closed_no_key_material(app_db, fake_provider, admin_conn, caplog) -> None:
    base_url, fake = fake_provider
    org = _org()
    secret = "sk-SECRETKEY-DO-NOT-LEAK-6f1c9a"
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_span.return_value = span
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, api_key=secret, tracer=tracer)

        fake.program(status(401, error_type="authentication_error", message="invalid key"))
        with caplog.at_level(logging.DEBUG, logger="skylize"):
            with pytest.raises(LLMAuthenticationError) as ei:
                await adapter.generate(_request(org))

        assert fake.attempts == 1  # 401 fails closed immediately, no retry
        # No key material anywhere on the failure path.
        assert secret not in str(ei.value)
        assert ei.value.__cause__ is None  # SDK exception not chained
        assert all(secret not in rec.getMessage() for rec in caplog.records)
        span_attrs = " ".join(str(c) for c in span.set_attribute.call_args_list)
        assert secret not in span_attrs
        assert await _ledger_rows(app_db, org) == []  # fail closed => nothing recorded
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G9 — timeout: typed LLMTimeout within the CONFIGURED bound (D2/D3), exactly
# one HTTP request (a possibly-billed call is never re-sent), NO ledger row
# ===========================================================================


@requires_app_role
async def test_g9_hang_typed_timeout_within_configured_bound_sync(app_db, fake_provider, admin_conn) -> None:
    """Sync egress: a hanging server + Settings.llm_timeout_seconds=1.0 (no
    constructor patching — the knob exists now, owner decision D3) raises the
    TYPED LLMTimeout well before the 4s hang completes, chains the SDK's
    APITimeoutError, sends exactly ONE request (a timed-out call may already be
    billed provider-side, so it is never re-sent — owner decision D2), and
    writes no ledger row."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_timeout_seconds=1.0)

        fake.program(hang(seconds=4.0))
        started = time.monotonic()
        with pytest.raises(LLMTimeout) as ei:
            await adapter.generate(_request(org))
        elapsed = time.monotonic() - started

        # The configured 1s timeout fired — not the 4s hang, not the SDK's ~600s.
        assert elapsed < 3.0
        assert isinstance(ei.value.__cause__, anthropic.APITimeoutError)  # chained
        assert fake.attempts == 1  # one socket open; the timeout was NOT retried
        assert await _ledger_rows(app_db, org) == []  # no partial/timeout row
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_g9_hang_typed_timeout_async_egress(app_db, fake_provider, admin_conn) -> None:
    """Async (tools) egress: the SAME Settings-driven timeout raises the same
    typed LLMTimeout (the retry helper is shared), one request, no ledger row."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_timeout_seconds=1.0)

        fake.program(hang(seconds=4.0))
        req = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")]),
        ])
        with pytest.raises(LLMTimeout) as ei:
            await adapter.generate_with_tools(req, tools=[_demo_tool()])

        assert isinstance(ei.value.__cause__, anthropic.APITimeoutError)
        assert fake.attempts == 1  # never retried on the async egress either
        assert await _ledger_rows(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_g9_timeout_async_egress_clean_cancellation(app_db, fake_provider, admin_conn) -> None:
    """Async (tools) egress: cancelling a hanging call is clean and writes no row."""
    import asyncio

    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(hang(seconds=4.0))
        req = _tools_request(org, [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")]),
        ])
        with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):
            await asyncio.wait_for(adapter.generate_with_tools(req, tools=[_demo_tool()]), timeout=1.5)

        assert fake.attempts == 1  # the SDK opened the socket before we cancelled
        assert await _ledger_rows(app_db, org) == []  # nothing settled
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# G10 — malformed/truncated body: TYPED LLMMalformedResponse (D4), the parser
# error chained, exactly one request, no partial result, NO ledger row
# ===========================================================================


@requires_app_role
async def test_g10_malformed_body_typed_error_no_partial_result(app_db, fake_provider, admin_conn) -> None:
    """A truncated 200 body maps to the typed LLMMalformedResponse (owner
    decision D4) with the SDK parser's json.JSONDecodeError chained. NOT
    retried (one request only): a served-but-unparseable 200 means the provider
    completed — and billed — the generation, so a retry would double-spend. No
    partial result is accepted and NO ledger row is written."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(malformed())
        with pytest.raises(LLMMalformedResponse) as ei:
            await adapter.generate(_request(org))

        assert isinstance(ei.value.__cause__, json.JSONDecodeError)  # chained
        assert fake.attempts == 1  # a parse failure is never retried
        assert await _ledger_rows(app_db, org) == []  # no partial result recorded
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# D1 (inverse of the step-12 finding) — the SDK's internal retry is DISABLED.
# REPLACES test_finding_sdk_internal_retry_amplifies_attempts, which proved the
# pre-fix amplification (1 adapter attempt -> 3 HTTP requests, SDK default
# max_retries=2). With max_retries=0 and x-should-retry ABSENT, exactly ONE
# HTTP request reaches the server per adapter attempt.
# ===========================================================================


@requires_app_role
async def test_d1_sdk_internal_retry_disabled_one_request_per_attempt(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        # ONE adapter attempt against a persistent 429 with NO x-should-retry
        # header: any SDK-internal retry would show up as extra requests.
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_retry_max_attempts=1)

        fake.program(status(429, error_type="rate_limit_error", headers={"retry-after": "0"}))
        with pytest.raises(LLMRateLimited):
            await adapter.generate(_request(org))
        # Exactly one real HTTP request per adapter attempt (was 3 pre-fix).
        assert fake.attempts == 1

        # Same on a persistent 500.
        fake.program(status(500, error_type="api_error"))
        with pytest.raises(LLMProviderUnavailable):
            await adapter.generate(_request(org))
        assert fake.attempts == 1

        assert await _ledger_rows(app_db, org) == []  # nothing served on either
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# Connection failure (step 8) — a real refused connection maps to the typed
# LLMProviderUnavailable immediately (no retry: this seam cannot prove the
# provider never received — and will not bill — the request), no ledger row.
# ===========================================================================


@requires_app_role
async def test_connection_refused_maps_to_provider_unavailable(app_db, admin_conn) -> None:
    org = _org()
    # Start and fully stop a real server so its ephemeral port now REFUSES
    # connections — a genuine transport-level failure, not a mocked one.
    with running_fake_provider() as (base_url, _fake):
        pass
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        with pytest.raises(LLMProviderUnavailable) as ei:
            await adapter.generate(_request(org))

        cause = ei.value.__cause__
        assert isinstance(cause, anthropic.APIConnectionError)  # chained SDK error
        assert not isinstance(cause, anthropic.APITimeoutError)  # the non-timeout path
        assert await _ledger_rows(app_db, org) == []  # nothing served, nothing recorded
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# Key material — the NEW error paths (timeout, malformed body, connection
# failure) leak no key material into the message, anywhere down the
# __cause__/__context__ chain, any log record, or any trace attribute.
# ===========================================================================


def _assert_exception_chain_clean(exc: BaseException, secret: str) -> None:
    """Walk the full __cause__/__context__ chain; no link may carry the key."""
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        assert secret not in str(node)
        assert secret not in repr(node)
        node = node.__cause__ or node.__context__


@requires_app_role
async def test_new_error_paths_carry_no_key_material(app_db, fake_provider, admin_conn, caplog) -> None:
    base_url, fake = fake_provider
    org = _org()
    secret = "sk-SECRETKEY-DO-NOT-LEAK-9b2d41"
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_span.return_value = span
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, api_key=secret, tracer=tracer, llm_timeout_seconds=1.0)

        with caplog.at_level(logging.DEBUG):
            # Timeout path (D2).
            fake.program(hang(seconds=4.0))
            with pytest.raises(LLMTimeout) as timeout_ei:
                await adapter.generate(_request(org))
            # Malformed-body path (D4).
            fake.program(malformed())
            with pytest.raises(LLMMalformedResponse) as malformed_ei:
                await adapter.generate(_request(org))

        # Connection-failure path — same secret-keyed adapter, dead port.
        with running_fake_provider() as (dead_url, _f):
            pass
        adapter2, _bus2, _repo2, _ledger2 = _make_adapter(
            app_db, dead_url, api_key=secret, tracer=tracer)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(LLMProviderUnavailable) as conn_ei:
                await adapter2.generate(_request(org))

        for ei in (timeout_ei, malformed_ei, conn_ei):
            _assert_exception_chain_clean(ei.value, secret)
        assert all(secret not in rec.getMessage() for rec in caplog.records)
        span_attrs = " ".join(str(c) for c in span.set_attribute.call_args_list)
        assert secret not in span_attrs
        assert await _ledger_rows(app_db, org) == []  # none of the failures settled
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# D2 — ONE PRICE RESOLUTION PER CALL (owner decisions DEC-A .. DEC-D)
#
# The pre-call gate priced the REQUESTED concrete id; the post-call ledger write
# re-priced from the provider's RESOLVED id. Anthropic resolves aliases, so the
# two are not always the same id — which meant the call could be gated at one
# rate and charged at another, or, when model_pricing carried no row for the
# resolved form, PricingNotFound aborted the ledger write AFTER the provider had
# already billed: money spent, untracked.
#
# Everything below runs the real SDK against the fake server over a real socket
# and reads the real ai_cost_ledger back through Postgres.
# ===========================================================================

# An id the provider "resolves" to that model_pricing does NOT carry.
UNLISTED_MODEL = "fake-sonnet-mvp-unlisted"
# An id model_pricing DOES carry, at a deliberately different (10x) rate — the
# case where re-resolving would silently charge a rate the ceiling never saw.
DIVERGENT_MODEL = "fake-sonnet-mvp-20990101"
DIVERGENT_RATE = SYNTH_RATE * 10


async def _cleanup_extra_prices(admin_conn: object, *models: str) -> None:
    for model in models:
        try:
            await admin_conn.execute(  # type: ignore[attr-defined]
                "DELETE FROM model_pricing WHERE model = $1", model
            )
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


@requires_app_role
async def test_dec_b_unpriced_resolved_id_still_writes_exactly_one_row(
    app_db, fake_provider, admin_conn, caplog
) -> None:
    """DEC-B: after real spend, a ledger row is ALWAYS written.

    The gate prices the requested id (seeded). The provider reports serving an
    id with NO model_pricing row at all. Before DEC-A this raised PricingNotFound
    inside record_cost, _settle_cost re-raised, the caller got a 5xx, and NO
    ai_cost_ledger row existed for a call the account had already been billed for.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)  # seeds MODEL only
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(success(
            text="Served by an alias we do not price.",
            model=UNLISTED_MODEL,
            input_tokens=1_000, output_tokens=500,
            message_id="msg_unlisted_1",
        ))
        with caplog.at_level(logging.ERROR, logger="skylize"):
            # The call must SUCCEED. A raise here is the defect: the caller saw a
            # 502 while the provider had already billed.
            resp = await adapter.generate(_request(org))

        assert resp.text == "Served by an alias we do not price."
        assert fake.attempts == 1  # one real HTTP round trip, and it was billed

        rows = await _ledger_rows_priced(app_db, org)
        assert len(rows) == 1, "money was spent; exactly one ledger row must exist"
        # DEC-C — the row records what actually SERVED the request.
        assert rows[0]["model"] == UNLISTED_MODEL
        assert rows[0]["idempotency_key"] == "msg_unlisted_1"
        # DEC-A — priced from the GATE's snapshot (the seeded synthetic rate).
        assert rows[0]["input_price_micros_per_mtok"] == SYNTH_RATE
        assert rows[0]["output_price_micros_per_mtok"] == SYNTH_RATE
        assert rows[0]["cost_micros"] == 1_500  # 1500 tokens at 1 micro/token
        # ...and the response reports the SAME number the row stores.
        assert resp.cost_usd_micros == rows[0]["cost_micros"]

        # DEC-D — reported at ERROR with org, correlation, both ids, applied price.
        diverged = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and "llm_resolved_model_diverged" in r.getMessage()
        ]
        assert diverged, "an unpriced resolved id must be reported at ERROR"
        msg = diverged[0].getMessage()
        assert org in msg
        assert MODEL in msg and UNLISTED_MODEL in msg
        assert str(SYNTH_RATE) in msg
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_dec_a_diverged_id_priced_at_the_gate_rate_not_its_own(
    app_db, fake_provider, admin_conn, caplog
) -> None:
    """DEC-A: when BOTH ids are priced at DIFFERENT rates, the GATE's rate wins.

    This is the divergence that never raised and so never announced itself: the
    ceiling was checked at the requested id's rate and the ledger charged at the
    resolved id's. One call, two rates. The gate's snapshot is now the single
    price, and the divergence is reported rather than absorbed.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        await _seed_price(admin_conn, DIVERGENT_MODEL, rate=DIVERGENT_RATE)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(success(
            model=DIVERGENT_MODEL,
            input_tokens=1_000, output_tokens=1_000,
            message_id="msg_divergent_1",
        ))
        with caplog.at_level(logging.ERROR, logger="skylize"):
            resp = await adapter.generate(_request(org))

        rows = await _ledger_rows_priced(app_db, org)
        assert len(rows) == 1
        assert rows[0]["model"] == DIVERGENT_MODEL          # DEC-C
        assert rows[0]["input_price_micros_per_mtok"] == SYNTH_RATE   # DEC-A
        assert rows[0]["output_price_micros_per_mtok"] == SYNTH_RATE  # DEC-A
        assert rows[0]["cost_micros"] == 2_000              # 2000 tok x 1 micro
        assert rows[0]["cost_micros"] != 20_000             # NOT the 10x rate
        assert resp.cost_usd_micros == 2_000

        assert any(
            "llm_resolved_model_diverged" in r.getMessage()
            for r in caplog.records if r.levelno >= logging.ERROR
        )
    finally:
        await _cleanup_extra_prices(admin_conn, DIVERGENT_MODEL)
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_ceiling_estimate_and_ledger_row_use_the_same_rate(
    app_db, fake_provider, admin_conn
) -> None:
    """One call, one rate: the pre-call ceiling estimate and the ledger row agree.

    The ceiling is seeded at EXACTLY the estimate computed from the seeded rate,
    then one micro-USD below it. Passing at the first and refusing at the second
    pins the estimate to that rate to the micro; the surviving call's ledger row
    is then asserted against ``compute_cost_micros`` at the SAME rate. Both
    numbers therefore provably derive from one price snapshot.
    """
    base_url, fake = fake_provider
    request = _request(_org())  # shape only; org is replaced per sub-case below
    estimate = estimate_max_micros(
        input_chars=_generate_input_chars(request),
        requested_max_tokens=request.requested_max_tokens,
        input_price_micros_per_mtok=SYNTH_RATE,
        output_price_micros_per_mtok=SYNTH_RATE,
    )

    # (a) ceiling one micro BELOW the estimate -> refused before egress.
    org_tight = _org()
    try:
        await _seed_tenant(admin_conn, org_tight)
        await _seed_price(admin_conn, MODEL)
        await _seed_ceiling_audited(app_db, org_tight, estimate - 1)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        fake.program(success(message_id="msg_should_not_happen"))
        with pytest.raises(OrgSpendCeilingExceeded) as ei:
            await adapter.generate(_request(org_tight))
        assert ei.value.estimated_micros == estimate
        assert fake.attempts == 0  # refused BEFORE any egress
        assert await _ledger_rows(app_db, org_tight) == []
    finally:
        await _cleanup(admin_conn, org_tight)

    # (b) ceiling EXACTLY at the estimate -> allowed; the row uses that same rate.
    org_ok = _org()
    try:
        await _seed_tenant(admin_conn, org_ok)
        await _seed_price(admin_conn, MODEL)
        await _seed_ceiling_audited(app_db, org_ok, estimate)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        fake.program(success(input_tokens=37, output_tokens=11, message_id="msg_same_rate"))
        resp = await adapter.generate(_request(org_ok))

        rows = await _ledger_rows_priced(app_db, org_ok)
        assert len(rows) == 1
        assert rows[0]["input_price_micros_per_mtok"] == SYNTH_RATE
        assert rows[0]["output_price_micros_per_mtok"] == SYNTH_RATE
        assert rows[0]["cost_micros"] == compute_cost_micros(
            input_tokens=37,
            output_tokens=11,
            input_price_micros_per_mtok=SYNTH_RATE,
            output_price_micros_per_mtok=SYNTH_RATE,
        )
        assert resp.cost_usd_micros == rows[0]["cost_micros"]
    finally:
        await _cleanup(admin_conn, org_ok)


@requires_app_role
async def test_happy_path_cost_unchanged_by_single_resolution(
    app_db, fake_provider, admin_conn, caplog
) -> None:
    """Same ids in and out: cost is byte-identical to before DEC-A, and quiet.

    Mirrors G1's call exactly (123 in / 45 out at the synthetic 1 micro/token
    rate == 168 micro-USD) and additionally pins the frozen price columns and
    the absence of a divergence report — a no-divergence call must not change
    in any observable way.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(success(
            text="Meeting summary.", input_tokens=123, output_tokens=45,
            message_id="msg_happy_same",
        ))
        with caplog.at_level(logging.ERROR, logger="skylize"):
            resp = await adapter.generate(_request(org))

        assert resp.cost_usd_micros == 168
        rows = await _ledger_rows_priced(app_db, org)
        assert len(rows) == 1
        assert rows[0]["model"] == MODEL  # provider echoed the requested id
        assert rows[0]["cost_micros"] == 168
        assert rows[0]["input_price_micros_per_mtok"] == SYNTH_RATE
        assert rows[0]["output_price_micros_per_mtok"] == SYNTH_RATE
        assert not any(
            "llm_resolved_model_diverged" in r.getMessage() for r in caplog.records
        )
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# D5 — llm_retry_max_attempts is a TOTAL attempt count. A value of 1 is the
#      legitimate "no retries" setting and must send EXACTLY ONE real request.
#      (0 is refused by Settings; see tests/unit/test_anthropic_adapter.py.)
# ===========================================================================


@requires_app_role
async def test_one_attempt_budget_sends_exactly_one_http_request(
    app_db, fake_provider, admin_conn
) -> None:
    """One attempt, one socket write, on both the success and the failure path.

    Counted server-side: the adapter builds both SDK clients with max_retries=0,
    so the fake's request count IS the adapter's attempt count.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_retry_max_attempts=1
        )
        assert adapter._retry_max_attempts == 1

        # Success: exactly one request reaches the server.
        fake.program(success(text="one shot", message_id="msg_one_attempt"))
        resp = await adapter.generate(_request(org))
        assert resp.text == "one shot"
        assert fake.attempts == 1

        # A retryable status is NOT retried at this budget: still one request.
        fake.program(status(500, message="boom"))
        with _delay_spy(adapter) as delays:
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate(_request(org))
        assert fake.attempts == 1
        assert delays == []  # no backoff was even computed

        # And one ledger row for the one served call (the 500 settles nothing).
        rows = await _ledger_rows(app_db, org)
        assert len(rows) == 1
        assert rows[0]["idempotency_key"] == "msg_one_attempt"
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# P3 — ONE SDK client per adapter over a REAL socket, released by aclose().
#      Both egresses used to construct a client per call and never close it;
#      in the tool loop that leaked one AsyncAnthropic per iteration, each
#      holding its own TCP pool until GC finalized it.
# ===========================================================================


@requires_app_role
async def test_repeated_real_calls_reuse_one_client_object(
    app_db, fake_provider, admin_conn
) -> None:
    """Three real HTTP round trips, one client object, one connection pool.

    Identity is asserted on the object the SDK actually used, and the server's
    own request count proves the calls really went out.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        assert adapter._sync_client_instance is None  # nothing opened yet

        fake.program(success(text="first", message_id="msg_reuse_1"))
        await adapter.generate(_request(org))
        first_client = adapter._sync_client_instance
        assert first_client is not None

        fake.program(success(text="second", message_id="msg_reuse_2"))
        await adapter.generate(_request(org))
        fake.program(success(text="third", message_id="msg_reuse_3"))
        await adapter.generate(_request(org))

        assert adapter._sync_client_instance is first_client
        assert fake.attempts == 1  # program() resets the recorder; 1 per program

        # Three distinct served calls -> three ledger rows, so reuse did not
        # collapse or drop a call.
        keys = {r["idempotency_key"] for r in await _ledger_rows(app_db, org)}
        assert keys == {"msg_reuse_1", "msg_reuse_2", "msg_reuse_3"}

        # aclose() really closes the pool; the SDK reports it.
        assert first_client.is_closed() is False
        await adapter.aclose()
        assert first_client.is_closed() is True
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_tool_egress_reuses_one_async_client_across_iterations(
    app_db, fake_provider, admin_conn
) -> None:
    """The tool-loop leak, over real HTTP: N iterations, ONE async client."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)
        tools = [_demo_tool()]

        clients = []
        for i in range(3):
            fake.program(success(text=f"turn {i}", message_id=f"msg_tools_reuse_{i}"))
            await adapter.generate_with_tools(
                _tools_request(
                    org,
                    [LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")])],
                ),
                tools,
            )
            clients.append(adapter._async_client_instance)

        assert clients[0] is not None
        assert clients.count(clients[0]) == 3, "a new async client was built per iteration"

        assert clients[0].is_closed() is False
        await adapter.aclose()
        assert clients[0].is_closed() is True
    finally:
        await _cleanup(admin_conn, org)
