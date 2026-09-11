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
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolCategory = Literal["memory", "search", "integration", "compute"]

#: Namespace for `ToolContext.replay_key`. Its own namespace, so a replay key can
#: never coincide with a hitl_id, a decision_id, or a GCP requestId.
_REPLAY_KEY_NS = uuid5(NAMESPACE_URL, "skylize.tools.replay_key")


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """Proof that the permission gate ran and AUTHORIZED this specific call.

    Produced ONLY by `ToolProxy._authorize_permission`. A handler that performs an
    elevated action (sharing a customer's file, say) must require one, because the
    gate is opt-in per tool and an opt-in check that a tool simply forgot to
    declare would otherwise dispatch ungated.

    That is the whole point: `ToolContext.permission_grant` is `None` unless the
    gate produced it, so an elevated handler reached WITHOUT a declared
    `ToolPermissionProfile` finds nothing here and refuses. Deny-by-default is
    therefore a data dependency the handler cannot satisfy on its own, not a
    convention a future author has to remember.
    """

    action_class: str
    grantee: str          # the address, or 'anyone' for a link grant
    role: str             # the role actually authorized (<= the org's max_role)
    matched_pattern: str  # which allow-list row authorized it, for the audit trail


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Per-call context handed to a tool's handler alongside its validated input."""

    org_id: str
    agent_id: str
    correlation_id: UUID
    #: Present ONLY when the permission gate authorized this call. Handlers that
    #: perform an elevated action MUST check it; see `PermissionGrant`. Defaults to
    #: None so every handler written before this field existed is unaffected —
    #: and so an elevated handler that is somehow reached ungated finds nothing.
    permission_grant: PermissionGrant | None = None
    #: The HITL ticket this call is replaying, when it is one. None on an ordinary
    #: (non-deferred) request path.
    #:
    #: LOAD-BEARING FOR EXTERNALLY-MUTATING HANDLERS, and the reason it is here
    #: rather than being derived from `correlation_id`. `HitlQueueService.approve`
    #: mints a FRESH correlation id on every approval attempt
    #: (app/hitl/service.py:160), and a transient failure after the claim releases
    #: the row back to 'pending' (app/hitl/service.py:234-246) so a human can
    #: retry — which re-executes the whole agent run. An idempotency key derived
    #: from `correlation_id` would therefore differ on every retry and defeat the
    #: provider-side deduplication it exists to trigger. `hitl_id` is stable
    #: across those retries; that is the entire point of threading it here.
    hitl_id: UUID | None = None
    #: The provider's id for the `tool_use` block that produced this call, when
    #: the caller is the agent tool loop. Informational on its own -- it is
    #: provider-scoped and carries no tenancy or ticket binding -- and load-bearing
    #: only in combination with the two fields around it. See `replay_key`.
    tool_use_id: str | None = None
    #: True ONLY for the dispatch of a turn a human approved and that was replayed
    #: from storage rather than re-sampled. Not true for the whole resumed run:
    #: turns AFTER the approved one are sampled fresh, so their block ids are no
    #: more stable than any other run's.
    is_hitl_resumption: bool = False

    def replay_key(self) -> UUID | None:
        """A RETRY-STABLE identity for this logical tool call, or None.

        None is the honest answer almost everywhere, and returning it is the
        point of this being a method rather than three fields a handler has to
        combine correctly.

        `tool_use_id` alone was rejected as a replay identity because "each
        approval attempt is a fresh LLM sampling and mints fresh block ids"
        (docs/architecture/spend_reservation_replay_semantics.md section 7). That
        is still true of every path except one: on a RESUMED turn the reviewed
        assistant message is read from storage and not re-sampled, so the block
        id is the same on every retry of that approval. Hence all three
        conditions below, together:

          * `is_hitl_resumption` -- the id came from storage, not from sampling;
          * `hitl_id`            -- binds the key to a ticket and a tenant, the
                                    same pairing `request_id_for(hitl_id, operation)`
                                    already uses (app/gcp/actions.py:127-138);
          * `tool_use_id`        -- distinguishes the calls within that turn.

        A caller that gets None must fall back to a run-level key and a
        once-per-run rule, not invent one from `correlation_id`, which is minted
        fresh on every approval attempt.
        """
        if not self.is_hitl_resumption or self.hitl_id is None or self.tool_use_id is None:
            return None
        return uuid5(_REPLAY_KEY_NS, f"{self.hitl_id}:{self.tool_use_id}")


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

    `actual_amount_field` is the settlement half of the same idea, read off the
    tool's VALIDATED OUTPUT instead of its input. A tool whose actual spend can
    come in BELOW what it asked to reserve — a refund the provider partially
    approves, an order the provider fills short — names the output field carrying
    what really moved, and `ToolProxy` settles the hold with THAT rather than with
    the reservation. Leaving it None declares the opposite and equally explicitly:
    this tool always spends what it reserved, so the reservation IS the actual.

    Not inferred from a conventional field name, and not discovered by probing the
    output for a plausible attribute. Either would make a tool that forgot to
    report a lower actual indistinguishable from one that correctly has none, and
    the ledger would over-commit without anything to notice it. Declaring it puts
    that difference in the registry entry, where `ToolDefinition` can — and does —
    check it against `output_schema` before the tool is ever registered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str = Field(min_length=3, max_length=3)
    amount_field: str = Field(min_length=1)
    #: Field on the VALIDATED OUTPUT carrying the amount that actually moved, in
    #: the same integer MINOR units as `amount_field`. None means "actual always
    #: equals reserved"; see the class docstring. Validated against the tool's
    #: `output_schema` in `ToolDefinition`, so a typo here fails at registration
    #: rather than silently over-committing a real spend.
    actual_amount_field: str | None = Field(default=None, min_length=1)


class ToolPermissionProfile(BaseModel):
    """Declares a tool as performing an ELEVATED ACTION requiring pre-authorization.

    The third opt-in gate on `ToolProxy.invoke`, alongside `ToolSpendProfile` and
    `ToolOAuthProfile` (integration_inputs.md 2.5, Q2.5d). The first two gate a
    tool on a RESOURCE it consumes — a live OAuth grant, a spend ceiling. This one
    gates it on the SHAPE OF THE ACTION: who the agent is about to hand a
    customer's data to.

    `action_class` keys the org's allow-list rows in `org_permission_grants`
    (migration 0022). An org with no rows for that class can perform the action
    with nobody: absence is denial.

    `grantee_field` and `role_field` name fields on the tool's VALIDATED input —
    read off the parsed `input_schema` instance, not the raw dict, so they have
    already passed the tool's own type validation. Same discipline as
    `ToolSpendProfile.amount_field`.

    Deliberately NOT a callable predicate: what an agent is about to grant, and to
    whom, has to be inspectable and auditable BEFORE dispatch, and a lambda in a
    registry entry is neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_class: str = Field(min_length=1)
    grantee_field: str = Field(min_length=1)
    role_field: str = Field(min_length=1)
    #: Value of `grantee_field` meaning "anyone with the link" — a grant that names
    #: no recipient at all. Gated by the allow-list's separate `allow_link_sharing`
    #: flag rather than by any address pattern, because it is a different risk
    #: class, not a broader pattern.
    link_sharing_sentinel: str = "anyone"


class ToolWifProfile(BaseModel):
    """Declares a tool DEPENDENT ON A LIVE WORKLOAD IDENTITY FEDERATION TRUST.

    The fourth opt-in gate on `ToolProxy.invoke`, alongside `ToolSpendProfile`,
    `ToolPermissionProfile` and `ToolOAuthProfile`. It is a separate profile from
    `ToolOAuthProfile` for the same reason `gcp_wif_connections` is a separate
    table from `oauth_credentials` (migration 0024): a federation trust is not a
    stored grant. There is no token to hold, nothing to refresh, and no expiry —
    a short-lived credential is minted per call at Google's Security Token
    Service from a trust relationship the customer can revoke at any moment.

    WHAT IT CHECKS, and why each check is at the gate rather than in the handler:
      1. a connection row exists for the org;
      2. its `connection_state` is 'valid' — a connection the health probe has
         already found broken must be refused BEFORE anything is minted, so an
         urgent action fails in milliseconds with the right remedy named rather
         than after a round trip to Google;
      3. the requested resource is an ENABLED row in `gcp_wif_targets`.

    A handler could do all three. It would then be invisible in the registry
    entry, which is precisely what the other three profiles exist to avoid: all
    of them "refuse callable predicates on purpose, so a gate stays inspectable"
    (see `ToolSpendProfile` and `ToolPermissionProfile` above). A gate that only
    exists inside a function body is one refactor from being skipped.

    `label` selects WHICH connection when an org has more than one, matching the
    `label` semantics `oauth_credentials` and `org_credentials` already use.

    `project_field` / `zone_field` / `instance_field` name fields on the tool's
    VALIDATED input — read off the parsed `input_schema` instance, not the raw
    dict, so they have already passed the tool's own type validation. Same
    discipline as `ToolSpendProfile.amount_field`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = ""
    project_field: str = Field(min_length=1)
    zone_field: str = Field(min_length=1)
    instance_field: str = Field(min_length=1)


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


class ToolApprovalProfile(BaseModel):
    """Declares that invoking this tool requires a HUMAN APPROVAL OF THE EXACT
    SAMPLED CALL, not merely of the request that started the run.

    Opt-in and explicit, exactly like the three profiles above and `ToolWifProfile`:
    a tool without this profile keeps precisely the behaviour it had before the
    mid-loop suspension gate existed. That default is what preserves the property
    at app/agents/execution.py:292-293 -- "a reject/defer verdict means no LLM
    call, no deliverable, and no ledger row" -- for every contract that does not
    opt in. Declaring this field is what knowingly gives that property up, in
    exchange for the human reviewing an ACTION instead of a REQUEST (owner
    decision D1, docs/architecture/hitl_approval_resumption_design.md).

    WHY THIS LIVES ON THE TOOL AND NOT ON THE CONTRACT'S human_in_loop_triggers.
    The synchronous decision gate's evaluator "runs before the mint and before
    the model, against a proposal with no spend, no scope and no security
    verdict, so trigger PRESENCE is all it can observe"
    (app/decision_engine/evaluator.py:211-220). It is therefore structurally
    unable to answer "is THIS sampled call gated". The tool is the thing whose
    invocation moves money or mutates the world, so the tool is where the
    declaration belongs -- the same seam the spend / oauth / permission / wif
    profiles already use.

    `reason` is shown to the reviewer and recorded in the queue row's
    trigger_reason. `irreversible` is disclosure, not control flow: it tells the
    reviewer that rejecting stops what has not happened and does not reverse what
    has, because no compensation mechanism exists anywhere in this codebase.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)
    irreversible: bool = True


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
    #: Non-None marks this tool as performing an elevated action requiring org
    #: pre-authorization; see `ToolPermissionProfile`. Defaults to None like the
    #: two above — but note the backstop: a handler performing an elevated action
    #: must ALSO require `ToolContext.permission_grant`, so forgetting this field
    #: fails closed at dispatch rather than silently skipping the gate.
    permission: ToolPermissionProfile | None = None
    #: Non-None marks this tool dependent on a live GCP Workload Identity
    #: Federation trust; see `ToolWifProfile`. Defaults to None like the three
    #: above, so every tool registered before this field existed is unaffected.
    wif: ToolWifProfile | None = None
    #: Non-None marks this tool as requiring a human approval of the exact
    #: sampled call; see `ToolApprovalProfile`. Defaults to None like the four
    #: above. Unlike them it is NOT enforced in the proxy: the gate that reads it
    #: lives in the agent tool loop, because only there does the whole turn (and
    #: the conversation prefix a resumption needs) exist at once. See
    #: AgentExecutionService._govern_tool_turn.
    approval: ToolApprovalProfile | None = None

    @model_validator(mode="after")
    def _spend_fields_exist_on_their_schemas(self) -> ToolDefinition:
        """Reject a spend profile naming a field its schemas do not have.

        Runs at CONSTRUCTION, which is the whole point. `ToolProxy` reads
        `amount_field` off the validated input and `actual_amount_field` off the
        validated output; a typo in either is a silent money bug at the moment it
        finally matters, and `actual_amount_field` is the worse of the two — a
        declared-but-absent output field would send the proxy back to committing
        the reservation, over-committing a spend that came in lower, which is
        exactly the failure declaring the field was meant to prevent.

        Checking here rather than in `ToolRegistry.validate_schemas` is
        deliberate: a `ToolDefinition` that never reaches a registry (a test
        fixture, a directly-dispatched tool) is just as capable of moving money.
        """
        if self.spend is None:
            return self
        for field_name, schema, which in (
            (self.spend.amount_field, self.input_schema, "input_schema"),
            (self.spend.actual_amount_field, self.output_schema, "output_schema"),
        ):
            if field_name is None:
                continue
            if field_name not in schema.model_fields:
                raise ValueError(
                    f"tool_id={self.tool_id!r} declares a spend field "
                    f"{field_name!r} that {which} {schema.__name__!r} does not "
                    f"define; a spend field the proxy cannot read is a silent "
                    f"money bug, so registration fails closed"
                )
        return self


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


class ToolSpendKeyConflict(ToolPermissionDenied):
    """The reservation KEY was refused, not the amount. The spend did not happen.

    Raised when the ledger reports `ReservationConflict`
    (app/principal/errors.py:116): the `(org_id, idempotency_key)` pair already
    backs a reservation recorded for a DIFFERENT amount
    (app/principal/spend.py:316-318). A repeat with a MATCHING amount is not this
    error -- that is an ordinary idempotent retry and returns the original hold.

    Its own branch with `failed_stage="reservation"`, deliberately NOT a
    `ToolSpendDenied`, on exactly the reasoning `ToolCredentialDenied` records. A
    key collision and an exhausted ceiling are unrelated conditions with
    unrelated remedies -- the ceiling wants a limit raised or a human approval,
    this wants the CALLER to stop reusing one key for a changed amount -- and
    collapsing them would make both unactionable in the audit trail.

    Subclassing `ToolSpendDenied` would also hand this a `defer_to_human` flag it
    has no business carrying. Routing a caller-side idempotency fault into a
    human approval queue as though it were an overspend is precisely the
    misrouting that flag was split into three types to prevent. For the same
    reason the containment auto-hook does NOT fire for it (tools/proxy.py): a
    repeated key is not a customer overspending, and acting on an unverified
    signal is how a safety control starts stopping healthy machines.

    That it is a `ToolError` at all is the substance of the fix. Until this type
    existed the underlying `ReservationConflict` left `ToolProxy.invoke` as a
    bare `BudgetError`, missed the `except ToolError` branch that turns a refused
    call into an error `tool_result` (app/agents/execution.py:981), and faulted
    the entire agent run over one bad key.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="reservation")


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


class ToolPermissionTierDenied(ToolPermissionDenied):
    """An ELEVATED ACTION was refused by the org's pre-authorization allow-list.

    Its own branch with `failed_stage="permission_tier"`, deliberately distinct
    from BOTH `ToolCredentialDenied` and `ToolSpendDenied`. Three unrelated
    conditions with three unrelated remedies: a dead grant needs the customer to
    reconnect, an exhausted budget needs a ceiling raised, and this needs an
    operator to pre-authorize a recipient. Collapsing any two would make both
    unactionable in the audit trail — the same reasoning that gave
    `ToolConvergenceDenied` and `ToolCallLimitExceeded` their own stages rather
    than shoehorning them into `scope`.

    `defer_to_human` mirrors `ToolSpendDenied`'s flag and carries the same
    meaning: the tool did NOT run, but a human COULD authorize this. The gate
    never enqueues a HITL row itself — routing happens at the request boundary,
    where a replay is safe. Enqueuing mid-call would defer a single tool call into
    a queue whose approve path replays the WHOLE agent execution
    (`app/hitl/service.py:187`), creating the deliverable a second time.
    """

    def __init__(self, reason: str, *, defer_to_human: bool = True) -> None:
        super().__init__(reason, failed_stage="permission_tier")
        self.defer_to_human = defer_to_human


class ToolPermissionUnavailable(ToolPermissionTierDenied):
    """The elevated action could not be EVALUATED, so the call FAILS CLOSED.

    Raised when no permission gate is wired, the tool declares a profile whose
    named fields are absent or unreadable on the validated input, or — the
    security-critical case — a handler performing an elevated action was reached
    with no `PermissionGrant` in its `ToolContext`, meaning the tool never
    declared a `ToolPermissionProfile` and the gate never ran.

    Its own type for the reason `ToolSpendUnavailable` and
    `ToolCredentialUnavailable` have theirs: "we could not check" must never be
    collapsed into "the operator declined to authorize this". `defer_to_human` is
    False — there is nothing coherent for a human to approve until the
    misconfiguration is fixed.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, defer_to_human=False)


class ToolInputError(ToolError):
    """`input_data` failed validation against the tool's `input_schema`."""


class ToolExecutionError(ToolError):
    """The tool's handler raised while executing."""


class ToolWifDenied(ToolPermissionDenied):
    """A WIF-dependent tool call was refused at the federation gate.

    Subclasses `ToolPermissionDenied` so it routes through the SAME denied-call
    audit path the scope, budget and credential denials use, rather than
    inventing a second denial taxonomy. Never raised directly — always one of the
    three subclasses below, so a caller can branch on the TYPE and an operator is
    never handed the wrong remedy.
    """


class ToolWifNotConnected(ToolWifDenied):
    """No GCP federation is configured for this org. REMEDY: run onboarding."""


class ToolWifTrustBroken(ToolWifDenied):
    """The federation exists but is not usable, and the health probe already knew.

    Carries the connection's own remedy wording, because the two broken states
    need OPPOSITE customer actions: 'revoked' means the pool or provider is gone
    and the federation must be re-created; 'misconfigured' means the trust works
    and one setting (an IAM binding, an audience, an attribute condition) is
    wrong. Telling the second customer to reconnect would be actively wrong.

    Raised BEFORE any token is minted. That ordering is the point: an urgent
    action against a connection already known to be broken must fail in
    milliseconds naming the fix, not after a round trip to Google.
    """


class ToolWifTargetNotAllowed(ToolWifDenied):
    """The requested instance is not an enabled row in `gcp_wif_targets`.

    Deny-by-default over the customer's own allow-list: an org that has federated
    a project has NOT thereby authorised every machine in it. Absence is denial.
    """
