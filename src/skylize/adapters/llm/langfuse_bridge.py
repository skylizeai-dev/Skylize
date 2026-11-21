"""Langfuse wiring: a fail-closed factory plus a v2-shaped bridge onto the v3+ SDK.

WHY A BRIDGE. Both Langfuse call sites in the tree speak the Langfuse **v2**
client API — ``client.trace(id=..., name=..., metadata=...)`` returning a handle
with ``.generation(...)`` / ``.span(...)``:

- ``AnthropicAdapter._record_langfuse`` (adapters/llm/anthropic_adapter.py:713-738)
- ``DecisionPipeline._record_langfuse_span`` / ``_start_trace``
  (decision_engine/pipeline.py:472-491, 590-606)

Langfuse v3 rebuilt the SDK on OpenTelemetry and removed ``Langfuse.trace``.
The installed SDK at the time of writing (langfuse 4.7.1) exposes
``start_observation(trace_context=..., name=..., as_type=...)``,
``create_trace_id`` and ``flush`` — and no ``trace``. Handing a raw v3+/v4
client to either call site therefore raises ``AttributeError`` on every call,
which both sites swallow by design ("observability must never fail the call").
The result would be a deployment with Langfuse keys set that silently reports
nothing. This bridge keeps the call sites unchanged and translates.

WHY FAIL CLOSED ON MISCONFIGURATION. Observability never fails a CALL, but a
half-configured deployment is a boot-time mistake that should be loud, the same
reasoning ``bootstrap.resolve_credential_encryption_key`` applies to a malformed
key: exactly one of the two keys set, or keys set with no usable SDK installed,
raises ``LangfuseConfigurationError`` naming the variables — never their values.
Neither key set means "Langfuse off" and returns ``None``.

NOT DONE HERE (serialized, see the PR): passing the bridge into
``AnthropicAdapter(langfuse_client=...)`` at bootstrap.py, a ``langfuse_base_url``
setting, and declaring ``langfuse`` as a dependency in pyproject.toml.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping
from uuid import UUID

log = logging.getLogger("skylize.observability.langfuse")

#: v2 usage keys emitted by the call sites -> v3+ ``usage_details`` keys.
_USAGE_KEYS: Mapping[str, str] = {
    "prompt_tokens": "input",
    "completion_tokens": "output",
}


class LangfuseConfigurationError(RuntimeError):
    """Langfuse is half-configured, or configured without a usable SDK."""


def _load_sdk() -> Any:
    """Import the SDK lazily so the module loads (and CI's importability gate
    passes) on an install without langfuse."""
    from langfuse import Langfuse  # type: ignore[import-not-found,unused-ignore]

    return Langfuse


def to_trace_id(raw: str | UUID | None, client: Any) -> str:
    """Map a caller's trace id onto a v3+ W3C trace id (32 lowercase hex).

    The call sites key traces on a UUID (``governance_token_id``, ``event_id``).
    A UUID's 128 bits ARE a valid W3C trace id, so it is used verbatim
    (``uuid.hex``) and the Langfuse trace stays joinable with the audit trail
    by eye. Anything else is hashed deterministically by the SDK
    (``create_trace_id(seed=...)``) so the same input always lands on the same
    trace.
    """
    if raw is None:
        return str(client.create_trace_id())
    if isinstance(raw, UUID):
        return raw.hex
    try:
        return UUID(str(raw)).hex
    except ValueError:
        return str(client.create_trace_id(seed=str(raw)))


class _TraceHandle:
    """What ``LangfuseBridge.trace`` returns: the v2 ``.generation``/``.span`` surface."""

    __slots__ = ("_client", "_trace_id", "_trace_name", "_trace_metadata")

    def __init__(
        self, client: Any, trace_id: str, name: str | None, metadata: Mapping[str, Any] | None
    ) -> None:
        self._client = client
        self._trace_id = trace_id
        self._trace_name = name
        self._trace_metadata = dict(metadata or {})

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def _metadata(self, own: Mapping[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = dict(self._trace_metadata)
        if self._trace_name:
            merged["trace_name"] = self._trace_name
        merged.update(own or {})
        return merged

    def _emit(self, *, as_type: str, name: str, metadata: Mapping[str, Any] | None, **extra: Any) -> None:
        obs = self._client.start_observation(
            trace_context={"trace_id": self._trace_id},
            name=name,
            as_type=as_type,
            metadata=self._metadata(metadata),
            **{k: v for k, v in extra.items() if v is not None},
        )
        obs.end()

    def generation(
        self,
        *,
        name: str,
        model: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        input: Any = None,  # noqa: A002 — v2 keyword name
        output: Any = None,
        **_ignored: Any,
    ) -> None:
        usage = {
            v3: int(metadata[v2])
            for v2, v3 in _USAGE_KEYS.items()
            if metadata is not None and isinstance(metadata.get(v2), int)
        }
        # The adapter prices every call from the ai_cost_ledger price table
        # (ADR-0006, micro units). Sending that figure as `cost_details` makes
        # Langfuse display OUR cost instead of inferring its own from a model
        # price list that can drift from the ledger. Langfuse costs are USD floats.
        micros = metadata.get("cost_usd_micros") if metadata is not None else None
        cost = (
            {"total": micros / 1_000_000}
            if isinstance(micros, int) and not isinstance(micros, bool)
            else None
        )
        self._emit(
            as_type="generation",
            name=name,
            metadata=metadata,
            model=model,
            input=input,
            output=output,
            usage_details=usage or None,
            cost_details=cost,
        )

    def span(
        self,
        *,
        name: str,
        metadata: Mapping[str, Any] | None = None,
        input: Any = None,  # noqa: A002 — v2 keyword name
        output: Any = None,
        **_ignored: Any,
    ) -> None:
        self._emit(as_type="span", name=name, metadata=metadata, input=input, output=output)


class LangfuseBridge:
    """A v2-shaped client (``trace``/``flush``) backed by a v3+ ``Langfuse`` client."""

    def __init__(self, client: Any) -> None:
        if not callable(getattr(client, "start_observation", None)):
            raise LangfuseConfigurationError(
                "the installed langfuse SDK has no start_observation(); "
                "this bridge requires langfuse>=3 (the OpenTelemetry-based SDK)"
            )
        self._client = client

    @property
    def client(self) -> Any:
        return self._client

    def trace(
        self,
        *,
        id: str | UUID | None = None,  # noqa: A002 — v2 keyword name
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        **_ignored: Any,
    ) -> _TraceHandle:
        return _TraceHandle(self._client, to_trace_id(id, self._client), name, metadata)

    def flush(self) -> None:
        self._client.flush()

    def shutdown(self) -> None:
        shutdown = getattr(self._client, "shutdown", None)
        if callable(shutdown):
            shutdown()


def build_langfuse_bridge(
    settings: Any, *, sdk_loader: Callable[[], Any] = _load_sdk
) -> LangfuseBridge | None:
    """Return a bridge when Langfuse is configured, ``None`` when it is off.

    Reads ``langfuse_public_key`` / ``langfuse_secret_key`` (config.py, env
    ``SKYLIZE_LANGFUSE_PUBLIC_KEY`` / ``SKYLIZE_LANGFUSE_SECRET_KEY``) and an
    optional ``langfuse_base_url`` (no such Settings field exists yet; the SDK
    default applies when absent).
    """
    public_key = str(getattr(settings, "langfuse_public_key", "") or "").strip()
    secret_key = str(getattr(settings, "langfuse_secret_key", "") or "").strip()
    base_url = str(getattr(settings, "langfuse_base_url", "") or "").strip()

    if not public_key and not secret_key:
        return None
    if not public_key or not secret_key:
        missing = "SKYLIZE_LANGFUSE_PUBLIC_KEY" if not public_key else "SKYLIZE_LANGFUSE_SECRET_KEY"
        raise LangfuseConfigurationError(
            f"Langfuse is half-configured: {missing} is not set while its pair is. "
            "Set both to enable Langfuse, or neither to turn it off."
        )
    try:
        sdk = sdk_loader()
    except ImportError as exc:
        raise LangfuseConfigurationError(
            "Langfuse keys are set but the langfuse SDK is not installed. Install "
            "langfuse>=3 or unset SKYLIZE_LANGFUSE_PUBLIC_KEY/SKYLIZE_LANGFUSE_SECRET_KEY."
        ) from exc

    kwargs: dict[str, Any] = {"public_key": public_key, "secret_key": secret_key}
    if base_url:
        # `base_url`, not the deprecated `host`: in langfuse 4.x a LANGFUSE_BASE_URL
        # environment variable silently wins over `host=`, so an operator's
        # explicit setting could be ignored.
        kwargs["base_url"] = base_url
    bridge = LangfuseBridge(sdk(**kwargs))
    log.info("langfuse_enabled base_url=%s", base_url or "<sdk default>")
    return bridge
