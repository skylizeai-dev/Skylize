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
from ..app.credentials.oauth import (
    GrantNotConnected,
    GrantRevoked,
    OAuthCredentialService,
    RefreshUnavailable,
)
from ..app.permissions.gate import (
    PermissionDeniedError,
    PermissionGate,
    PermissionUnavailableError,
)
from ..app.principal.errors import CeilingExceeded, EnvelopeNotFound
from ..app.principal.models import Reservation
from ..app.principal.spend import SpendLedger
from ..contracts.base import AgentContract, GovernanceToken
from ..contracts.token import LiveStateChecker, validate_tool_call
from ..dal.gcp_wif import GcpWifRepository
from .base import (
    PermissionGrant,
    ToolCallLimitExceeded,
    ToolContext,
    ToolConvergenceDenied,
    ToolCredentialDenied,
    ToolCredentialReconnectRequired,
    ToolCredentialUnavailable,
    ToolDefinition,
    ToolExecutionError,
    ToolInputError,
    ToolNotRegistered,
    ToolPermissionDenied,
    ToolPermissionTierDenied,
    ToolPermissionUnavailable,
    ToolWifNotConnected,
    ToolWifTargetNotAllowed,
    ToolWifTrustBroken,
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
        oauth_credentials: OAuthCredentialService | None = None,
        permission_gate: PermissionGate | None = None,
        wif_repo: "GcpWifRepository | None" = None,
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
        # None where no OAuth infrastructure is wired (memory backend, most unit
        # harnesses). A tool declaring an `oauth` profile then FAILS CLOSED in
        # `_ensure_oauth_credential` rather than dispatching against a grant
        # nobody checked — same reasoning as the spend ledger above.
        self._oauth_credentials = oauth_credentials
        # None where no permission infrastructure is wired. A tool declaring a
        # `permission` profile then FAILS CLOSED in `_authorize_permission`
        # rather than performing an elevated action nobody authorized.
        self._permission_gate = permission_gate
        # None where no GCP federation infrastructure is wired. A tool
        # declaring a `wif` profile then FAILS CLOSED in `_authorize_wif`
        # rather than mutating a customer's infrastructure through a trust
        # nobody checked — same reasoning as the three gates above.
        self._wif_repo = wif_repo

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
        # The HITL ticket being replayed, when this call is one. Threaded to
        # the handler through `ToolContext` so an externally-mutating tool can
        # derive a RETRY-STABLE idempotency key from it; see ToolContext.hitl_id.
        hitl_id: UUID | None = None,
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

        # Credential state (OAuthCredentialService). Only for tools declaring an
        # `oauth` profile, and placed HERE — after every local check, but BEFORE
        # the spend reservation below — deliberately.
        #
        # The comment on the spend block is the reason: a hold "must be placed as
        # late as possible, after every cheaper denial has had its chance". A dead
        # or unrefreshable credential IS such a denial. Ordered the other way, a
        # revoked Google grant would first reserve budget against the customer's
        # ceiling and only then discover the call could never run, leaving a hold
        # to unwind — the exact waste the spend block was written to avoid.
        #
        # It sits after input validation because refreshing may cost a network
        # round trip, and the cheapest denials must always run first.
        if tool.oauth is not None:
            await self._ensure_oauth_credential(
                tool=tool, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
            )

        # Elevated-action pre-authorization (PermissionGate). Only for tools
        # declaring a `permission` profile. AFTER the OAuth stage above: there is
        # nothing to authorize on a call that has no usable credential, and
        # evaluating the recipient of a share that could never be sent would leak
        # "this share would have been allowed" for a disconnected integration.
        #
        # BEFORE the spend reservation below, for the same reason the OAuth stage
        # is: this is a tenant-scoped DB read, cheaper than a hold on the shared
        # mutable ceiling, and the hold must be placed last.
        #
        # `permission_grant` is threaded onto the ToolContext handed to the
        # handler. That is load-bearing, not informational: an elevated handler
        # REQUIRES it (see `PermissionGrant`), so a sharing tool that forgot to
        # declare a profile reaches its handler with None here and fails closed
        # instead of dispatching ungated.
        permission_grant: PermissionGrant | None = None
        if tool.permission is not None:
            permission_grant = await self._authorize_permission(
                tool=tool, validated_input=validated_input, contract=contract,
                org_id=org_id, correlation_id=correlation_id,
                governance_token=governance_token,
            )

        # GCP federation trust (gcp_wif_connections). Only for tools declaring a
        # `wif` profile. Placed AFTER the permission stage and BEFORE the spend
        # reservation, for the reason the OAuth stage gives at its own site: a
        # broken federation IS a denial, and ordering it after the spend hold
        # would reserve budget against the customer's ceiling only to discover
        # the call could never run, leaving a hold to unwind.
        #
        # It also refuses on a connection the health probe has ALREADY found
        # broken, without minting anything. That is the whole value of the probe
        # existing: an urgent action fails in milliseconds naming the remedy,
        # instead of after a round trip to Google at the worst possible moment.
        if tool.wif is not None:
            await self._authorize_wif(
                tool=tool, validated_input=validated_input, contract=contract,
                org_id=org_id, correlation_id=correlation_id,
                governance_token=governance_token,
            )

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

        context = ToolContext(
            org_id=org_id, agent_id=contract.agent_id, correlation_id=correlation_id,
            permission_grant=permission_grant, hitl_id=hitl_id,
        )
        try:
            output = await tool.handler(validated_input, context)
        except ToolPermissionDenied as exc:
            # A GOVERNANCE DENIAL raised from inside a handler stays a governance
            # denial. The deny-by-default backstop lives here: a handler that
            # performs an elevated action refuses when it was dispatched without
            # a PermissionGrant (see `PermissionGrant`). Flattening that into
            # ToolExecutionError below would audit a refused share as a generic
            # "handler error" and hide it from any caller branching on the denial
            # type — the type IS the signal that a gate was missing.
            await self._release_spend(reservation, org_id=org_id)
            await self._audit_call(
                tool_id=tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=f"handler denied: {exc}",
            )
            raise
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

    async def _ensure_oauth_credential(
        self,
        *,
        tool: ToolDefinition,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        governance_token: GovernanceToken,
    ) -> None:
        """Guarantee a live OAuth grant, or deny. Every exit that is not a return
        denies, and every denial is audited before it is raised — the same
        discipline `_reserve_spend` follows.

        This method never returns the token. Its job is to establish that a
        usable grant EXISTS (refreshing on demand if needed); the connector then
        resolves the token itself per call, as the HubSpot precedent does
        (tools/builtin/hubspot_tools.py:3-6). Keeping the secret off the call
        path means it never lands on `ToolContext`, which is handed to every
        handler and is trivially logged.
        """
        profile = tool.oauth
        assert profile is not None  # caller checks; narrows for the type checker

        async def deny(exc: ToolCredentialDenied) -> ToolCredentialDenied:
            await self._audit_call(
                tool_id=tool.tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=f"credential: {exc}",
            )
            return exc

        if self._oauth_credentials is None:
            raise await deny(ToolCredentialUnavailable(
                f"tool {tool.tool_id!r} requires a {profile.provider!r} OAuth grant "
                f"but no OAuth credential service is wired in this process; "
                f"failing closed"
            ))

        try:
            await self._oauth_credentials.ensure_fresh(
                org_id=org_id,
                provider=profile.provider,
                label=profile.label,
                correlation_id=correlation_id,
            )
        except (GrantNotConnected, GrantRevoked) as exc:
            # Terminal for this call: a human must reconnect upstream. No retry
            # and no in-Skylize approval can clear it.
            raise await deny(ToolCredentialReconnectRequired(str(exc))) from exc
        except RefreshUnavailable as exc:
            # We could not CHECK. Fail closed, but do not claim the customer
            # disconnected us — nothing about their grant is known to be wrong.
            raise await deny(ToolCredentialUnavailable(str(exc))) from exc

    async def _authorize_permission(
        self,
        *,
        tool: ToolDefinition,
        validated_input: Any,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        governance_token: GovernanceToken,
    ) -> PermissionGrant:
        """Authorize one elevated action, or deny. Every exit that is not a
        `PermissionGrant` denies, and every denial is audited before it is raised —
        the same discipline `_reserve_spend` and `_ensure_oauth_credential` follow.

        Reads the grantee and role off the VALIDATED input, never the raw dict, so
        both have already passed the tool's own type validation.
        """
        profile = tool.permission
        assert profile is not None  # caller checks; narrows for the type checker

        async def deny(exc: ToolPermissionTierDenied) -> ToolPermissionTierDenied:
            await self._audit_call(
                tool_id=tool.tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=f"permission: {exc}",
            )
            return exc

        if self._permission_gate is None:
            raise await deny(ToolPermissionUnavailable(
                f"tool {tool.tool_id!r} declares an elevated action "
                f"({profile.action_class!r}) but no permission gate is wired in "
                f"this process; failing closed"
            ))

        grantee = getattr(validated_input, profile.grantee_field, None)
        role = getattr(validated_input, profile.role_field, None)
        if not isinstance(grantee, str) or not grantee:
            raise await deny(ToolPermissionUnavailable(
                f"tool {tool.tool_id!r} declares grantee field "
                f"{profile.grantee_field!r} but the validated input carries "
                f"{grantee!r}, which is not a non-empty string"
            ))
        if not isinstance(role, str) or not role:
            raise await deny(ToolPermissionUnavailable(
                f"tool {tool.tool_id!r} declares role field {profile.role_field!r} "
                f"but the validated input carries {role!r}, which is not a "
                f"non-empty string"
            ))

        try:
            grant = await self._permission_gate.authorize(
                org_id=org_id,
                action_class=profile.action_class,
                grantee=grantee,
                role=role,
                link_sharing_sentinel=profile.link_sharing_sentinel,
            )
        except PermissionDeniedError as exc:
            # The operator has not pre-authorized this. A human COULD, so the
            # caller may route it to HITL at the request boundary — but this gate
            # never enqueues mid-call (see ToolPermissionTierDenied).
            raise await deny(ToolPermissionTierDenied(str(exc))) from exc
        except PermissionUnavailableError as exc:
            raise await deny(ToolPermissionUnavailable(str(exc))) from exc

        await self._audit_call(
            tool_id=tool.tool_id, contract=contract, org_id=org_id,
            correlation_id=correlation_id, governance_token=governance_token,
            result="success",
            reason=(
                f"permission authorized: {profile.action_class} -> "
                f"{grant.grantee} as {grant.role} (matched {grant.matched_pattern!r})"
            ),
        )
        return grant

    async def _authorize_wif(
        self,
        *,
        tool: ToolDefinition,
        validated_input: Any,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        governance_token: GovernanceToken,
    ) -> None:
        """Refuse unless a LIVE federation covers the EXACT resource requested.

        Every exit that is not a silent return denies, and each denial is audited
        before it is raised, so a refused federation leaves the same trail a
        refused scope check does.

        Three denials with three different remedies, kept as three types because
        collapsing them would hand an operator the wrong fix during an incident:
        not connected (onboard), trust broken (re-create OR edit one setting,
        depending on which broken state), target not allowed (add the instance).
        """
        profile = tool.wif
        assert profile is not None  # caller checks; narrows for the type checker

        async def deny(exc: ToolPermissionDenied) -> ToolPermissionDenied:
            await self._audit_call(
                tool_id=tool.tool_id, contract=contract, org_id=org_id,
                correlation_id=correlation_id, governance_token=governance_token,
                result="denied", reason=str(exc),
            )
            return exc

        if self._wif_repo is None:
            # FAIL CLOSED, exactly like the other three gates on a missing
            # dependency. A tool that mutates a customer's infrastructure must
            # never dispatch because the check itself was not wired.
            raise await deny(ToolWifNotConnected(
                f"tool {tool.tool_id!r} declares a WIF profile but no federation "
                "store is wired; refusing to act on customer infrastructure "
                "through an unchecked trust"
            ))

        row = await self._wif_repo.get(org_id, profile.label)
        if row is None:
            raise await deny(ToolWifNotConnected(
                f"org {org_id!r} has no GCP federation configured"
                + (f" for label {profile.label!r}" if profile.label else "")
                + "; run GCP onboarding before this action can be authorized"
            ))

        if row.connection_state != "valid":
            # The probe already established this. Refuse before minting anything,
            # and carry the connection's own reason so the message names the
            # remedy for THIS broken state rather than a generic one.
            raise await deny(ToolWifTrustBroken(
                f"GCP federation for org {org_id!r} is {row.connection_state!r} "
                f"and cannot be used: {row.state_reason or 'no reason recorded'}"
            ))

        project = getattr(validated_input, profile.project_field, None)
        zone = getattr(validated_input, profile.zone_field, None)
        instance = getattr(validated_input, profile.instance_field, None)
        if not (isinstance(project, str) and isinstance(zone, str)
                and isinstance(instance, str)):
            raise await deny(ToolWifTargetNotAllowed(
                f"tool {tool.tool_id!r} did not supply a readable "
                "project/zone/instance triple to authorize"
            ))

        targets = await self._wif_repo.list_targets(
            org_id, row.conn_id, enabled_only=True
        )
        allowed = any(
            t.gcp_project_id == project and t.zone == zone
            and t.instance_name == instance
            for t in targets
        )
        if not allowed:
            # Deny-by-default over the customer's own allow-list. Federating a
            # project does NOT authorise every machine in it.
            raise await deny(ToolWifTargetNotAllowed(
                f"{project}/{zone}/{instance} is not an enabled target for org "
                f"{org_id!r}; add it to the GCP target list to authorize this action"
            ))


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
