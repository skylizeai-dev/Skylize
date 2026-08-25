# HITL-per-turn and the principal DAL

Design note. No `src/` or `tests/` changes. Written against `0aa4621` on
`feat/cowork-surface`; every claim below was re-verified in the tree at that
commit rather than carried over from a prior session's report. Three claims a
prior report made are corrected inline and flagged **CORRECTION**.

---

## Q1 — HITL per turn: recommendation

### What the code actually does

Stage 2.5 is not the first gate an `agent.execute` proposal meets. `evaluate`
runs safety veto, authority, then policy before reaching it
(`app/decision_engine/evaluator.py:115-140`), and only then short-circuits into
`_decide_agent_execution` (`:139-140`).

**CORRECTION 1.** A prior report described stage 2.5 as the gate that catches a
scope- or budget-exceeding turn. It cannot be, and never was. The proposal it
judges is built by `_build_execution_proposal`
(`app/agents/execution.py:955-980`) with `action_kind=AGENT_EXECUTE_ACTION_KIND`
(`:974`), `requires_external_launch=False` (`:975`), `metadata={}` (`:979`), no
`spend_minor_units`, and no `security_verdict`. Against that proposal:

* stage 0 `safety_veto_check` passes unconditionally — `proposal.security_verdict`
  is `None` and only `reject=True` vetoes (`evaluator.py:270-272`);
* stage 1 `authority_check` requires only `worker` because
  `requires_external_launch` is `False` (`evaluator.py:290-291`), and every
  contract is at least `worker`;
* stage 2 `policy_check` passes because `agent.execute` is in
  `KNOWN_ACTION_KINDS` (`app/decision_engine/events.py:44-51`),
  `involves_spend` is `False` (`events.py:136-138`), and `metadata` carries no
  `brand_safety` key (`evaluator.py:322-323`).

So for this vertical the first three stages are structurally incapable of
firing, and stages 3-6 are never reached at all — `_decide_agent_execution` is
terminal (`evaluator.py:139-140`, and the docstring at `:210-212` says so
explicitly). The entire verdict is the trigger-presence branch:

* `FIRST_EXTERNAL_LAUNCH` present → defer (`evaluator.py:216-229`)
* no triggers → approve (`evaluator.py:230-240`)
* any other trigger present → defer (`evaluator.py:247-260`)

That last branch keys off *presence of a trigger on the contract*, never off
anything the turn attempted. It cannot do otherwise: stage 2.5 runs at step 2.5
of `execute()`, before prompt building (step 3) and before any LLM call
(step 4), so at gate time no tool intent exists yet.

### Who else is affected

**CORRECTION 2.** A prior report implied `cowork_agent` is the first
interactive/multi-turn contract to hit this. It is not. Of 22 registered
contracts, 13 carry non-empty `human_in_loop_triggers`, and three declare
`invocable_tools` (multi-turn capable): `seo_keyword_agent` (2 tools, **no**
triggers → approves), `cfo_agent` (1 tool, 2 triggers → defers), `cowork_agent`
(2 tools, 2 triggers → defers). `cfo_agent`
(`contracts/mvp/finance.py:162`, triggers `spend_over_ceiling` +
`low_confidence_irreversible`) therefore **already** defers every execution in a
governed org today.

The difference is granularity, not novelty. `cfo_agent` is one request producing
one budget summary, so one HITL ticket per request is proportionate.
`cowork_agent` is conversational — `CoworkTurnIn.message` is one message
(`schemas/agents/cowork.py:14-19`) — so the same rule yields one ticket per
*message*. The rule did not become wrong; the unit it is applied to got much
finer.

### The three shapes

**(a) Proposal-level discriminator** (`chat.turn` vs `chat.tool_invocation`).
Not implementable at stage 2.5 as framed. The discriminator would have to
describe what the turn attempts, and at gate time the model has not been called,
so that fact does not exist. Making it exist requires either moving the gate
after the first LLM turn — which breaks the stated D1 property that a
reject/defer means "no LLM call, no deliverable, no ledger row"
(`app/agents/execution.py:255-262`) — or having the caller self-declare intent,
which is unauthenticated self-assertion by the component being gated. Both are
worse than the problem. Rejected on grounds of ordering, not taste.

**(c) Trigger definitions scoped to tool/action combinations.** The most
principled end state, and the one that would let `LOW_CONFIDENCE_IRREVERSIBLE`
mean something precise. It also changes contract semantics for all 13
trigger-bearing contracts, 11 of which are single-shot and behave correctly
today, and requires re-deriving what each trigger means per tool before anything
can ship. Right direction, wrong size for unblocking commits (b) and (c).

**(b) Contract-level declaration.** Minimal, local, reversible.

### Recommendation: (b), as an explicit opt-out that defaults to today's behavior

Add one boolean to `AgentContract` (`contracts/base.py:82`, alongside
`human_in_loop_triggers` at `:136`) —
`defers_on_trigger_presence: bool = True`. Stage 2.5's third branch
(`evaluator.py:247-260`) consults it; `FIRST_EXTERNAL_LAUNCH`
(`:216-229`) is **unchanged and still unconditional**. `cowork_agent` sets it
`False`. Every other contract keeps the default, so all 21 of them evaluate
byte-identically and no existing test changes.

Name it for what it does. It is not `interactive=True`: "interactive" describes
the surface and invites future readers to assume interactive agents are governed
less. This flag says one narrow thing — *for this contract, the mere presence of
a trigger is not itself a request-time verdict* — which is a statement about
**when** a condition is adjudicated, not about **whether** it is.

That framing is accurate because the two triggers `cowork_agent` declares are
already adjudicated elsewhere, at a point that actually has the facts:

* `AUTHORITY_EXCEEDED` is adjudicated at mint by `resolve_effective_scope`,
  which raises `AuthorityExceeded` naming the offending scopes and never
  silently trims (`app/principal/authority.py:97-124`, raise at `:116-123`), and
  again per call at `ValidationStage.SCOPE` (`contracts/token.py:400-410`).
* `LOW_CONFIDENCE_IRREVERSIBLE` is a property of a specific proposed action.
  Stage 2.5 has no action to inspect, and the async path's `hitl_check` matches
  triggers against `proposal.metadata` (`events.py:131-134`) which this vertical
  leaves empty.

---

## Q1 — what still defers under this shape (the governance-preserving proof)

With `defers_on_trigger_presence=False` on `cowork_agent`, stage 2.5 approves an
ordinary turn. Nothing that previously caught a scope- or budget-exceeding
action stops catching it, because stage 2.5 was never what caught them.

**A turn that attempts a tool the human does not hold cannot obtain a token for
it.** `mint` intersects the request against the principal's compiled authority
before signing: `ceiling = contract_tools & snapshot.scopes` and any excess
raises (`app/principal/authority.py:112`, `:116-123`), reached via
`_gate_principal_scope` (`app/governance/authority.py:302-311`, `:361-416`).
An empty intersection refuses rather than issuing a zero-tool token
(`app/agents/execution.py`, `_principal_scope_for`, landed in `0aa4621`).

**A turn that attempts a tool outside the signed token cannot execute it.**
`ValidationStage.SCOPE` fails when `requested_tool_id not in token.scope`, and
independently when `token.scope` is not a subset of the contract's allowed tools
(`contracts/token.py:400-410`). This runs on **every** tool call inside
`ToolProxy.invoke` (`tools/proxy.py:113-120`) — the proxy is the sole IF-TOOL
path (`tools/proxy.py:1-16`) — and again before **every** LLM turn in
`_execute_with_tools`.

**A turn that would exceed the token budget cannot reach a provider.**
`ValidationStage.BUDGET` fails when
`tokens_used_so_far + requested_token_cost > token.max_token_budget`
(`contracts/token.py:412-417`), re-evaluated each turn with the real running
total so the loop stops before the over-budget call egresses, raising
`TokenBudgetExceeded`.

**A turn made after the human's grants changed is refused at its next call.**
For principal-bound (v1.1) tokens the revocation stage additionally checks
authority freshness (`contracts/token.py:372-398`) against the cached
fingerprint (`app/governance/snapshot.py:88-118`), which **fails closed on a
miss** (`:105-111`). A live-state checker that cannot answer at all is refused
rather than trusted (`token.py:388-395`). Grant writes push the invalidation via
`invalidate_principal_authority` (`app/governance/authority.py:418-439`).

**A killed or suspended agent, or a killed tenant/platform, still stops
everything.** `assert_active` at mint (`app/governance/authority.py:293`) and
`GovernanceSnapshot.reason_for` on the revocation stage
(`app/governance/snapshot.py:69-86`), where kill-switch state outranks all
authority.

**The window stays bounded.** `ValidationStage.EXPIRY` (`token.py:358-365`)
against a co-work TTL of 5 minutes (`app/cowork/session.py:49`).

So the ordered pipeline — signature, expiry, revocation, scope, budget,
delegation (`contracts/token.py:344-349`) — is untouched, and it is the pipeline,
not stage 2.5, that holds the property this branch exists to prove.

### What is genuinely given up, stated plainly

`LOW_CONFIDENCE_IRREVERSIBLE` loses its request-time backstop for
`cowork_agent`. Today that costs nothing real, because irreversibility is a
property of tools and this contract's manifest is `llm.generate` +
`memory.search` (`contracts/mvp/cowork.py:54-61`) — one generative, one
read-only. It stops costing nothing the moment a side-effecting tool joins that
manifest.

**Therefore the opt-out must be re-examined whenever `cowork_agent.allowed_tools`
grows.** That is a guard rail worth encoding rather than remembering: a contract
test asserting `defers_on_trigger_presence is False` implies a manifest drawn
only from a declared reversible/read-only set would fail loudly the day someone
adds `stripe.refund`. Recommended as part of PROMPT 8, listed below.

---

## Q2 — Principal DAL: recommendation

### The port already exists — do not add one to `dal/ports.py`

`PrincipalRepository` is already declared, with exactly the two methods needed,
at `app/principal/provider.py:23-36`, and `AuthorityProvider` — what
`GovernanceAuthority.mint` actually depends on — at `:40-45`.

This repo runs two port conventions side by side, and the principal bounded
context consistently uses the second one:

* `dal/ports.py` holds Protocols for the older/bulk repositories —
  `GovernanceRepository:73`, `DeliverableRepository:304`,
  `HitlQueueRepository:454`, `WorkflowRepository:544`, `CapitalRepository:373`.
* The principal context declares its own ports in the app layer:
  `journal.py:37 JournalRepository`, `provider.py:23 PrincipalRepository`,
  `provider.py:40 AuthorityProvider`, `spend.py:67 SpendRepository`.

The decisive precedent is its own sibling: `work_journal` and `principal` landed
in the *same* migration (0019), and `dal/work_journal.py` implements the
app-layer port `app/principal/journal.py:37`, saying so in its first line
(`dal/work_journal.py:1`). Matching that is the instruction; inventing a
`dal/ports.py` entry would split one bounded context across two conventions.

**Recommendation: add `src/skylize/dal/principal.py` with
`PostgresPrincipalRepository` implementing the existing
`app/principal/provider.py:23` Protocol. No Protocol changes anywhere.**

### RLS scoping — the same `tenant_session` pattern, not a new one

`dal/work_journal.py` opens exactly one `async with self._db.tenant_session(org_id)`
per method (`:55`, `:88`, `:106`, `:117`, `:136`), holds no connection or
transaction state, and mirrors `PgDeliverableRepository` (`dal/work_journal.py:1-6`).
`tenant_session` (`dal/connection.py:71`) issues
`SELECT set_config('skylize.org_id', $1, true)` (`:79`) — `SET LOCAL`, scoped to
the transaction (`:74`).

That is what the 0019 policies read: `tenant_isolation` on both `principal` and
`principal_grant`, `FOR ALL`, `USING`/`WITH CHECK`
`org_id = current_setting('skylize.org_id', true)`, with `ENABLE` **and**
`FORCE` row level security
(`migrations/versions/0019_principal_authority.py:237-256`). `skylize_app` is
granted SELECT/INSERT/UPDATE/DELETE on both (`:263-267`) — neither is
append-only, unlike `work_journal`.

Both methods take `org_id` explicitly, so no unscoped read is expressible
(`app/principal/provider.py:25-28`).

### Column-to-model mapping is not a passthrough

The tables carry columns the models do not, so PROMPT 8 must map fields
explicitly the way `dal/work_journal.py:23-47` does, not `model_validate` a raw
record:

* `principal` (`0019:71-84`) has `created_at`; `Principal`
  (`app/principal/models.py:120-140`) does not. PK is `(org_id, principal_id)`.
  `authority_level` is CHECK-constrained to the same five values as the
  `AuthorityLevel` Literal (`models.py:60`) — they agree today and a test should
  keep them agreeing.
* `principal_grant` (`0019:86-104`) has `grant_id`, `created_by`, `created_at`;
  `Grant` (`models.py:82-96`) has none of them. The DB CHECK requiring a
  justification for `explicit_grant`/`explicit_deny` (`0019:101-102`) duplicates
  the model validator (`models.py:98-106`) — deliberate belt-and-braces, worth a
  test asserting both fire.
* `principal_grant_lookup (org_id, principal_id, valid_from DESC)` (`0019:106-109`)
  already covers `load_grants`; no new index is needed.

Effective-dating is **not** a SQL concern: `load_grants` returns every grant for
the principal and `compile_authority` filters by `is_active_at(at)`
(`app/principal/authority.py:77`, `models.py:114-117`). Pushing a
`valid_from <= now < valid_to` predicate into SQL would move policy out of the
pure, exhaustively testable kernel the module exists to protect
(`app/principal/authority.py:1-17`). Recommend `load_grants` stays a plain
per-principal read.

### Where resolution happens: once per mint, not per turn

`_gate_principal_scope` already calls `snapshot_for` on every mint
(`app/governance/authority.py:385-387`), and `mint` happens once per `execute()`
run. Per-turn re-resolution would add a database round trip to the hot path for
no gain, because per-turn enforcement does not need the database at all: it
compares the token's `authority_fingerprint` against the cached one
(`app/governance/snapshot.py:88-118`), which is a string compare, and the
docstring at `contracts/token.py:372-385` states this is deliberately a
revocation-class check on the synchronous path that "cannot reach the database".

**Recommendation: resolve once per mint/refresh. Do not add a per-turn read.**

### Cache reuse: reuse the existing mechanism, no new namespace

**CORRECTION 3.** There is no Redis fingerprint cache to reuse. The authority
fingerprint cache is an **in-process dict** keyed `(org_id, principal_id)` —
`GovernanceSnapshot._authority` (`app/governance/snapshot.py:27`), populated at
mint (`app/governance/authority.py:409-411`) and dropped on grant change
(`:432`). Redis appears only in the *invalidation broadcast* adapter, which is a
publish/subscribe port with a Redis production implementation and an in-process
one (`app/governance/broadcast.py:15-17`, Protocol at `:85-98`), carrying an
`AUTHORITY` invalidation kind that already names the principal
(`broadcast.py:34`, `:48`).

So the mechanism is: in-process cache + push invalidation, and it already covers
principals end to end. Principal-authority resolution should reuse it **directly
and add no key namespace of its own** — a second cache for the same fact would
create two sources of truth, and the existing one's fail-closed-on-miss
semantics (`snapshot.py:105-111`) are the property that makes the whole scheme
safe. The correct integration is simply that the new repository feeds
`PrincipalAuthorityService`, which feeds `mint`, which already warms the cache.

---

## Q2 — interface sketch (signatures only)

No bodies. The Protocol below **already exists** and is reproduced verbatim from
`app/principal/provider.py:23-36` to show that nothing needs adding to it:

```python
@runtime_checkable
class PrincipalRepository(Protocol):
    async def load_principal(
        self, *, org_id: str, principal_id: str
    ) -> Principal | None: ...

    async def load_grants(
        self, *, org_id: str, principal_id: str
    ) -> Sequence[Grant]: ...
```

The one new type, shaped after `PostgresJournalRepository`
(`dal/work_journal.py:50-53`):

```python
class PostgresPrincipalRepository:
    def __init__(self, db: Database) -> None: ...

    async def load_principal(
        self, *, org_id: str, principal_id: str
    ) -> Principal | None: ...

    async def load_grants(
        self, *, org_id: str, principal_id: str
    ) -> Sequence[Grant]: ...
```

Plus two module-level row mappers mirroring `dal/work_journal.py:23-47`:

```python
def _principal(rec: Any) -> Principal: ...
def _grant(rec: Any) -> Grant: ...
```

The contract field, on `AgentContract` (`contracts/base.py`):

```python
defers_on_trigger_presence: bool = True
```

---

## Dependency between Q1 and Q2

The dependency runs one way, Q1 → Q2, and it is load-bearing:

* **Under the recommended Q1 shape (b),** per-turn governance is enforced from
  the already-signed token — `ValidationStage.SCOPE` and `BUDGET`
  (`contracts/token.py:400-417`) — plus an in-memory fingerprint compare
  (`app/governance/snapshot.py:88-118`). None of that touches Postgres.
  Therefore principal authority needs resolving **once per mint**, and the
  simple read-only repository sketched above is sufficient.

* **Had Q1 chosen (a),** a per-turn discriminator would have made each turn its
  own governance decision requiring current authority, forcing either a
  per-turn database read or a TTL'd cache with its own staleness semantics —
  a materially larger DAL and a new correctness surface.

So Q2's "resolve once per mint, reuse the existing cache, add no namespace" is
**conditional on Q1 landing as (b)**. If a future change reintroduces per-turn
authority evaluation, this Q2 recommendation must be revisited, not assumed.

---

## What PROMPT 8 must implement

**Files to create**

1. `src/skylize/dal/principal.py` — `PostgresPrincipalRepository` +
   `_principal` / `_grant` mappers. Mirror `dal/work_journal.py` exactly: one
   `tenant_session(org_id)` per method, no held state, explicit column mapping.
2. `tests/integration/test_principal_dal_pg.py` — real Postgres, app role.
   Must include a module-local `app_db` fixture: `conftest.py` has none, 14
   modules each carry their own, and omitting it is an ERROR not a skip once
   both DB variables are set (this is exactly what `57b9790` fixed for
   `test_work_journal_pg.py`). Copy the dominant form from
   `tests/integration/test_agent_execute_governed_e2e.py:104-113`.
   Cover: RLS cross-tenant isolation as `skylize_app`, effective-dated grant
   filtering happening in `compile_authority` not SQL, and the
   justification CHECK (`0019:101-102`) firing alongside the model validator.
3. `tests/unit/test_cowork_trigger_presence.py` — stage 2.5 approves a
   `cowork_agent` proposal with the flag `False`; every other trigger-bearing
   contract still defers; `FIRST_EXTERNAL_LAUNCH` still defers unconditionally.

**Files to touch**

4. `src/skylize/contracts/base.py:82` — add
   `defers_on_trigger_presence: bool = True` to `AgentContract`, next to
   `human_in_loop_triggers` (`:136`).
5. `src/skylize/app/decision_engine/evaluator.py:247-260` — consult the flag in
   the third branch only. Leave `:216-229` and `:230-240` alone.
6. `src/skylize/contracts/mvp/cowork.py` — set the flag `False`, with a comment
   pointing at this note.
7. `src/skylize/bootstrap.py` — build `PostgresPrincipalRepository` on the
   postgres backend and `InMemoryPrincipalRepository`
   (`app/principal/provider.py:80`) on memory, wrap in
   `PrincipalAuthorityService` (`:48`), and pass it to **both**
   `GovernanceAuthority.build(principal_authority=...)`
   (`app/governance/authority.py:195`) and
   `AgentExecutionService(principal_authority=...)`. Today bootstrap wires
   neither, so `_principal_authority` is `None` in every container.
8. `tests/contract/test_cowork_contract.py` — extend to pin that the flag is
   `False` **and** that `allowed_tools` stays within a declared
   reversible/read-only set, so adding a side-effecting tool fails loudly (see
   "what is genuinely given up" above).

**Constraints carried from the two corrected facts established on 2026-08-03**

* **Two production `execute()` call sites, not four** —
  `edge/routes/agents.py:90` and `app/hitl/service.py:163`. Any threading of
  `on_behalf_of_principal` touches exactly these two; everything else naming
  `agent_execution` is construction (`bootstrap.py:463`, `:473`, `:480`) or
  `list_agents()` (`agents.py:219`). The 29 test call sites across 8 files must
  keep passing unedited.
* **`request_json`, not `proposal_json`** — re-verified this session.
  `approve()` (`app/hitl/service.py:132`) parses `row.request_json` into a
  `HitlReplayEnvelope` (`:149`) and builds the `execute()` call purely from
  `envelope.*` (`:163-174`). `proposal_json` is read only for `department`
  (`:219`), `proposing_agent_id` (`:285`), `proposal_id` (`:383`) and
  `action_kind` (`:393`) — never for replay arguments. Both columns are written
  once at enqueue (`dal/hitl.py:91` and `:121`). So the principal binding for
  commit (c) belongs in `HitlReplayEnvelope` (`schemas/hitl.py:33-39`) as an
  optional field with a default, which keeps stored rows parseable under
  `extra="forbid"` (`:34`).

**Ordering.** Item 4-6 (Q1) is independently shippable and unblocks nothing else;
items 1-2 + 7 (Q2) unblock commit (b), the chat endpoint. Doing Q1 first means
the endpoint's first green test is an ordinary approved turn rather than a
deferred one.

---

## Open questions this note does NOT resolve

1. **Nothing maps `RequestContext.user_id` to `principal.principal_id`.**
   `RequestContext` carries `org_id`, `user_id`, `roles`
   (`schemas/base.py:73-87`) and no principal field; `Principal.principal_id` is
   its own string space (`app/principal/models.py:130`). Whether the chat
   endpoint may pass `ctx.user_id` directly, or needs a lookup, is undecided.
   Passing it directly is only safe if the two are provisioned as the same
   identifier — which nothing currently enforces.

2. **No write path exists for `principal` or `principal_grant`.**
   `principal_grant.created_by` is `NOT NULL` with no default (`0019:97`), so
   some writer must supply it, and there is no admin endpoint, no seeding
   routine, and no repository method that inserts either row. Until that exists,
   the DAL in item 1 can only read rows nobody can create through the product.
   This may be acceptable for a first cut (operator SQL) but should be a
   deliberate choice, not a discovery.

3. **`rehydrate` does not restore authority fingerprints.**
   `GovernanceAuthority.rehydrate` (`app/governance/authority.py:207-219`)
   restores kill scopes, revoked tokens and agent states, but not
   `_authority`. By the fail-closed-on-miss rule
   (`app/governance/snapshot.py:105-111`) every live v1.1 token is therefore
   refused after a process restart until re-minted. With a 5-minute co-work TTL
   (`app/cowork/session.py:49`) that self-heals quickly and is arguably correct,
   but it is an undocumented availability characteristic rather than a decision
   anyone recorded.

4. **Whether `cfo_agent` should also take the opt-out.** It has the same
   structural property (triggers + `invocable_tools`) and defers every execution
   today. Its triggers include `SPEND_OVER_CEILING`, which — unlike
   `cowork_agent`'s two — is a condition stage 2.5 genuinely cannot delegate,
   because the `agent.execute` proposal carries no spend and `capital_check`
   (`evaluator.py:363-388`) is never reached on this vertical. Left open
   deliberately: it needs the spend question answered first, and answering it
   here would be false closure.
