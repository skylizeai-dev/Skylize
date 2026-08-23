# Capital-check wiring on the tool-call path — design proposal (DRAFT, unapproved)

**Status:** DRAFT / design only. No production code was changed in the pass that
produced this document. Nothing here is ratified; §11 lists the decisions an owner
must make before any of it is implemented.

**Scope:** §1.1 capital enforcement gap — an agent tool call that moves real money is
dispatched today without ever reaching `capital_check`.

**Audit basis:** every claim below is cited to `file:line` as read at commit `5e11959`
on branch `feat/durable-governance`. Docs are claims; code is ground truth (CLAUDE.md,
EVIDENCE DISCIPLINE).

---

## 1. Confirmed current state

Each item was re-read directly, not carried over from a prior audit.

### 1.1 `ToolProxy` declares tool calls cost-free

`src/skylize/tools/proxy.py:114-122`:

```python
validation = validate_tool_call(
    token=governance_token,
    public_key=self._public_key,
    requested_tool_id=tool_id,
    contract_allowed_tool_ids=allowed_tool_ids,
    requested_token_cost=0,  # tool calls don't debit the LLM token budget
    tokens_used_so_far=0,
    live_state=self._live_state_for(org_id),
)
```

Lines 119-120 are **correct as written and must not change**. `validate_tool_call`'s
BUDGET stage governs `GovernanceToken.max_token_budget`
(`src/skylize/contracts/base.py:226`) — an *LLM token* ceiling. A tool call genuinely
does not debit that ceiling. The comment is accurate. The defect is not that these
zeros are wrong; it is that **no other ceiling is consulted anywhere in
`ToolProxy.invoke`.**

### 1.2 `ToolProxy` has no path to an evaluator or a capital repository

Reconfirmed. `ToolProxy.__init__` (`src/skylize/tools/proxy.py:73-86`) accepts exactly
five collaborators:

| Parameter | Type | Line |
|---|---|---|
| `registry` | `ToolRegistry` | proxy.py:76 |
| `audit` | `AuditService` | proxy.py:77 |
| `public_key` | `EllipticCurvePublicKey` | proxy.py:78 |
| `live_state_for` | `LiveStateFor` | proxy.py:79 |
| `record_action` | `RecordAction \| None` | proxy.py:80 |

There is no `DecisionEvaluator`, no `CapitalRepository`, no `SpendLedger`, no HITL
port. The sole construction site, `src/skylize/bootstrap.py:538-543`, passes four of
the five (`record_action` is not supplied). The module's import block
(`proxy.py:21-40`) contains no `decision_engine` or `dal` import of any kind.

**Confirmed: no path exists today.** The prior audit's finding stands.

### 1.3 The evaluator returns terminally before the capital stage for synchronous work

`src/skylize/app/decision_engine/evaluator.py:139-140`:

```python
if proposal.action_kind == AGENT_EXECUTE_ACTION_KIND:
    return self._decide_agent_execution(proposal, contract, stages)
```

This is stage 2.5. It fires after authority (stage 1) and policy (stage 2) and returns
**terminally**, so the only existing synchronous vertical — the
`POST /api/v1/agents/execute` gate — never reaches stage 3 (scoring, evaluator.py:143-149)
or stage 4 (capital, evaluator.py:151-155). That is deliberate and documented as owner
decision K2 (evaluator.py:130-137): the vertical is decided by `_decide_agent_execution`
alone so it can never ride the generic default-approve at evaluator.py:173-184.

Consequence for this design: **a new synchronous vertical placed at 2.5 inherits the
same skip.** Reaching `capital_check` requires either routing around 2.5 or building
the capital stage into the new vertical explicitly. §6.3 chooses the latter.

### 1.4 `capital_check` itself

`src/skylize/app/decision_engine/evaluator.py:373-398`. Four paths:

| Condition | Outcome | Line |
|---|---|---|
| `not proposal.involves_spend` | `_PASS` | 377-378 |
| `ceiling is None` | `deferred_to_human`, trigger `SPEND_OVER_CEILING` | 379-386 |
| `committed + spend > ceiling` | `deferred_to_human`, trigger `SPEND_OVER_CEILING` | 387-396 |
| otherwise | `_PASS` | 398 |

`involves_spend` is a property: `spend_minor_units is not None`
(`src/skylize/app/decision_engine/events.py:136-138`). So **`spend_minor_units=None`
means "no spend" and passes silently.** Any wiring that fails to populate this field
produces a check that runs, logs a stage, and enforces nothing — a worse outcome than
today's honest absence, because it looks closed.

Note also that `capital_check` **has no `hard_deny` outcome.** Both failure paths defer
to a human. `DecisionOutcome` admits `"rejected"` (events.py:53) but `capital_check`
never returns it.

### 1.5 What `capital_check` currently sees

`KNOWN_ACTION_KINDS` (`src/skylize/app/decision_engine/events.py:44-51`) has four
members, but only three can reach stage 4, because `agent.execute` short-circuits at
2.5 (§1.3):

| `action_kind` | Source event | Mapped at |
|---|---|---|
| `creative.review` | `CreativeReviewRequested` | events.py:144-166 |
| `sales.campaign` | `SalesCampaignProposed` | events.py:167+ |
| `sales.budget_reallocation` | `SalesBudgetReallocationProposed` | events.py, same block |
| `agent.execute` | *none — synchronous, no event* | events.py:35-40 |

All three spend-bearing kinds arrive as **async bus events** through
`DecisionProposal.from_event` (events.py:141-143). None of them is a tool call.

**Confirmed: the §1.1 gap is exactly as characterised.** A tool call is dispatched at
`proxy.py:192` with no monetary ceiling consulted on any path.

### 1.6 What is *not* broken (must not regress)

- **Token pipeline** — signature/expiry/revocation/scope/budget/delegation, invoked at
  proxy.py:114-122, denial at 123-131.
- **`max_calls_per_run`** — proxy.py:136-150, raises `ToolCallLimitExceeded`.
- **Convergence breaker** — proxy.py:152-176, raises `ToolConvergenceDenied`.
- **Input-schema validation** — proxy.py:177-185, raises `ToolInputError`.
- **Per-call audit** — proxy.py:198-202 and `_audit_call`, proxy.py:205-228.

The design below inserts one new step and reorders nothing.

### 1.7 A second proxy exists — out of scope, but note it

`src/skylize/runtime/tool_proxy.py` defines a *different* `ToolProxy` (plus
`RegistryToolProxy`), used by `src/skylize/runtime/agent_runner.py:256` via
`dispatch_llm`. Its budget handling is also token-only (`runtime/tool_proxy.py:219-228`).
The §1.1 brief scopes to `src/skylize/tools/proxy.py`, and this document follows that
scope — but **the same monetary gap exists on that second path**, and closing one does
not close the other. Flagged, not designed here.

---

## 2. The gap, stated precisely

> An agent holding a valid governance token, within scope, under its call limit, and
> not looping, may invoke a spend-bearing tool for an unbounded amount. The token
> pipeline bounds *LLM tokens*. `max_calls_per_run` bounds *call count*. The
> convergence breaker bounds *repetition*. **Nothing bounds currency.**

The gap is **monetary only**. It is not a total governance gap, and the design must not
pretend otherwise.

It is also **latent, not live**: the current registry
(`src/skylize/tools/builtin/__init__.py:22-33`) holds `memory.recall`,
`current_datetime`, `search.web`, and two HubSpot tools. None moves money. The gap
becomes exploitable the moment the first spend-bearing connector is registered — which
`docs/06_integrations/integration_inputs.md` (committed `171fa1c`, itself
DRAFT/unapproved) exists to gate. **The correct sequencing is: close this before that
lands, not after.**

---

## 3. Constraints that shape the fix

1. **Tool calls are synchronous and in-request.** They cannot become a fourth async
   business event type. (Hard exit gate — see §10.)
2. **Most tool calls do not spend.** `memory.recall` and `current_datetime` must not
   pay a DB round-trip per call.
3. **`app/decision_engine` may be imported from the request path**; `skylize.decision_engine`
   (the OPA package) may **not** (CLAUDE.md; owner decision K3, `src/skylize/dal/hitl.py:1-21`).
4. **Money is integer minor units.** Never float, never Decimal across a process
   boundary (`src/skylize/app/principal/models.py:181-183`).
5. **The three ledgers stay distinct** (ADR-0006). This design touches the *business
   spend* ceiling, not `run_ledger` and not `ai_cost_ledger`.

---

## 4. FINDING: `capital_check` alone does not close the gap

This is the most important result of the audit and it changes the shape of the
recommendation.

`capital_check` is a **read-then-compare**: `get_ceiling` at evaluator.py:145, then
`projected > ceiling.ceiling_minor_units` at evaluator.py:387. The repository port
exposes reads only — `CapitalRepository` has exactly one method, `get_ceiling`
(`src/skylize/dal/ports.py:373-377`). There is no reserve, no conditional write, no lock.

The codebase already contains an explicit, unambiguous verdict on that pattern.
`src/skylize/app/principal/models.py:185-188`:

> *"NOTE: this is the read model. The ceiling is NOT enforced by reading this and
> comparing — see `spend.SpendLedger`. A read-then-check is not a ceiling under
> concurrency; it is a race."*

And `src/skylize/app/principal/spend.py:6-18`:

> *"A budget ceiling cannot be enforced by a claim inside a signed token. A token is a
> copy; a budget is a shared mutable resource. Two concurrent runs holding the same
> '$500 ceiling' token will each read 'under ceiling' and each spend $500. […] the
> cumulative ceiling is enforced here, by a single conditional UPDATE whose WHERE
> clause IS the policy. If the UPDATE affects zero rows, the action is denied. There is
> no read-then-check window."*

Two agents in one workflow issuing concurrent refunds would both pass `capital_check`
and both dispatch. **Wiring `capital_check` into the tool path and stopping there
produces a gate that is auditable, logged, stage-recorded — and bypassable by
concurrency.** That is the failure mode §1.1 is trying to eliminate.

The enforcing primitive already exists and is unwired:

- `SpendLedger.reserve` / `commit` / `release` — `src/skylize/app/principal/spend.py:110-190`
- Atomic `_RESERVE_SQL`, ceiling in the `WHERE` clause — spend.py:196-222, specifically
  `AND e.spent_minor + e.reserved_minor + $3 <= e.ceiling_minor` (spend.py:214), with
  `FOR UPDATE` at spend.py:207
- `CeilingExceeded(reason, defer_to_human=...)` — `src/skylize/app/principal/errors.py:107-112`
- `over_ceiling_behavior` in `('hard_deny','defer_to_human')` — DDL at
  `migrations/versions/0019_principal_authority.py:125-126`; read model at
  `src/skylize/app/principal/models.py:200`; the branch that consumes it at
  `src/skylize/app/principal/spend.py:158`
- Tables `spend_envelope` / `spend_reservation` — migrated at 0019
  (`migrations/versions/0019_principal_authority.py:115-132`)

`grep` for `SpendLedger` outside its own module returns only the package re-export
(`src/skylize/app/principal/__init__.py:48`). **It is constructed nowhere in
`bootstrap.py`.** The enforcement machinery is built and dormant.

### 4.1 Consequence for the design

The two mechanisms have different jobs and both are needed:

| | `capital_check` (evaluator) | `SpendLedger.reserve` |
|---|---|---|
| Question | *Is this class of action permitted against the org/department budget?* | *Does this specific amount fit this principal's envelope, right now?* |
| Scope | org + `capital_scope` | org + `principal_id` |
| Store | `budget_ledger` via `CapitalRepository` | `spend_envelope` via `SpendRepository` |
| Concurrency | racy (read-then-compare) | atomic (conditional UPDATE) |
| Outcomes | `deferred_to_human` only | `hard_deny` **or** `defer_to_human` |
| Role | **policy** | **enforcement** |

The brief asks for `capital_check` wiring. This document designs it — and states
plainly that **the `hard_deny` / T4 behaviour the brief asks to surface does not exist
in `capital_check` at all** (§1.4); it exists only in the principal envelope path.
Delivering the requested `capital_check` wiring alone would not produce the requested
`hard_deny` semantics. §6 therefore designs both layers, marks which is which, and §11
puts the sequencing decision to the owner.

---

## 5. Rejected alternatives

**A. New async event type `tool.spend_requested`.** Rejected on the brief's own hard
gate, and independently wrong: a tool call is in-request and its caller is blocked on
the result. Round-tripping through the bus would either block a request on async
consumption or dispatch before the decision returns. This is the architecture conflict
the gate names; it is not designed around here.

**B. Let `tool.invoke` fall through stages 3-6.** Rejected. Stage 5 calls
`self._remember(proposal, contract)` (evaluator.py:162), which inserts into the
partition conflict window. In-request tool calls would pollute the async engine's
conflict window and could cause an unrelated async proposal to lose a conflict to a
tool call. Stage 6's HITL check and the generic default-approve at evaluator.py:173-184
are also shaped for async proposals.

**C. Enforce inside each tool handler.** Rejected. It is the IF-TOOL chokepoint's job
(`src/skylize/tools/proxy.py:1-17`); per-handler enforcement is unauditable in aggregate
and fails open on every new connector by default.

**D. Reuse the token `BUDGET` stage by setting `requested_token_cost` to a currency
amount.** Rejected — it conflates the token ceiling with the money ceiling, exactly the
ADR-0006 confusion. Lines 119-120 stay as they are.

---

## 6. Design

### 6.1 Change A — a tool declares its cost model (opt-in)

**File:** `src/skylize/tools/base.py`

Add alongside `ToolDefinition` (base.py:39-49):

```python
CostEstimator = Callable[[BaseModel], int]
"""Validated tool input -> spend in integer MINOR units. Never float."""


@dataclass(frozen=True, slots=True)
class ToolCostModel:
    """Declares that a tool moves real money, and how much for a given input.

    ABSENT (the default) means the tool is not spend-bearing and the proxy skips
    the capital path entirely -- zero added latency for memory.recall,
    current_datetime, search.web and every other non-spending tool.

    PRESENT is a load-bearing declaration: the proxy WILL consult the ceiling
    before dispatch, and a tool whose estimator raises is DENIED, not dispatched.
    """

    capital_scope: str          # ledger scope, e.g. "payments"
    currency: str               # ISO-4217, len 3, matches spend_envelope.currency
    estimate: CostEstimator
```

and one optional field on `ToolDefinition`:

```python
    cost_model: ToolCostModel | None = None
```

**Why a callable rather than a static amount:** the amount is per-call and lives in the
input (a refund's `amount_minor`), so it cannot be a registration-time constant.

**Why opt-in rather than opt-out:** it is the only way to meet constraint §3.2. The cost
is that a new spend-bearing tool registered *without* a `cost_model` is silently
ungoverned — a fail-open default. §6.5 closes that with a registry-side assertion.

**`ToolDefinition` already sets `extra="forbid"`** (base.py:40), so this is a strictly
additive optional field; every existing construction site stays valid unchanged.

### 6.2 Change B — the proxy gains a capital collaborator and one insertion point

**File:** `src/skylize/tools/proxy.py`

One new optional constructor parameter (proxy.py:73-86):

```python
        capital: ToolCapitalGate | None = None,
```

where `ToolCapitalGate` is a small port defined in `tools/` (not imported from
`app.decision_engine` directly, so `tools` does not take a dependency on the evaluator's
module graph — the concrete adapter is assembled in `bootstrap.py`):

```python
class ToolCapitalGate(Protocol):
    async def authorize(
        self, *, org_id: str, principal_id: str | None, tool_id: str,
        cost_model: ToolCostModel, amount_minor: int,
        contract: AgentContract, correlation_id: UUID,
        governance_token_id: UUID,
    ) -> CapitalAuthorization: ...
```

`capital=None` preserves today's behaviour exactly for every caller that does not pass
it — but see §6.5: `None` combined with a registered spend-bearing tool must fail closed
at startup, not at call time.

**Insertion point — `proxy.py`, between line 185 and line 187:**

```
104   registry.resolve                       (unchanged)
114   validate_tool_call                     (unchanged -- lines 119-120 UNTOUCHED)
136   max_calls_per_run                      (unchanged)
152   convergence record_action              (unchanged)
177   input_schema.model_validate            (unchanged)
>>>   NEW: capital gate  (only if tool.cost_model is not None)
187   handler dispatch                       (unchanged)
198   audit success                          (unchanged)
```

**Why exactly there, and nowhere else:**

- **After the token pipeline (114)** — non-negotiable. An unauthenticated or
  out-of-scope caller must never reach the capital store. Placing the gate earlier turns
  an unauthenticated tool call into an unauthenticated database read: a DoS amplifier
  and a ceiling-probing oracle.
- **After input validation (177)** — required, not merely preferred. The estimator reads
  a typed field off the validated model. Running it against the raw `input_data: dict`
  would mean estimating spend from unvalidated attacker-controlled input.
- **Immediately before dispatch (187)** — the last point at which no side effect has
  occurred. Any later is post-hoc accounting, not a gate.

**Deliberate, stated consequence:** a capital-denied call has already incremented
`ToolCallCounter` (proxy.py:137) and been recorded by the convergence tracker
(proxy.py:159-166). This is judged correct — the agent *did* attempt the call, and an
agent that repeatedly retries an over-ceiling refund should trip the breaker rather than
retry indefinitely. It is recorded here so it is a decision, not an accident.

### 6.3 Change C — a second synchronous vertical in the evaluator

**File:** `src/skylize/app/decision_engine/events.py`

```python
TOOL_INVOKE_ACTION_KIND = "tool.invoke"
```

added to `KNOWN_ACTION_KINDS` (events.py:44-51). This follows the
`AGENT_EXECUTE_ACTION_KIND` precedent verbatim (events.py:35-40): *"a first-class
KNOWN_ACTION_KINDS member … This is an authorized enum extension, not an invented
value."* It adds **no** event class, **no** `from_event` branch (events.py:141-143 is
untouched), and **no** bus topic. Like `agent.execute`, it is a synchronous vertical
with no wire event.

**This is an enum extension and requires the same owner ratification `agent.execute`
received. It is proposed here, not assumed** (§11, D1).

**File:** `src/skylize/app/decision_engine/evaluator.py`

A second vertical immediately after the existing one at evaluator.py:139-140:

```python
        if proposal.action_kind == TOOL_INVOKE_ACTION_KIND:
            return await self._decide_tool_invocation(proposal, contract, stages)
```

`_decide_tool_invocation` is `async` (unlike `_decide_agent_execution`, which is sync)
because `get_ceiling` is awaited. It runs, terminally:

1. `ceiling = await self._capital.get_ceiling(proposal.org_id, proposal.capital_scope)`
   — reusing evaluator.py:144-148's exact form.
2. `self.capital_check(proposal, ceiling)` — **calling evaluator.py:373-398 unmodified.**
   This is the deliverable the brief names.
3. On `_PASS`, return `approved` **explicitly**, mirroring the K2 rationale at
   evaluator.py:220-223 — never fall through to the generic default-approve.

It deliberately does **not** call `conflict_detection` or `_remember` (rationale in
§5.B), and does not call `hitl_check`, whose triggers are shaped for async proposals.

`capital_check` itself is **not modified**. `score()` may be called for the audit record
but is never terminal (evaluator.py:142-149).

### 6.4 Change D — the enforcing layer, and how denial surfaces

The adapter behind `ToolCapitalGate` — proposed as
`app/decision_engine/tool_capital.py`, assembled in `bootstrap.py` — runs **policy then
enforcement**:

```
1. POLICY     build DecisionProposal(action_kind="tool.invoke",
                                     spend_minor_units=amount_minor,   <-- §1.4: must be set
                                     capital_scope=cost_model.capital_scope,
                                     currency=cost_model.currency, ...)
              -> evaluator.decide(...)  -> capital_check
              deferred_to_human  -> write HITL, DENY this call
              approved           -> continue to 2

2. ENFORCE    SpendLedger.reserve(org_id, principal_id, amount_minor,
                                  idempotency_key, correlation_id,
                                  governance_token_id)
              CeilingExceeded(defer_to_human=False) -> hard_deny
              CeilingExceeded(defer_to_human=True)  -> write HITL, DENY
              EnvelopeNotFound                      -> DENY (fail closed)
              Reservation                           -> ALLOW, hold the reservation_id

3. SETTLE     handler succeeded -> SpendLedger.commit(reservation_id, actual_minor)
              handler raised    -> SpendLedger.release(reservation_id)
```

Step 3 is why the reserve/commit lifecycle matters and a bare check does not: a Stripe
call that times out ambiguously must not silently consume budget, and one that returns a
different settled amount must record the actual. `commit` already clamps with
`LEAST($3, amount_minor)` (spend.py:334) and is idempotent when the hold is already
settled (spend.py:345). Abandoned holds are swept by `sweep_expired` (spend.py:401),
which **must be scheduled** — spend.py's own docstring says "Run from Temporal on a
schedule, not from a request path" (spend.py:402). Nothing schedules it today (§11, D4).

**`principal_id` is already available on the tool path.** `ToolProxy.invoke` receives
`governance_token`, and `GovernanceToken.on_behalf_of.principal_id` exists on v1.1
tokens (`src/skylize/contracts/base.py:239` and `:193`). **No token-format change and no
signature change is required.** `contracts/base.py:174-176` already states the
expectation directly: *"the tool proxy must additionally assert that the token's scope is
still within that human's authority."*

For a **v1.0 autonomous token** (`on_behalf_of is None`, base.py:239) there is no
principal and therefore no envelope. `SpendLedger.reserve` would raise
`EnvelopeNotFound`, which is a denial by design (spend.py:31-32: *"a missing envelope …
Absence of budget is never unlimited budget"*). **An autonomous agent therefore cannot
spend money at all under this design.** That is a real, deliberate behavioural
consequence and needs an owner decision (§11, D2) — the alternative is an org-level
fallback envelope, which reintroduces an unattributed spend path.

#### Surfacing to the caller

Three new exception types in `src/skylize/tools/base.py`, subclassing
`ToolPermissionDenied` exactly as `ToolConvergenceDenied` (base.py:74-88) and
`ToolCallLimitExceeded` (base.py:91-101) already do:

```python
class ToolCapitalDenied(ToolPermissionDenied):
    """Spend exceeds the ceiling and the envelope's over_ceiling_behavior is
    'hard_deny'. Terminal: no ticket, no retry path."""
    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="capital")


class ToolCapitalDeferred(ToolPermissionDenied):
    """Spend exceeds the ceiling and behaviour is 'defer_to_human', OR no
    ceiling is configured (evaluator.py:379-386 fails closed). A HITL ticket
    HAS been written; this call is denied and does NOT execute on approval
    unless a replay envelope was persisted."""
    def __init__(self, reason: str, *, hitl_id: UUID | None) -> None:
        super().__init__(reason, failed_stage="capital")
        self.hitl_id = hitl_id


class ToolCapitalUnavailable(ToolPermissionDenied):
    """The ceiling could not be established at all. Fails closed. Its own type,
    mirroring AuthorityUnavailable (principal/errors.py:47-56): 'we could not
    check' must never collapse into 'there was nothing to find'."""
    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="capital")
```

`failed_stage="capital"` is a plain string, consistent with the existing non-enum stages
`"convergence"` and `"call_limit"`. `ValidationStage`
(`src/skylize/contracts/token.py:185-193`) is **not** extended — the same reasoning
already recorded at `src/skylize/app/principal/errors.py:8-18`.

**No caller change is required.** All three subclass `ToolError`, which
`AgentExecutionService._invoke_tool` already catches at
`src/skylize/app/agents/execution.py:942-947`, returning a `tool_result` block with
`is_error=True`. The LLM sees the denial in-band and can adapt — proposing a smaller
refund or telling the user it needs approval — rather than the run crashing. Denials are
audited through the existing `_audit_call(result="denied", ...)` path (proxy.py:205-228),
so they land in `audit_log` beside every other denial with no new audit vocabulary.

**Honest limitation of `defer_to_human` on this path.** A synchronous tool call cannot
block for a human. The semantics are therefore: *deny now, ticket enqueued, refund does
not happen in this turn.* Making approval actually execute the refund later requires a
`HitlReplayEnvelope` for the tool call (`request_json`, `src/skylize/dal/ports.py:431`),
which today carries agent-execution replays only. That is deferred and flagged (§11, D3)
rather than half-built.

### 6.5 Change E — close the fail-open default

§6.1's opt-in design means a spend-bearing tool registered without a `cost_model` is
ungoverned. Two startup assertions, in `ToolRegistry.validate_schemas` or a sibling —
`validate_schemas` is already called at construction
(`src/skylize/tools/builtin/__init__.py:42`), so the hook exists:

1. Any tool with a `cost_model` declared, while the proxy has `capital=None`, is a
   **startup failure**. Fail closed at boot, in the style of `bootstrap.py`'s
   `LLMConfigurationError` (bootstrap.py:515-520) — never a silent degrade.
2. A registry-level manifest of `tool_id -> spend_bearing: bool` that every registered
   tool must appear in, so adding a connector forces an explicit yes/no rather than
   defaulting to no.

Assertion 2 is the one that actually prevents the next connector from arriving
ungoverned. It is proposed, not designed in detail here.

---

## 7. Ordering summary

```
ToolProxy.invoke
  1. resolve tool                                   proxy.py:104      unchanged
  2. validate_tool_call  (LLM-token ceiling)        proxy.py:114-122  unchanged
  3. max_calls_per_run                              proxy.py:136-150  unchanged
  4. convergence breaker                            proxy.py:152-176  unchanged
  5. input_schema.model_validate                    proxy.py:177-185  unchanged
  6. if tool.cost_model is None:  -> straight to 7                    NEW, no-op path
     else:
       6a. amount = cost_model.estimate(validated)                    raises -> DENY
       6b. evaluator.decide(tool.invoke proposal)   evaluator.py:373  POLICY
       6c. SpendLedger.reserve(...)                 spend.py:214      ENFORCE (atomic)
  7. handler dispatch                               proxy.py:187-195  unchanged
  8. settle: commit(actual) | release()             spend.py:~300     NEW
  9. audit                                          proxy.py:198-202  unchanged
```

The money ceiling sits between the token ceiling and dispatch. The token pipeline,
`max_calls_per_run`, and the convergence breaker are untouched in position and behaviour
— constraint met.

---

## 8. Before / after trace

**Scenario.** Org `acme`, principal `emma@acme` (T4 director), monthly envelope ceiling
**$5,000.00** = `500000` minor units, `spent_minor=487000`, `reserved_minor=0`,
`over_ceiling_behavior='hard_deny'`. Available: `13000` ($130.00). A support agent
decides to refund **$400.00** (`40000` minor units) via an `integration.stripe_refund`
tool.

**Note:** no Stripe tool is registered today (`src/skylize/tools/builtin/__init__.py:22-33`
— memory, datetime, web search, two HubSpot). This trace is concrete but prospective; it
is the case the design must handle when `integration_inputs.md`'s connectors land.

### Before (today's code)

```
1  resolve "integration.stripe_refund"                 proxy.py:104   OK
2  validate_tool_call(requested_token_cost=0)          proxy.py:114   OK
                                                       (signature/expiry/revocation/
                                                        scope/budget/delegation all pass;
                                                        BUDGET compares 0 <= max_token_budget)
3  max_calls_per_run: 1 <= 3                           proxy.py:136   OK
4  convergence: first call, no repeat                  proxy.py:152   OK
5  input validated: {amount_minor: 40000}              proxy.py:177   OK
6  handler dispatch -> Stripe API                      proxy.py:192   $400.00 LEAVES
7  audit "tool.invoked" result="success"               proxy.py:198   recorded after the fact
```

**Outcome: the refund executes.** The envelope had $130.00 available. The org is $270.00
over ceiling. The audit trail records it accurately — as history. Every governance
control fired and every one of them passed, because none of them was looking at money.

### After (this design)

```
1-5  identical, unchanged                                             OK

6    cost_model is not None -> capital path engages
6a   estimate(validated) -> 40000 minor, USD, scope="payments"
6b   POLICY: DecisionProposal(action_kind="tool.invoke",
                              spend_minor_units=40000,
                              capital_scope="payments", currency="USD")
       -> evaluator: authority OK, policy OK ("tool.invoke" in KNOWN_ACTION_KINDS)
       -> _decide_tool_invocation
       -> get_ceiling("acme", "payments")           evaluator.py:145
       -> capital_check                             evaluator.py:373
          [dept budget_ledger has room]             -> _PASS  (evaluator.py:398)
6c   ENFORCE: SpendLedger.reserve(org="acme", principal="emma@acme",
                                  amount_minor=40000, idempotency_key=...)
       -> _RESERVE_SQL                              spend.py:214
          WHERE 487000 + 0 + 40000 <= 500000  ->  527000 <= 500000  ->  FALSE
       -> 0 rows. No reservation row inserted.
       -> re-read ONLY for a reason string          spend.py:150-153
       -> raise CeilingExceeded(available=13000,
                                defer_to_human=False)   spend.py:158
                                                     (over_ceiling_behavior='hard_deny')

7    DISPATCH NEVER REACHED.  $0.00 leaves.

     raise ToolCapitalDenied(
       "reservation of 40000 would exceed envelope <id>: available=13000 USD")
     audit: result="denied", reason="capital: ..."   proxy.py:205
     -> execution.py:942 catches ToolError
     -> tool_result block, is_error=True
     -> the LLM is told the refund was denied and why, and can propose $130.00
        or escalate to Emma.
```

**Change `over_ceiling_behavior` to `'defer_to_human'`** and step 6c instead raises
`CeilingExceeded(defer_to_human=True)`; the gate writes a HITL ticket via
`HitlQueueRepository` and raises `ToolCapitalDeferred(hitl_id=...)`. The refund still
does not execute this turn (§6.4, honest limitation).

**Concurrency check.** Two agents refund $100.00 simultaneously with $130.00 available.
Both pass step 6b (`capital_check` reads the same pre-state — the race in §4). At 6c the
two `_RESERVE_SQL` statements serialize on `FOR UPDATE` (spend.py:207): the first bumps
`reserved_minor` to 10000, the second evaluates `487000 + 10000 + 10000 <= 500000` ->
`507000 <= 500000` -> FALSE and is denied. **Total spend $100.00, not $200.00.** This is
precisely what step 6b alone cannot do, and why §4 argues the enforcement layer is not
optional.

---

## 9. Test obligations

Not written this pass; recorded so implementation is not declared done without them.

1. `cost_model is None` -> capital path never engages; zero DB round-trips. Assert on a
   spy port, for `memory.recall` and `current_datetime`.
2. Over-ceiling + `hard_deny` -> `ToolCapitalDenied`, **handler never called** (spy
   handler asserts zero invocations).
3. Over-ceiling + `defer_to_human` -> `ToolCapitalDeferred`, HITL row written, handler
   never called.
4. No ceiling configured -> denial, not pass (evaluator.py:379-386 fail-closed).
5. v1.0 autonomous token + `cost_model` -> denial via `EnvelopeNotFound` (§11, D2 may
   change this).
6. Estimator raises -> denial, handler never called.
7. Handler raises after a successful reserve -> `release` called, `reserved_minor`
   returns to its prior value.
8. Handler succeeds -> `commit(actual_minor)` called; `actual > amount` is clamped
   (spend.py:334 `LEAST`).
9. **Concurrency, Postgres-backed:** two concurrent reserves against $130.00 available;
   exactly one succeeds. This test is meaningless as a unit test with an in-memory fake —
   it MUST run against real Postgres.
10. Regression: token pipeline, `max_calls_per_run`, convergence, and input validation
    behave identically with `capital=None` and with a gate wired but no `cost_model`.

**Per CLAUDE.md TESTING:** tests 3, 5, 7, 8, 9 are Postgres-backed and **skip silently**
without `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_APP_DB_URL`. Before any claim that this gap
is closed, confirm they **ran**, not skipped — and that `SKYLIZE_TEST_APP_DB_URL` is the
non-superuser `skylize_app` role (`tests/integration/conftest.py:31`, `:173`), or the RLS
scoping on `spend_envelope` proves nothing.

> Note: CLAUDE.md cites this assertion at `tests/integration/conftest.py:26`; at commit
> `5e11959` the actual lines are 31 and 173. The CLAUDE.md citation is stale.

---

## 10. Hard exit gate assessment

**Gate 1 — "STOP if this requires breaking the stateless invariant on any Safety Suite
agent (`memory_read_access=[]`, `memory_write_access=[]`)."**

**NOT BREACHED. No stop required.**

The Safety Suite contracts declare empty memory access at
`src/skylize/contracts/definitions/security.py:38-39`, described at security.py:16 as
*"All are stateless: memory_read_access and memory_write_access minimal."* Those two
lists are consumed in exactly two places: prompt assembly
(`src/skylize/app/agent_prompts/service.py:51`) and the `memory.recall` tool's namespace
filter (`src/skylize/tools/builtin/memory_recall.py:78-79, 97`). They govern **agent
memory namespaces**.

This design reads `spend_envelope` and `budget_ledger` through DAL ports keyed by
`(org_id, principal_id)` and `(org_id, scope)`. Neither is agent memory; neither touches
a memory namespace; no `memory_read_access` or `memory_write_access` list is read,
widened, or consulted anywhere in §6. A Safety Suite agent invoking a spend-bearing tool
would be capital-checked without gaining any memory access. The invariant holds unchanged.

**Gate 2 — "STOP if the fix requires a new async event type."**

**NOT BREACHED. No stop required.**

The design adds **no** event class, **no** `from_event` branch (events.py:141-143
untouched), **no** bus topic, and **no** subscription. It adds one `action_kind` *string*
to `KNOWN_ACTION_KINDS`, following the `AGENT_EXECUTE_ACTION_KIND` precedent
(events.py:35-40) which established that a synchronous, in-request vertical can be a
first-class action kind with no corresponding event. The whole path is synchronous and
in-request, exactly as constraint §3.1 requires.

**Caveat, flagged rather than assumed:** `agent.execute` was added by explicit owner
decision K2. `tool.invoke` is an enum extension of the same kind and should receive the
same explicit ratification. It is **proposed** in §6.3, not treated as pre-approved
(§11, D1).

**Gate 3 — "no application code this pass."** Honoured. `proxy.py`, `evaluator.py`,
`events.py`, and all other production files are unmodified; this document is the only
addition from this pass.

---

## 11. Open decisions for the owner

| # | Decision | Why it cannot be defaulted |
|---|---|---|
| **D1** | Ratify `tool.invoke` as a `KNOWN_ACTION_KINDS` member (events.py:44-51). | `agent.execute` required explicit owner decision K2. Same class of change; same ratification. |
| **D2** | May a **v1.0 autonomous** token (no `on_behalf_of`) spend at all? | As designed: no — `EnvelopeNotFound` denies (§6.4). The alternative, an org-level fallback envelope, creates an unattributed spend path. This is a product decision, not a technical one. |
| **D3** | Should `defer_to_human` on the tool path be **replayable**? | Requires a tool-call `HitlReplayEnvelope`; `request_json` (ports.py:431) carries agent-execution replays only today. Non-replayable is honest but means approval does not perform the refund. |
| **D4** | Who schedules `SpendLedger.sweep_expired`? | spend.py:402 requires a scheduler, and nothing calls it today. Without it a crashed worker permanently consumes budget and it presents as *"my agents stopped working and nobody knows why"* (spend.py:38-40). **This is a prerequisite, not a follow-up.** |
| **D5** | **Is `capital_check` wiring alone acceptable as the §1.1 fix?** | §4: it is a read-then-compare and therefore racy by the codebase's own stated standard, and it has no `hard_deny` outcome (§1.4). Wiring it alone yields an auditable gate that concurrency defeats. **Recommendation: do not ship the policy layer without the enforcement layer** — a gate that looks closed is worse than a documented gap. |
| **D6** | Move `SpendRepository` / `PostgresSpendRepository` into `skylize.dal`? | spend.py:42-52 flags that it currently opens its own asyncpg pool, which is why it can live under `skylize.app` without tripping the import-linter contract forbidding `skylize.app -> skylize.dal.connection`. Wiring it into bootstrap is exactly the "wiring pass" that docstring defers this to. |
| **D7** | Does `runtime/tool_proxy.py` (§1.7) get the same treatment? | It is a second live dispatch path with the same monetary gap. Closing only `tools/proxy.py` leaves it open. |

---

## 12. What this document does not do

- Does not modify any production code.
- Does not design the `hitl_queue` replay envelope for tool calls (D3).
- Does not specify per-connector cost models beyond the Stripe-refund shape in §8.
- Does not cover `runtime/tool_proxy.py` (§1.7, D7).
- Does not address `run_ledger` or `ai_cost_ledger` (ADR-0006 — different ledgers,
  different units, deliberately untouched).
- Does not resolve §4/D5. That is an owner call, and it is the decision that determines
  whether the §1.1 gap actually closes.
