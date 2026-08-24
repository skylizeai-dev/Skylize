"""ToolProxy — the IF-TOOL enforcement gate (system_boundaries.md §4.6).

An agent never touches an adapter directly; it calls `ToolProxy.invoke(...)`,
which:
  1. resolves the tool from the `ToolRegistry` (fails closed on unknown id);
  2. validates the caller's `GovernanceToken` through the existing, ordered
     `contracts.token.validate_tool_call` pipeline — signature, expiry,
     revocation, scope, budget, delegation — the same gate the LangGraph
     `governance_checkpoint` node uses;
  3. validates `input_data` against the tool's `input_schema`;
  4. dispatches to the tool's handler;
  5. emits one `audit.action_recorded` (`action_type="tool.invoked"`) per call —
     the closed event taxonomy has no dedicated `tool` category
     (tests/contract/test_tool_dedup_events.py), so tool activity is audited,
     not published as its own event type.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from pydantic import ValidationError

from ..app.audit.service import AuditService
from ..app.principal.errors import CeilingExceeded, EnvelopeNotFound
from ..app.principal.models import Reservation
from ..app.principal.spend import SpendLedger
from ..contracts.base import AgentContract, GovernanceToken
from ..contracts.token import LiveStateChecker, validate_tool_call
from .base import (
    ToolCallLimitExceeded,
    ToolContext,
    ToolConvergenceDenied,
    ToolDefinition,
    ToolExecutionError,
    ToolInputError,
    ToolNotRegistered,
    ToolPermissionDenied,
    ToolResult,
    ToolSpendDeferredToHuman,
    ToolSpendHardDenied,
    ToolSpendUnavailable,
)
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

LiveStateFor = Callable[[str], LiveStateChecker]

# The Governance Authority's convergence recorder. Returns True iff *this* call
# tripped the runaway-loop breaker (same tool + input twice consecutively in the
# workflow). Injected as a callback so the proxy stays decoupled from the full
# Authority — it only needs this one hot-path hook.
RecordAction = Callable[..., Awaitable[bool]]


class ToolCallCounter:
    """Per-(workflow, agent, tool) call counter enforcing `ToolGrant.max_calls_per_run`.

    Keyed by ``(correlation_id, agent_id, tool_id)`` — mirrors
    `governance.authority.ConvergenceTracker`'s workflow-scoped keying, so two
    agents in the same workflow (or the same agent across workflows) never
    share a count, even though each carries its own `max_calls_per_run` for
    the same `tool_id`. In-process only; the proxy is long-lived, so this
    counter accumulates for the life of the process rather than per-request.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[UUID, str, str], int] = {}

    def increment(self, correlation_id: UUID, agent_id: str, tool_id: str) -> int:
        key = (correlation_id, agent_id, tool_id)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        return count


class ToolProxy:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit: AuditService,
        public_key: EllipticCurvePublicKey,
        live_state_for: LiveStateFor,
        record_action: RecordAction | None = None,
        spend_ledger: SpendLedger | None = None,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._public_key = public_key
        self._live_state_for = live_state_for
        self._record_action = record_action
        self._call_counts = ToolCallCounter()
        # None on the memory backend (no durable ledger). A spend-capable tool
        # invoked without a ledger FAILS CLOSED in `_reserve_spend` rather than
        # running ungoverned — an unenforced ceiling is worse than no ceiling,
        # because it reads as enforced.
        self._spend_ledger = spend_ledger

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    async def invoke(
        self,
        *,
        tool_id: str,
        input_data: dict[str, Any],
        governance_token: GovernanceToken,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
    ) -> ToolResult:
        try:
            tool = self._registry.resolve(tool_id)
        except ToolNotRegistered:
            await self._audit_call(
                tool_id=tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="failed", reason="unknown tool_id",
            )
            raise

        allowed_tool_ids = {grant.tool_id for grant in contract.allowed_tools}
        validation = validate_tool_call(
            token=governance_token,
            public_key=self._public_key,
            requested_tool_id=tool_id,
            contract_allowed_tool_ids=allowed_tool_ids,
            requested_token_cost=0,  # tool calls don't debit the LLM token budget
            tokens_used_so_far=0,
            live_state=self._live_state_for(org_id),
        )
        if not validation.is_valid:
            stage = validation.failed_stage.value if validation.failed_stage else None
            await self._audit_call(
                tool_id=tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=f"{stage}: {validation.reason}",
            )
            raise ToolPermissionDenied(validation.reason or "denied", failed_stage=stage)

        # Call-count ceiling (agent_governance.md §6): checked BEFORE the
        # convergence breaker below. Exceeding a declared max_calls_per_run is a
        # hard contract violation — a harder stop than the runaway-loop heuristic
        # convergence detects — so it must not be masked by a convergence trip.
        grant = next((g for g in contract.allowed_tools if g.tool_id == tool_id), None)
        if grant is not None and grant.max_calls_per_run is not None:
            call_count = self._call_counts.increment(correlation_id, contract.agent_id, tool_id)
            if call_count > grant.max_calls_per_run:
                reason = (
                    f"call limit exceeded: {tool_id!r} called {call_count} times, "
                    f"max_calls_per_run={grant.max_calls_per_run}"
                )
                await self._audit_call(
                    tool_id=tool_id, contract=contract, org_id=org_id,
                    correlation_id=correlation_id, governance_token=governance_token,
                    result="denied", reason=reason,
                )
                raise ToolCallLimitExceeded(reason)

        # Convergence breaker (agent_governance.md §7): once the token has passed
        # the ordered validation, record this action with the Authority BEFORE
        # dispatching. If the agent just made the identical tool call (same input)
        # back-to-back in this workflow, record_action trips the breaker — the
        # Authority suspends the agent and emits the convergence_failure + breaker
        # events — and we deny this call so no side effect runs on the loop.
        if self._record_action is not None:
            tripped = await self._record_action(
                agent_id=contract.agent_id,
                org_id=org_id,
                correlation_id=correlation_id,
                action_type=tool_id,
                action_args=input_data,
            )
            if tripped:
                reason = (
                    f"convergence breaker tripped: repeated {tool_id!r} call "
                    "suspended by the Governance Authority"
                )
                await self._audit_call(
                    tool_id=tool_id, contract=contract, org_id=org_id,
                    correlation_id=correlation_id, governance_token=governance_token,
                    result="denied", reason=reason,
                )
                raise ToolConvergenceDenied(reason)

        try:
            validated_input = tool.input_schema.model_validate(input_data)
        except ValidationError as exc:
            await self._audit_call(
                tool_id=tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="failed", reason=f"input validation: {exc}",
            )
            raise ToolInputError(str(exc)) from exc

        # Spend ceiling (spend.SpendLedger). LAST gate before dispatch, and only
        # for spend-capable tools: the ceiling is a shared mutable resource, so a
        # hold must be placed as late as possible — after every cheaper denial has
        # had its chance — to minimise the window in which budget is held for a
        # call that was never going to run.
        reservation: Reservation | None = None
        if tool.spend is not None:
            reservation = await self._reserve_spend(
                tool=tool, validated_input=validated_input, contract=contract,
                org_id=org_id, correlation_id=correlation_id,
                governance_token=governance_token,
            )

        context = ToolContext(org_id=org_id, agent_id=contract.agent_id, correlation_id=correlation_id)
        try:
            output = await tool.handler(validated_input, context)
        except Exception as exc:  # noqa: BLE001 — normalized into one tool error type
            # The hold MUST NOT outlive the call it was placed for. Without this
            # a failing tool leaks budget until `sweep_expired` reclaims it at
            # expires_at, and the customer sees spend capacity vanish for 15
            # minutes with nothing to show for it.
            await self._release_spend(reservation, org_id=org_id)
            await self._audit_call(
                tool_id=tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="failed", reason=f"handler error: {exc}",
            )
            raise ToolExecutionError(str(exc)) from exc

        # The side effect has happened. Audit it BEFORE settling the ledger: the
        # audit record is the evidence that the action ran, and a ledger failure
        # must not be able to erase it. Ordered the other way, a raising commit
        # would leave an executed, real-world action with no audit row at all.
        await self._audit_call(
            tool_id=tool_id, contract=contract, org_id=org_id,
            correlation_id=correlation_id, governance_token=governance_token,
            result="success", reason=None, outputs=output.model_dump(mode="json"),
        )

        # Settle the hold with the reserved amount. Deliberately NOT released on
        # failure here — the action ran, so the budget it consumed is real; a
        # release would under-count spend for an action that actually happened.
        # A commit failure propagates: the caller has to know the ledger and the
        # world disagree, and the audit row above lets a human reconcile. The
        # hold meanwhile stays 'held', so the ceiling keeps binding until
        # `sweep_expired` reclaims it rather than freeing budget prematurely.
        if reservation is not None:
            await self._spend_ledger.commit(  # type: ignore[union-attr]
                org_id=org_id,
                reservation_id=reservation.reservation_id,
                actual_minor=reservation.amount_minor,
            )

        return ToolResult(tool_id=tool_id, output=output)

    async def _reserve_spend(
        self,
        *,
        tool: ToolDefinition,
        validated_input: Any,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        governance_token: GovernanceToken,
    ) -> Reservation:
        """Place the hold, or deny. Every exit that is not a `Reservation` denies.

        Each denial is audited before it is raised, so a refused spend leaves the
        same trail a refused scope check does.
        """
        profile = tool.spend
        assert profile is not None  # caller checks; narrows for the type checker

        async def deny(exc: ToolPermissionDenied) -> ToolPermissionDenied:
            await self._audit_call(
                tool_id=tool.tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=f"budget: {exc}",
            )
            return exc

        if self._spend_ledger is None:
            raise await deny(ToolSpendUnavailable(
                f"tool {tool.tool_id!r} is spend-capable but no spend ledger is "
                f"wired in this process; failing closed"
            ))

        # WHOSE budget. A v1.0 token carries no `on_behalf_of`
        # (contracts/base.py:239) and therefore names no human to charge. There is
        # no org-level fallback envelope by design — charging an unnamed principal
        # is how a spend ceiling silently stops binding anyone.
        on_behalf_of = governance_token.on_behalf_of
        if on_behalf_of is None:
            raise await deny(ToolSpendUnavailable(
                f"tool {tool.tool_id!r} is spend-capable but token "
                f"{governance_token.token_id} carries no on_behalf_of principal "
                f"(token_version={governance_token.token_version!r}); failing closed"
            ))

        amount = getattr(validated_input, profile.amount_field, None)
        # bool is an int subclass; `True` must not be read as 1 cent.
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise await deny(ToolSpendUnavailable(
                f"tool {tool.tool_id!r} declares spend field "
                f"{profile.amount_field!r} but the validated input carries "
                f"{amount!r}, which is not a positive integer minor-unit amount"
            ))

        try:
            return await self._spend_ledger.reserve(
                org_id=org_id,
                principal_id=on_behalf_of.principal_id,
                amount_minor=amount,
                # Unique per invocation, NOT derived from (correlation, agent,
                # tool). `try_reserve` treats a repeated idempotency_key as a
                # retry and returns the ORIGINAL hold, so a key shared by two
                # distinct calls would let the second spend against the first's
                # reservation. `invoke` has no retry/replay path — every call is a
                # distinct logical spend. Idempotent replay needs a
                # caller-supplied key, which this signature does not accept.
                idempotency_key=f"tool:{tool.tool_id}:{uuid4()}",
                correlation_id=correlation_id,
                governance_token_id=governance_token.token_id,
            )
        except CeilingExceeded as exc:
            raise await deny(
                ToolSpendDeferredToHuman(str(exc)) if exc.defer_to_human
                else ToolSpendHardDenied(str(exc))
            ) from exc
        except EnvelopeNotFound as exc:
            raise await deny(ToolSpendUnavailable(str(exc))) from exc

    async def _release_spend(
        self, reservation: Reservation | None, *, org_id: str
    ) -> None:
        """Best-effort release on the failure path.

        Swallows its own errors deliberately: this runs while an exception is
        already in flight, and a ledger hiccup here must not replace the real
        failure the caller needs to see. An unreleased hold is not lost budget —
        `sweep_expired` reclaims it at `expires_at`; masking the tool's actual
        error would be the worse outcome.
        """
        if reservation is None or self._spend_ledger is None:
            return
        try:
            await self._spend_ledger.release(
                org_id=org_id, reservation_id=reservation.reservation_id
            )
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception(
                "failed to release spend reservation %s; it will be swept at %s",
                reservation.reservation_id, reservation.expires_at,
            )

    async def _audit_call(
        self,
        *,
        tool_id: str,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        governance_token: GovernanceToken,
        result: str,
        reason: str | None,
        outputs: Any = None,
    ) -> None:
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="tool.invoked",
            result=result,
            source_agent_id=contract.agent_id,
            authority_level=contract.authority_level,
            governance_token_id=governance_token.token_id,
            inputs={"tool_id": tool_id},
            outputs=outputs,
            result_reason=reason,
        )
