"""Unit tests for the Langfuse factory + v2->v3 bridge (adapters/llm/langfuse_bridge.py).

No network. The fake SDK below mirrors the v3+/v4 surface the bridge relies on
(`start_observation`, `create_trace_id`, `flush`). One test additionally drives
the REAL installed SDK into an in-memory OpenTelemetry exporter; it is skipped
when `langfuse` is not installed (it is not a declared dependency yet).
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from skylize.adapters.llm.langfuse_bridge import (
    LangfuseBridge,
    LangfuseConfigurationError,
    build_langfuse_bridge,
    to_trace_id,
)


class _FakeObservation:
    def __init__(self) -> None:
        self.ended = False

    def end(self) -> None:
        self.ended = True


class _FakeV3Client:
    """The v3+/v4 surface: no `trace` attribute at all."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.observations: list[tuple[dict[str, Any], _FakeObservation]] = []
        self.flushed = 0

    def start_observation(self, **kwargs: Any) -> _FakeObservation:
        obs = _FakeObservation()
        self.observations.append((kwargs, obs))
        return obs

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return ("f" * 32) if seed is None else (str(abs(hash(seed)))[:8].rjust(32, "0"))

    def flush(self) -> None:
        self.flushed += 1


def _settings(**kw: Any) -> SimpleNamespace:
    base = {"langfuse_public_key": "", "langfuse_secret_key": ""}
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_no_keys_means_off() -> None:
    def loader() -> Any:
        raise AssertionError("SDK must not be imported when Langfuse is off")

    assert build_langfuse_bridge(_settings(), sdk_loader=loader) is None
    assert build_langfuse_bridge(_settings(langfuse_public_key="  "), sdk_loader=loader) is None


@pytest.mark.parametrize(
    "kw,missing",
    [
        ({"langfuse_public_key": "pk-lf-1"}, "SKYLIZE_LANGFUSE_SECRET_KEY"),
        ({"langfuse_secret_key": "sk-lf-SECRETVALUE"}, "SKYLIZE_LANGFUSE_PUBLIC_KEY"),
    ],
)
def test_half_configured_fails_closed_naming_variable_not_value(kw: dict, missing: str) -> None:
    with pytest.raises(LangfuseConfigurationError) as exc_info:
        build_langfuse_bridge(_settings(**kw), sdk_loader=_FakeV3Client)
    assert missing in str(exc_info.value)
    assert "SECRETVALUE" not in str(exc_info.value) and "pk-lf-1" not in str(exc_info.value)


def test_keys_without_sdk_fail_closed() -> None:
    def loader() -> Any:
        raise ImportError("No module named 'langfuse'")

    with pytest.raises(LangfuseConfigurationError, match="not installed"):
        build_langfuse_bridge(
            _settings(langfuse_public_key="pk", langfuse_secret_key="sk"), sdk_loader=loader
        )


def test_v2_sdk_without_start_observation_is_refused() -> None:
    class _V2Client:
        def __init__(self, **_: Any) -> None: ...

        def trace(self, **_: Any) -> None: ...

    with pytest.raises(LangfuseConfigurationError, match="langfuse>=3"):
        build_langfuse_bridge(
            _settings(langfuse_public_key="pk", langfuse_secret_key="sk"), sdk_loader=lambda: _V2Client
        )


def test_configured_builds_client_with_keys_and_optional_host() -> None:
    bridge = build_langfuse_bridge(
        _settings(langfuse_public_key=" pk ", langfuse_secret_key="sk"), sdk_loader=lambda: _FakeV3Client
    )
    assert isinstance(bridge, LangfuseBridge)
    assert bridge.client.init_kwargs == {"public_key": "pk", "secret_key": "sk"}

    bridge = build_langfuse_bridge(
        _settings(langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host="https://lf.example"),
        sdk_loader=lambda: _FakeV3Client,
    )
    assert bridge is not None and bridge.client.init_kwargs["host"] == "https://lf.example"


def test_secret_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    build_langfuse_bridge(
        _settings(langfuse_public_key="pk-lf-PUB", langfuse_secret_key="sk-lf-SECRET"),
        sdk_loader=lambda: _FakeV3Client,
    )
    assert "sk-lf-SECRET" not in caplog.text


# ---------------------------------------------------------------------------
# Bridge translation
# ---------------------------------------------------------------------------

def test_uuid_trace_id_is_used_verbatim_as_w3c_hex() -> None:
    client = _FakeV3Client()
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert to_trace_id(u, client) == "12345678123456781234567812345678"
    assert to_trace_id(str(u), client) == u.hex
    assert len(to_trace_id("not-a-uuid", client)) == 32
    assert to_trace_id("not-a-uuid", client) == to_trace_id("not-a-uuid", client)
    assert to_trace_id(None, client) == "f" * 32


def test_generation_maps_to_start_observation_and_ends_it() -> None:
    client = _FakeV3Client()
    bridge = LangfuseBridge(client)
    tid = uuid.uuid4()
    bridge.trace(id=str(tid), name="llm.generate", metadata={"org_id": "o1"}).generation(
        name="anthropic.messages.create",
        model="claude-x",
        metadata={"prompt_tokens": 11, "completion_tokens": 7, "cost_usd_micros": 5},
    )
    (kwargs, obs), = client.observations
    assert obs.ended
    assert kwargs["trace_context"] == {"trace_id": tid.hex}
    assert kwargs["as_type"] == "generation"
    assert kwargs["name"] == "anthropic.messages.create"
    assert kwargs["model"] == "claude-x"
    assert kwargs["usage_details"] == {"input": 11, "output": 7}
    assert kwargs["metadata"]["org_id"] == "o1"
    assert kwargs["metadata"]["trace_name"] == "llm.generate"
    assert kwargs["metadata"]["cost_usd_micros"] == 5


def test_span_maps_to_span_observation() -> None:
    client = _FakeV3Client()
    LangfuseBridge(client).trace(id="evt-1", name="decision_engine.evaluate").span(
        name="stage.policy", metadata={"passed": True}
    )
    (kwargs, obs), = client.observations
    assert obs.ended and kwargs["as_type"] == "span" and kwargs["name"] == "stage.policy"
    assert "usage_details" not in kwargs and "model" not in kwargs


def test_flush_and_shutdown_delegate() -> None:
    client = _FakeV3Client()
    bridge = LangfuseBridge(client)
    bridge.flush()
    bridge.shutdown()  # fake has no shutdown: must not raise
    assert client.flushed == 1


# ---------------------------------------------------------------------------
# The real call site, unchanged
# ---------------------------------------------------------------------------

def _adapter_call(langfuse_client: Any) -> None:
    from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter

    settings = SimpleNamespace(
        anthropic_api_key="test-key",
        llm_model_default="m-default",
        llm_model_fast="m-fast",
        llm_model_reasoning="m-reasoning",
    )
    adapter = AnthropicAdapter(settings=settings, langfuse_client=langfuse_client)
    request = SimpleNamespace(governance_token_id=uuid.uuid4(), org_id="org-1")
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4), cost_usd_micros=9
    )
    adapter._record_langfuse(request, "claude-test", response)  # type: ignore[arg-type]


def test_raw_v3_client_at_adapter_call_site_records_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The defect this module fixes: a raw v3+/v4 client has no `.trace`, the
    adapter swallows the AttributeError, and nothing is reported."""
    client = _FakeV3Client()
    caplog.set_level(logging.WARNING)
    _adapter_call(client)
    assert client.observations == []
    assert "langfuse_record_failed" in caplog.text


def test_bridge_at_adapter_call_site_records_the_generation() -> None:
    client = _FakeV3Client()
    _adapter_call(LangfuseBridge(client))
    (kwargs, obs), = client.observations
    assert obs.ended
    assert kwargs["as_type"] == "generation" and kwargs["model"] == "claude-test"
    assert kwargs["usage_details"] == {"input": 3, "output": 4}
    assert kwargs["metadata"]["org_id"] == "org-1"


def test_real_installed_sdk_accepts_the_bridge_calls() -> None:
    """Drive the REAL SDK (if installed) into an in-memory exporter: proves the
    keyword arguments match the installed signature and the trace id lands."""
    langfuse = pytest.importorskip("langfuse")
    export = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = export.InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client = langfuse.Langfuse(
        public_key="pk-lf-test", secret_key="sk-lf-test", host="http://127.0.0.1:9",
        tracer_provider=provider,
    )
    try:
        tid = uuid.uuid4()
        LangfuseBridge(client).trace(id=tid, name="llm.generate").generation(
            name="anthropic.messages.create", model="claude-test",
            metadata={"prompt_tokens": 1, "completion_tokens": 2},
        )
        spans = exporter.get_finished_spans()
        assert any(
            s.name == "anthropic.messages.create" and format(s.context.trace_id, "032x") == tid.hex
            for s in spans
        )
    finally:
        provider.shutdown()
