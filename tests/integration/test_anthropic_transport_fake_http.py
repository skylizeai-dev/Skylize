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

Two deliberate mechanisms keep these deterministic WITHOUT hiding any HTTP:
  * ``x-should-retry: false`` — a header the real SDK honours to suppress its OWN
    internal retry (default max_retries=2). Setting it isolates the ADAPTER's
    retry policy, so the server sees exactly one request per adapter attempt. The
    dedicated amplification test OMITS it to reveal the SDK's compounding retries.
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
import functools
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
import pytest_asyncio

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.adapters.llm.gateway import (
    LLMAuthenticationError,
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMProviderUnavailable,
    LLMRateLimited,
)
from skylize.adapters.llm.spend_ceiling import SpendCeilingEnforcer
from skylize.app.audit.service import AuditService
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.cost_ledger import CostLedgerDAL
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


async def _seed_price(admin_conn: object, model: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, $1, $2, $3, $3, 'USD', 1, $4)
        """,
        _PROVIDER, model, SYNTH_RATE, _EPOCH,
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

        # x-should-retry:false suppresses the SDK's OWN retry so the server count
        # equals the ADAPTER's attempts. Retry-After=3 must drive the delay.
        fake.program(
            status(429, error_type="rate_limit_error",
                   headers={"retry-after": "3", "x-should-retry": "false"}),
            status(429, error_type="rate_limit_error",
                   headers={"retry-after": "3", "x-should-retry": "false"}),
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
                            headers={"retry-after": "1", "x-should-retry": "false"}))
        with _delay_spy(adapter) as delays:
            with pytest.raises(LLMRateLimited):
                await adapter.generate(_request(org))

        assert fake.attempts == 2  # bounded by llm_retry_max_attempts
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
            status(429, error_type="rate_limit_error",
                   headers={"retry-after": "2", "x-should-retry": "false"}),
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

        fake.program(status(503, error_type="api_error", headers={"x-should-retry": "false"}))
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
# G6 — 5xx: bounded retries, then LLMProviderUnavailable
# ===========================================================================


@requires_app_role
async def test_g6_5xx_bounded_then_provider_unavailable(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)  # max_attempts=3

        fake.program(status(500, error_type="api_error", headers={"x-should-retry": "false"}))
        with _delay_spy(adapter) as delays:
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate(_request(org))

        assert fake.attempts == 3  # bounded retries reached the socket
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
# G9 — timeout: clean cancellation, and NO ledger row
# ===========================================================================


@requires_app_role
async def test_g9_timeout_sync_egress_no_ledger_row(app_db, fake_provider, admin_conn) -> None:
    """Sync egress: a hanging server + a real short client timeout -> the SDK's own
    APITimeoutError, cleanly, and no ledger row (settle_cost is never reached).

    The timeout is injected onto the REAL SDK client constructor (a short timeout
    + max_retries=0) so the genuine SDK timeout path fires fast and once; the
    request recorder proves the socket was opened. See the step-12 findings for
    why a short timeout must be injected (the adapter exposes no timeout setting)."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        real_cls = anthropic.Anthropic
        short = functools.partial(real_cls, timeout=httpx.Timeout(1.0), max_retries=0)
        fake.program(hang(seconds=4.0))
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic", short):
            with pytest.raises(anthropic.APITimeoutError):
                await adapter.generate(_request(org))

        assert fake.attempts == 1  # the request DID reach the socket (real HTTP)
        assert await _ledger_rows(app_db, org) == []  # no partial/timeout row
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
# G10 — malformed/truncated body: typed error, no partial result accepted
# ===========================================================================


@requires_app_role
async def test_g10_malformed_body_no_partial_result(app_db, fake_provider, admin_conn) -> None:
    """A truncated 200 body: the SDK's own parser rejects it (json.JSONDecodeError,
    a FINDING -- the adapter maps no typed LLM error for this), and crucially NO
    partial result is accepted and NO ledger row is written."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        adapter, _bus, _repo, _ledger = _make_adapter(app_db, base_url)

        fake.program(malformed())
        with pytest.raises(json.JSONDecodeError):
            await adapter.generate(_request(org))

        assert fake.attempts == 1  # not retried (a parse error is not an APIStatusError)
        assert await _ledger_rows(app_db, org) == []  # no partial result recorded
    finally:
        await _cleanup(admin_conn, org)


# ===========================================================================
# FINDING (step 12) — the SDK performs its OWN retries on top of the adapter's.
# A single adapter attempt against a persistent 429/500 produces THREE HTTP
# requests (SDK default max_retries=2). This is invisible to any mocked test.
# ===========================================================================


@requires_app_role
async def test_finding_sdk_internal_retry_amplifies_attempts(app_db, fake_provider, admin_conn) -> None:
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_all(app_db, admin_conn, org)
        # ONE adapter attempt; the fake omits x-should-retry, so the SDK's own
        # retry policy is in force underneath the single adapter attempt.
        adapter, _bus, _repo, _ledger = _make_adapter(
            app_db, base_url, llm_retry_max_attempts=1)

        fake.program(status(429, error_type="rate_limit_error", headers={"retry-after": "0"}))
        with pytest.raises(LLMRateLimited):
            await adapter.generate(_request(org))
        # 1 adapter attempt amplified to 3 real HTTP requests by the SDK (1 + 2
        # internal retries). This is the flagged finding, proven over real HTTP.
        assert fake.attempts == 3

        # Same amplification on 5xx.
        fake.program(status(500, error_type="api_error"))
        with pytest.raises(LLMProviderUnavailable):
            await adapter.generate(_request(org))
        assert fake.attempts == 3

        assert await _ledger_rows(app_db, org) == []  # nothing served on either
    finally:
        await _cleanup(admin_conn, org)
