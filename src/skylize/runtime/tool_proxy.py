"""
The Tool Proxy — IF-TOOL, the single chokepoint between agents and adapters.

Two classes live here:

``ToolProxy``
    New Redis-backed gate per the spec. Accepts a raw public-key bytes, an
    asyncio Redis client, and a generic audit_publisher callable. Exposes
    ``validate_token`` (the ordered six-step pipeline) and ``dispatch_llm``
    (validate → generate → audit). Raises a typed ``ToolProxyError`` subclass
    at the first failing stage so callers get a machine-readable stage name.

``RegistryToolProxy`` (formerly ``ToolProxy``)
    The registry/ledger/bus-wired production proxy used by bootstrap and the
    runner. All original behaviour preserved; renamed to avoid collision with
    the new class.

``ToolDispatchDenied``, ``LLMGenerateHandler``, ``MemorySearchHandler``
    Legacy symbols kept for backward compatibility.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from pydantic import BaseModel, ConfigDict
import structlog

from ..adapters.llm.gateway import (
    LLMGateway,
    LLMGenerateRequest,
    LLMGenerateResponse,
    TokenBudgetExceeded,
)
from ..contracts.base import GovernanceToken
from ..contracts.registry import AgentNotRegistered, AgentRegistry
from ..contracts.token import (
    LiveStateChecker,
    ValidationStage,
    validate_tool_call,
    verify_token_signature,
)
from ..events.bus import EventBus
from ..schemas.events.audit import AuditActionRecorded
from ..security.ecc_service import Curve, ECCService
from .run_ledger import RunExpired, RunLedger

__all__ = [
    # New spec
    "ToolProxy",
    "ToolCallRequest",
    "ToolProxyError",
    "SignatureInvalid",
    "TokenExpired",
    "TokenRevoked",
    "ScopeViolation",
    "BudgetExceeded",
    "DelegationInvalid",
    # Legacy / registry-backed
    "RegistryToolProxy",
    "ToolHandler",
    "ToolDispatchDenied",
    "TokenBudgetExceeded",
    "LLMGenerateHandler",
    "MemorySearchHandler",
]

log = structlog.get_logger(__name__)

_GOVERNANCE_CURVE = Curve.P384


# ---------------------------------------------------------------------------
# Typed exception hierarchy (new spec)
# ---------------------------------------------------------------------------

class ToolProxyError(Exception):
    """Base for all validation failures raised by ``ToolProxy``.

    Carries the failed stage name and a human-readable reason so callers can
    branch on ``stage`` without string-parsing the message.
    """

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason


class SignatureInvalid(ToolProxyError):
    def __init__(self, reason: str = "token signature did not verify") -> None:
        super().__init__(ValidationStage.SIGNATURE.value, reason)


class TokenExpired(ToolProxyError):
    def __init__(self, reason: str = "token has expired") -> None:
        super().__init__(ValidationStage.EXPIRY.value, reason)


class TokenRevoked(ToolProxyError):
    def __init__(self, reason: str = "token has been revoked") -> None:
        super().__init__(ValidationStage.REVOCATION.value, reason)


class ScopeViolation(ToolProxyError):
    def __init__(self, reason: str = "tool not in scope") -> None:
        super().__init__(ValidationStage.SCOPE.value, reason)


class BudgetExceeded(ToolProxyError):
    def __init__(self, reason: str = "token budget exceeded") -> None:
        super().__init__(ValidationStage.BUDGET.value, reason)


class DelegationInvalid(ToolProxyError):
    def __init__(self, reason: str = "delegation chain invalid") -> None:
        super().__init__(ValidationStage.DELEGATION.value, reason)


# ---------------------------------------------------------------------------
# Request model (new spec)
# ---------------------------------------------------------------------------

class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str
    governance_token_id: UUID
    org_id: str
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# New ToolProxy (Redis-backed, callable audit publisher)
# ---------------------------------------------------------------------------

class ToolProxy:
    """Redis-backed governance gate for tool calls.

    Validates a ``GovernanceToken`` through the six mandatory stages before
    any side effect occurs; dispatches to the LLM gateway on success.

    Args:
        redis:                     ``redis.asyncio.Redis`` client (injected).
        governance_authority_pubkey: DER-encoded ECDSA P-384 public key bytes.
        audit_publisher:           Async callable ``(event: dict) -> None``.
                                   Failures are swallowed — audit must never
                                   propagate to callers.
    """

    def __init__(
        self,
        redis: Any,
        governance_authority_pubkey: bytes,
        audit_publisher: Callable[..., Any],
    ) -> None:
        self._redis = redis
        self._pubkey: EllipticCurvePublicKey = ECCService.load_public_key_der(
            governance_authority_pubkey
        )
        self._audit_publisher = audit_publisher

    # ------------------------------------------------------------------
    # Validation pipeline
    # ------------------------------------------------------------------

    async def validate_token(
        self,
        token: GovernanceToken,
        tool_call: ToolCallRequest,
        contract_allowed_tools: list[str],
    ) -> None:
        """Run the six-stage ordered validation for one tool call.

        Raises the appropriate ``ToolProxyError`` subclass at the first
        failing stage. On failure, emits an audit-deny event (swallowing any
        publisher error). On full success, returns ``None``.

        Validation order (canonical, must not change):
          1 signature → 2 expiry → 3 revocation → 4 scope → 5 budget → 6 delegation
        """
        # 1. SIGNATURE
        if not verify_token_signature(token, self._pubkey):
            await self._emit_audit_deny(token, tool_call, "signature", "token signature did not verify")
            raise SignatureInvalid()

        # 2. EXPIRY
        now = datetime.now(timezone.utc)
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now >= expires_at:
            await self._emit_audit_deny(token, tool_call, "expiry", f"token expired at {expires_at.isoformat()}")
            raise TokenExpired(f"token expired at {expires_at.isoformat()}")

        # 3. REVOCATION — Redis GET skylize:revoked:{token_id}
        revoked = await self._redis.get(f"skylize:revoked:{token.token_id}")
        if revoked is not None:
            reason = revoked.decode() if isinstance(revoked, bytes) else str(revoked)
            await self._emit_audit_deny(token, tool_call, "revocation", reason)
            raise TokenRevoked(reason)

        # 4. SCOPE — tool must be in BOTH token.scope AND contract_allowed_tools
        if tool_call.tool_id not in token.scope:
            reason = f"tool {tool_call.tool_id!r} not in token scope"
            await self._emit_audit_deny(token, tool_call, "scope", reason)
            raise ScopeViolation(reason)
        if tool_call.tool_id not in contract_allowed_tools:
            reason = f"tool {tool_call.tool_id!r} not in contract allowed_tools"
            await self._emit_audit_deny(token, tool_call, "scope", reason)
            raise ScopeViolation(reason)

        # 5. BUDGET — requested_max_tokens <= max_token_budget - tokens_used_so_far
        requested = int(tool_call.params.get("requested_max_tokens", 0) or 0)
        tokens_used = int(tool_call.params.get("tokens_used_so_far", 0) or 0)
        remaining = token.max_token_budget - tokens_used
        if requested > remaining:
            reason = (
                f"requested {requested} tokens exceeds remaining budget {remaining} "
                f"(max={token.max_token_budget}, used={tokens_used})"
            )
            await self._emit_audit_deny(token, tool_call, "budget", reason)
            raise BudgetExceeded(reason)

        # 6. DELEGATION — chain must be rank-monotonic (non-empty, ends at agent)
        chain = token.delegation_chain
        if not chain:
            reason = "empty delegation_chain"
            await self._emit_audit_deny(token, tool_call, "delegation", reason)
            raise DelegationInvalid(reason)
        if chain[-1] != token.agent_id:
            reason = "delegation_chain does not terminate at the token's agent_id"
            await self._emit_audit_deny(token, tool_call, "delegation", reason)
            raise DelegationInvalid(reason)

    # ------------------------------------------------------------------
    # LLM dispatch
    # ------------------------------------------------------------------

    async def dispatch_llm(
        self,
        token: GovernanceToken,
        tool_call: ToolCallRequest,
        contract_allowed_tools: list[str],
        gateway: LLMGateway,
    ) -> LLMGenerateResponse:
        """Validate the token then dispatch to the LLM gateway.

        Raises a ``ToolProxyError`` subclass on any validation failure
        (``gateway.generate`` is never called).  On success, emits an audit
        event and returns the gateway response.
        """
        await self.validate_token(token, tool_call, contract_allowed_tools)

        _skip = {"governance_token_id", "org_id"}
        extra = {
            k: v
            for k, v in tool_call.params.items()
            if k in LLMGenerateRequest.model_fields and k not in _skip
        }
        request = LLMGenerateRequest(
            governance_token_id=tool_call.governance_token_id,
            org_id=tool_call.org_id,
            **extra,
        )
        response = await gateway.generate(request)
        await self._emit_audit_success(token, tool_call, response)
        return response

    # ------------------------------------------------------------------
    # Internal audit helpers — failures are swallowed, never propagated
    # ------------------------------------------------------------------

    async def _emit_audit_deny(
        self,
        token: GovernanceToken,
        tool_call: ToolCallRequest,
        stage: str,
        reason: str,
    ) -> None:
        try:
            event = {
                "type": "audit.action_recorded",
                "token_id": str(token.token_id),
                "agent_id": token.agent_id,
                "org_id": tool_call.org_id,
                "tool_id": tool_call.tool_id,
                "result": "denied",
                "failed_stage": stage,
                "reason": reason,
            }
            await self._audit_publisher(event)
        except Exception:
            log.warning("tool_proxy.audit_publish_failed", stage=stage)

    async def _emit_audit_success(
        self,
        token: GovernanceToken,
        tool_call: ToolCallRequest,
        response: LLMGenerateResponse,
    ) -> None:
        try:
            event = {
                "type": "audit.action_recorded",
                "token_id": str(token.token_id),
                "agent_id": token.agent_id,
                "org_id": tool_call.org_id,
                "tool_id": tool_call.tool_id,
                "result": "success",
                "provider": response.provider,
                "total_tokens": response.usage.total_tokens,
            }
            await self._audit_publisher(event)
        except Exception:
            log.warning("tool_proxy.audit_publish_failed", stage="success")


# ---------------------------------------------------------------------------
# Legacy: ToolDispatchDenied
# ---------------------------------------------------------------------------

class ToolDispatchDenied(Exception):
    """Raised when a tool call is refused for a non-budget reason (bad signature,
    expiry, revocation, scope, delegation, or no registered handler)."""


# ---------------------------------------------------------------------------
# Legacy handlers
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolHandler(Protocol):
    """A concrete tool implementation behind the proxy. Receives only the
    validated payload and the caller's tenant — never the governance token."""

    async def handle(self, payload: dict[str, Any], org_id: str) -> dict[str, Any]: ...


class LLMGenerateHandler:
    """``llm.generate`` → the provider-abstracted LLM gateway."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def handle(self, payload: dict[str, Any], org_id: str) -> dict[str, Any]:
        request = LLMGenerateRequest.model_validate(payload)
        response = await self._gateway.generate(request)
        return response.model_dump(mode="json")


class MemorySearchHandler:
    """``memory.search`` — stub until the Memory port is wired through the proxy."""

    async def handle(self, payload: dict[str, Any], org_id: str) -> dict[str, Any]:
        log.info("tool_proxy.memory_search_stub", org_id=org_id, query=payload.get("query"))
        return {"results": []}


# ---------------------------------------------------------------------------
# RegistryToolProxy — the registry/ledger/bus-wired production proxy
# ---------------------------------------------------------------------------

class RegistryToolProxy:
    """Registry, ledger, and event-bus wired tool proxy (production default).

    This is the class formerly exported as ``ToolProxy``; it is renamed so
    that ``ToolProxy`` can refer to the new Redis-backed spec class without
    breaking bootstrap or the agent runner.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        public_key: EllipticCurvePublicKey,
        live_state_for: Callable[[str], LiveStateChecker],
        bus: EventBus,
        run_ledger: RunLedger,
        handlers: Mapping[str, ToolHandler],
    ) -> None:
        self._registry = registry
        self._public_key = public_key
        self._live_state_for = live_state_for
        self._bus = bus
        self._ledger = run_ledger
        self._handlers = dict(handlers)

    async def dispatch(
        self,
        *,
        token: GovernanceToken,
        tool_id: str,
        payload: BaseModel | Mapping[str, Any],
        org_id: str,
    ) -> dict[str, Any]:
        """Validate, dispatch, account, and audit one governed tool call."""
        payload_dict: dict[str, Any] = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else dict(payload)
        )
        tokens_requested = int(payload_dict.get("requested_max_tokens", 0) or 0)
        log.info(
            "tool_proxy.dispatch",
            agent_id=token.agent_id,
            tool_id=tool_id,
            org_id=org_id,
            tokens_requested=tokens_requested,
        )

        # 1. Resolve the contract — fail closed on an unknown agent.
        try:
            contract = self._registry.resolve(token.agent_id)
        except AgentNotRegistered as exc:
            await self._audit(token, tool_id, org_id, result="denied", reason=str(exc))
            raise ToolDispatchDenied(str(exc)) from exc
        allowed = {grant.tool_id for grant in contract.allowed_tools}

        # 2. Open the run ledger (fail closed if the run's time ceiling passed).
        try:
            await self._ledger.open_run(
                token.token_id,
                token.agent_id,
                budget=token.max_token_budget,
                ttl_seconds=token.max_execution_time_seconds,
            )
        except RunExpired as exc:
            await self._audit(token, tool_id, org_id, result="denied", reason=str(exc))
            raise
        tokens_used_so_far = await self._ledger.used(token.token_id, token.agent_id)

        # 3. The canonical ordered validation for THIS tool call.
        result = validate_tool_call(
            token=token,
            public_key=self._public_key,
            requested_tool_id=tool_id,
            contract_allowed_tool_ids=allowed,
            requested_token_cost=tokens_requested,
            tokens_used_so_far=tokens_used_so_far,
            live_state=self._live_state_for(org_id),
        )
        if not result.is_valid:
            reason = result.reason or "token validation failed"
            await self._audit(
                token, tool_id, org_id, result="denied", reason=reason, inputs=payload_dict
            )
            if result.failed_stage is ValidationStage.BUDGET:
                raise TokenBudgetExceeded(reason)
            stage = result.failed_stage.value if result.failed_stage else "unknown"
            raise ToolDispatchDenied(f"[{stage}] {reason}")

        # 4. Dispatch to the registered handler — fail closed if there is none.
        handler = self._handlers.get(tool_id)
        if handler is None:
            reason = f"no handler registered for tool {tool_id!r}"
            await self._audit(token, tool_id, org_id, result="denied", reason=reason)
            raise ToolDispatchDenied(reason)

        try:
            handler_result = await handler.handle(payload_dict, org_id)
        except Exception as exc:
            log.error(
                "tool_proxy.handler_error",
                agent_id=token.agent_id,
                tool_id=tool_id,
                org_id=org_id,
                error=str(exc),
            )
            await self._audit(
                token, tool_id, org_id, result="failed", reason=str(exc), inputs=payload_dict
            )
            raise

        # 5. Debit the run ledger by the actual tokens consumed.
        spent = _actual_tokens(handler_result)
        try:
            remaining = await self._ledger.debit(token.token_id, token.agent_id, spent)
        except TokenBudgetExceeded as exc:
            await self._audit(
                token, tool_id, org_id, result="denied", reason=str(exc),
                inputs=payload_dict, outputs=handler_result,
            )
            raise

        # 6. Audit the success and return.
        log.info(
            "tool_proxy.dispatched",
            agent_id=token.agent_id,
            tool_id=tool_id,
            org_id=org_id,
            tokens_spent=spent,
            tokens_remaining=remaining,
        )
        await self._audit(
            token, tool_id, org_id, result="success",
            inputs=payload_dict, outputs=handler_result,
        )
        return handler_result

    async def _audit(
        self,
        token: GovernanceToken,
        tool_id: str,
        org_id: str,
        *,
        result: str,
        reason: str | None = None,
        inputs: Any = None,
        outputs: Any = None,
    ) -> None:
        event = AuditActionRecorded(
            tenant_id=org_id,
            partition_key=str(token.token_id),
            department="audit",
            source_agent_id=token.agent_id,
            authority_level=token.authority_level,
            governance_token_id=token.token_id,
            correlation_id=token.token_id,
            payload=AuditActionRecorded.Payload(
                action_type=f"tool.{tool_id}",
                inputs_hash=_hash(inputs),
                outputs_hash=_hash(outputs),
                result=result,
                result_reason=reason,
            ),
        )
        await self._bus.publish(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _actual_tokens(result: dict[str, Any]) -> int:
    usage = result.get("usage")
    if isinstance(usage, Mapping):
        return int(usage.get("total_tokens", 0) or 0)
    return 0


def _hash(value: Any) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
