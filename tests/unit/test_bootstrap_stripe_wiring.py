"""Bootstrap wiring of the existing `stripe.refund` tool, TEST MODE ONLY (OD8).

What is pinned:
  * flag off (the default) leaves the container exactly as it was before this
    wiring: no `stripe.refund`, no Stripe store or refund limits on the proxy,
    and the same registered tool-id set as a Settings with no Stripe input;
  * flag on with a fake test key registers `stripe.refund` and nothing else
    new, and both the tool builder and the proxy get `stripe_livemode=False`;
  * `resolve_stripe_config` refuses a live / half-set configuration even when
    Settings was mutated after construction (defence in depth).

Memory backend only, no network, no Postgres. Fake keys only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import skylize.bootstrap as bootstrap
from skylize.app.stripe.actions import StripeRefundExecutor
from skylize.bootstrap import ConfigurationError, build_container, resolve_stripe_config
from skylize.config import Settings
from skylize.dal.stripe_accounts import InMemoryStripeAccountRepository, StripeAccountRow
from skylize.tools.builtin.stripe_tools import STRIPE_CREATE_REFUND_TOOL_ID
from skylize.tools.proxy import ToolProxy

_TEST_KEY = "sk_test_dummy"
_LIVE_KEY = "sk_live_dummy_not_a_real_key_0000"
_ORG = "org_stripe_wiring"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SKYLIZE_STRIPE_ENABLED", "SKYLIZE_STRIPE_SECRET_KEY",
        "SKYLIZE_ANTHROPIC_API_KEY", "SKYLIZE_OPENAI_API_KEY", "SKYLIZE_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings(**kwargs: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, backend="memory", anthropic_api_key="", openai_api_key="",
        llm_demo_mode=True, **kwargs,
    )


def _proxy(container: bootstrap.Container) -> ToolProxy:
    proxy = container.agent_execution._tools
    assert isinstance(proxy, ToolProxy)
    return proxy


async def _tool_ids(settings: Settings) -> set[str]:
    container = await build_container(settings)
    try:
        return {t.tool_id for t in _proxy(container).registry.all()}
    finally:
        await container.aclose()


# -- resolve_stripe_config ----------------------------------------------------

def test_resolver_off_by_default() -> None:
    assert resolve_stripe_config(_settings()) is None


def test_resolver_returns_test_key_when_enabled() -> None:
    assert resolve_stripe_config(
        _settings(stripe_enabled=True, stripe_secret_key=_TEST_KEY)
    ) == _TEST_KEY


@pytest.mark.parametrize(
    ("enabled", "key"),
    [(True, ""), (True, _LIVE_KEY), (False, _LIVE_KEY), (False, _TEST_KEY)],
)
def test_resolver_refuses_settings_mutated_past_the_validator(
    enabled: bool, key: str
) -> None:
    from pydantic import SecretStr

    s = _settings()
    # No validate_assignment on Settings: these assignments bypass the
    # validator, which is exactly what the resolver must not trust.
    s.stripe_enabled = enabled
    s.stripe_secret_key = SecretStr(key)
    with pytest.raises(ConfigurationError) as ei:
        resolve_stripe_config(s)
    if key:
        assert key not in str(ei.value)


# -- build_container, flag off ------------------------------------------------

async def test_flag_off_leaves_container_as_before() -> None:
    container = await build_container(_settings())
    try:
        proxy = _proxy(container)
        assert not proxy.registry.has(STRIPE_CREATE_REFUND_TOOL_ID)
        assert proxy._stripe_repo is None
        assert proxy._refund_limits is None
        assert proxy._stripe_livemode is False
    finally:
        await container.aclose()


async def test_flag_off_tool_set_equals_settings_without_stripe_input() -> None:
    explicit_off = await _tool_ids(_settings(stripe_enabled=False, stripe_secret_key=""))
    untouched = await _tool_ids(_settings())
    assert explicit_off == untouched
    assert STRIPE_CREATE_REFUND_TOOL_ID not in untouched


# -- build_container, flag on -------------------------------------------------

async def test_flag_on_registers_only_stripe_refund_in_test_mode() -> None:
    on = _settings(stripe_enabled=True, stripe_secret_key=_TEST_KEY)
    container = await build_container(on)
    try:
        proxy = _proxy(container)
        assert proxy.registry.has(STRIPE_CREATE_REFUND_TOOL_ID)
        tool = proxy.registry.resolve(STRIPE_CREATE_REFUND_TOOL_ID)
        assert tool.stripe is not None and tool.stripe.mutates_money is True
        assert tool.spend is not None
        assert proxy._stripe_livemode is False
        assert isinstance(proxy._stripe_repo, InMemoryStripeAccountRepository)
        # Memory backend: no pool, so no refund-limits DAL and no spend ledger;
        # the tool is registered but every call fails closed.
        assert proxy._refund_limits is None
        assert proxy._spend_ledger is None
        on_ids = {t.tool_id for t in proxy.registry.all()}
    finally:
        await container.aclose()
    assert on_ids - await _tool_ids(_settings()) == {STRIPE_CREATE_REFUND_TOOL_ID}


async def test_wired_store_is_asked_for_test_mode_only() -> None:
    container = await build_container(
        _settings(stripe_enabled=True, stripe_secret_key=_TEST_KEY)
    )
    try:
        proxy = _proxy(container)
        repo = proxy._stripe_repo
        assert isinstance(repo, InMemoryStripeAccountRepository)
        await repo.insert(StripeAccountRow(
            id=uuid.uuid4(), org_id=_ORG, stripe_account_id="acct_live_only",
            livemode=True, scope="read_write",
            connected_at=datetime.now(timezone.utc), deauthorized_at=None,
        ))
        # What the proxy gate and the handler ask: livemode=False -> nothing.
        assert await repo.get_connected(_ORG, livemode=proxy._stripe_livemode) is None
    finally:
        await container.aclose()


async def test_builder_receives_livemode_false_and_a_real_executor_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real = bootstrap.default_tool_registry

    def spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(bootstrap, "default_tool_registry", spy)
    container = await build_container(
        _settings(stripe_enabled=True, stripe_secret_key=_TEST_KEY)
    )
    try:
        assert captured["stripe_livemode"] is False
        assert isinstance(captured["stripe_repo"], InMemoryStripeAccountRepository)
        factory = captured["stripe_executor_factory"]
        assert callable(factory)
        executor = factory()
        assert isinstance(executor, StripeRefundExecutor)
        assert executor._secret_key == _TEST_KEY
    finally:
        await container.aclose()


async def test_builder_gets_no_stripe_inputs_when_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real = bootstrap.default_tool_registry

    def spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(bootstrap, "default_tool_registry", spy)
    container = await build_container(_settings())
    try:
        assert captured["stripe_repo"] is None
        assert captured["stripe_executor_factory"] is None
        assert captured["stripe_livemode"] is False
    finally:
        await container.aclose()
