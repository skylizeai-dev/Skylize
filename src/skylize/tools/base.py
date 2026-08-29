"""ToolDefinition — the agent-facing equivalent of AgentContract.

A tool is a typed, versioned capability an agent may invoke through the tool
proxy (`IF-TOOL`). `description` is sent to the LLM verbatim as the tool-use
description, so it must precisely state when the tool applies.

`tool_id` doubles as the governance scope key: `GovernanceAuthority.mint`
defaults a token's `scope` to the agent contract's `ToolGrant.tool_id` list,
and `contracts.token.validate_tool_call` checks membership by that same
string. There is no separate scope namespace in this codebase — keep tool_ids
and ToolGrant.tool_ids identical.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ToolCategory = Literal["memory", "search", "integration", "compute"]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Per-call context handed to a tool's handler alongside its validated input."""

    org_id: str
    agent_id: str
    correlation_id: UUID


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


class ToolSpendProfile(BaseModel):
    """Declares a tool SPEND-CAPABLE: invoking it moves real money.

    Most tools are not. A tool without this profile keeps exactly the behaviour it
    had before the spend ledger existed — no reservation, no ledger round-trip —
    so declaring spend-capability is opt-in and explicit rather than inferred from
    a category or a naming convention.

    `amount_field` names the field on the tool's VALIDATED input carrying the
    amount in integer MINOR units (cents), the same unit `SpendEnvelope` and
    `budget_ledger` use. It is read off the parsed `input_schema` instance, not
    the raw dict, so it has already passed the tool's own type validation.

    Deliberately NOT a callable estimator: the amount a tool is about to spend has
    to be inspectable and auditable before dispatch, and a lambda in a registry
    entry is neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    amount_field: str = Field(min_length=1)


class ToolOAuthProfile(BaseModel):
    """Declares a tool DEPENDENT ON A LIVE OAUTH GRANT for `provider`.

    Opt-in and explicit, exactly like `ToolSpendProfile`: a tool without this
    profile keeps precisely the behaviour it had before the OAuth infrastructure
    existed — no grant lookup, no refresh, no round trip. Provider-agnostic by
    construction; `provider` is a registry key ('google_drive', 'notion', ...),
    never a hardcoded provider inside the proxy.

    `label` selects WHICH connection when an org has more than one for the same
    provider; '' is the default connection, matching the `label` semantics
    org_credentials already uses (migration 0007:41).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    label: str = ""


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    tool_id: str
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    category: ToolCategory
    handler: ToolHandler
    #: Non-None marks this tool spend-capable; see `ToolSpendProfile`. Defaults to
    #: None so every tool registered before this field existed is unaffected.
    spend: ToolSpendProfile | None = None
    #: Non-None marks this tool dependent on a live OAuth grant; see
    #: `ToolOAuthProfile`. Defaults to None for the same reason `spend` does.
    oauth: ToolOAuthProfile | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_id: str
    output: BaseModel
    call_id: str | None = None

    def output_json(self) -> dict[str, Any]:
        return self.output.model_dump(mode="json")


class ToolError(Exception):
    """Base of the tool-invocation error hierarchy."""


class ToolNotRegistered(ToolError):
    """Unknown tool_id — the registry fails closed (IF-TOOL 404 equivalent)."""


class ToolPermissionDenied(ToolError):
    """Governance token failed `validate_tool_call` (IF-TOOL 403 equivalent)."""

    def __init__(self, reason: str, *, failed_stage: str | None = None) -> None:
        super().__init__(reason)
        self.failed_stage = failed_stage


class ToolConvergenceDenied(ToolPermissionDenied):
    """The convergence breaker tripped: this agent repeated the same tool call
    (same input) back-to-back within the workflow, so the runaway loop was
    suspended by the Governance Authority (agent_governance.md §7).

    Subclasses ToolPermissionDenied so it routes through the same denied-call
    audit/handling path the scope/budget/revocation denials already use; the
    Authority has already emitted the convergence_failure + breaker events.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="convergence")


class ToolCallLimitExceeded(ToolPermissionDenied):
    """The tool was invoked more than its `ToolGrant.max_calls_per_run` times
    within this workflow (agent_governance.md §6). This is a proxy-side ceiling
    derived from the contract, not one of `contracts.token.ValidationStage`'s
    token-validation stages (signature/expiry/revocation/scope/budget/
    delegation), so — mirroring `ToolConvergenceDenied` — it gets its own
    `failed_stage` rather than being shoehorned into `scope` or `budget`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="call_limit")


class ToolSpendDenied(ToolPermissionDenied):
    """A spend-capable tool call was refused by the spend ledger.

    Subclasses `ToolPermissionDenied` with `failed_stage="budget"` so it routes
    through the SAME denied-call audit path the scope/revocation denials use, and
    so `failed_stage` reuses the existing `ValidationStage.BUDGET` vocabulary
    (contracts/token.py) rather than inventing a second denial taxonomy.

    Never raised directly — always one of the three subclasses below, so a caller
    can branch on the TYPE. The `over_ceiling_behavior` distinction is a policy
    decision the customer configured on the envelope; collapsing it into one error
    with a boolean flag invites the flag being ignored at the call site.
    """

    def __init__(self, reason: str, *, defer_to_human: bool) -> None:
        super().__init__(reason, failed_stage="budget")
        self.defer_to_human = defer_to_human


class ToolSpendHardDenied(ToolSpendDenied):
    """Ceiling exceeded on an envelope whose `over_ceiling_behavior='hard_deny'`.

    Terminal. The action does not happen and there is no human to ask — the
    customer configured this envelope to stop, not to escalate.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, defer_to_human=False)


class ToolSpendDeferredToHuman(ToolSpendDenied):
    """Ceiling exceeded on an envelope whose `over_ceiling_behavior='defer_to_human'`.

    Also a denial — the tool did NOT run — but a recoverable one: the caller may
    route it to the HITL queue. Distinct from `ToolSpendHardDenied` precisely so
    that routing decision cannot be made by accident.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, defer_to_human=True)


class ToolSpendUnavailable(ToolSpendDenied):
    """The spend ceiling could not be evaluated, so the call FAILS CLOSED.

    Raised when a spend-capable tool is invoked but: no ledger is wired, the token
    carries no `on_behalf_of` principal to charge (a v1.0 autonomous token —
    contracts/base.py:239), no active envelope exists, or the declared amount is
    unreadable/non-positive.

    Its own type on purpose: "we could not check" must never be collapsed into
    "there was nothing to find" — mirroring `AuthorityUnavailable`
    (app/principal/errors.py:48-57). Both deny; only this one is an operational
    fault worth alerting on. Absence of a budget is never unlimited budget.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, defer_to_human=False)


class ToolCredentialDenied(ToolPermissionDenied):
    """A tool requiring a live OAuth grant was refused on CREDENTIAL STATE.

    Its own branch of the hierarchy with `failed_stage="credential"`, deliberately
    NOT reusing the `ToolSpendDenied` types. A dead Google grant and an exhausted
    budget are unrelated conditions with unrelated remedies — one needs the
    customer to reconnect an integration, the other needs a ceiling raised or a
    human approval — and collapsing them would make both unactionable in the
    audit trail. `failed_stage` is likewise its own value rather than being
    shoehorned into `scope` or `budget`, mirroring how `ToolConvergenceDenied`
    and `ToolCallLimitExceeded` each took their own.

    Never raised directly — always one of the two subclasses below, so a caller
    can branch on the TYPE rather than parsing a reason string.
    """

    def __init__(self, reason: str, *, reconnect_required: bool) -> None:
        super().__init__(reason, failed_stage="credential")
        self.reconnect_required = reconnect_required


class ToolCredentialReconnectRequired(ToolCredentialDenied):
    """The grant is dead: absent, expired beyond repair, or revoked upstream.

    A human must complete an out-of-band OAuth consent flow; no retry and no
    approval inside Skylize can clear it. `reconnect_required=True` is what a
    future dashboard surface reads to decide whether to prompt.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, reconnect_required=True)


class ToolCredentialUnavailable(ToolCredentialDenied):
    """The grant could not be EVALUATED, so the call FAILS CLOSED.

    Raised when no OAuth service is wired, the provider is unregistered, or the
    token endpoint was unreachable. Its own type for the same reason
    `ToolSpendUnavailable` has one: "we could not check" must never be collapsed
    into "the customer disconnected us". This one is an operational fault worth
    alerting on, and it must NOT drive a reconnect prompt — nothing about the
    customer's connection is known to be wrong.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, reconnect_required=False)


class ToolInputError(ToolError):
    """`input_data` failed validation against the tool's `input_schema`."""


class ToolExecutionError(ToolError):
    """The tool's handler raised while executing."""
