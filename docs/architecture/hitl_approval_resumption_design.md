# HITL approval resumption — making an approval execute the reviewed action

> **Type:** architecture design note for a CORE GOVERNANCE MECHANISM change.
> Not a feature, not connector work. It changes `HitlReplayEnvelope`
> (`schemas/hitl.py:56-63`) and the entry paths of
> `AgentExecutionService.execute` (`app/agents/execution.py:253-262`), which
> every HITL-escalated action in every vertical passes through.
>
> **Pass discipline: DESIGN ONLY.** No `src/`, `tests/`, or `migrations/`
> changes were made in this pass. Implementation requires explicit owner
> sign-off on the decisions listed in section 8 — the same gate the GCP
> kill-switch design (`docs/06_integrations/gcp_wif_killswitch_design.md`) and
> the spend-ledger fix both went through.
>
> **Commit designed against:** `b0abf13`, branch `feat/hitl-approval-resumption`.
> **Date:** 2026-09-09. Every codebase claim below is cited `file:line` and was
> re-verified in the tree at this commit, not carried from a prior session's
> report. Where a prior framing is wrong, it is corrected inline and flagged
> **CORRECTION**.
>
> **Predecessor / blocked on this:**
> `docs/architecture/spend_reservation_replay_semantics.md` section 7 (branch
> `fix/toolproxy-ledger-commit-accounting`, commit `fcde853`). That document
> rejected `tool_use.id` as a replay identity and named "make replay a
> resumption instead of a re-sampling" as its Option 1. This document is that
> option, designed. Section 5 states plainly whether it closes the ledger's
> identity gap.
>
> **Sibling that must be read with this one:**
> `docs/architecture/principal_dal_and_hitl_per_turn.md`. It established, at a
> different commit and for a different purpose, that the synchronous gate runs
> before the model is called and can therefore only observe that a trigger is
> *declared*. That finding is the load-bearing constraint on everything here.

---

## 0. Summary, and the three hard gates this audit trips

The goal set for this pass was: make HITL approval a **resumption** of the
specific reviewed action rather than a **re-sampling** of the agent run, so that
approving a request executes what the human was shown.

The audit found that the premise the goal was framed on does not hold at this
commit, and that the correction makes the work substantially larger than a
schema change. All three of the pass's hard exit gates trip. They are reported
here rather than designed around.

### 0.1 There is no suspended tool call to resume

**CORRECTION.** The framing "HITL approval does not resume a *suspended* tool
call" implies a tool call exists and is parked. It does not. There is no point
anywhere in the request path at which a tool call is suspended pending human
approval.

The only HITL deferral on the live request path is `_govern`
(`app/agents/execution.py:526`), called from `execute` at `:302-307`. It runs at
step 2.5 of `execute`:

| step | line | what happens |
|---|---|---|
| 1 | `:277` | resolve contract |
| 2 | `:281-286` | validate input against the contract's `input_schema` |
| **2.5** | **`:302-307`** | **the decision gate — the ONLY place a request defers to a human** |
| 3 | `:310-311` | build system + user prompt |
| 4 | `:315-316` | tool loop (`_execute_with_tools`) or single-shot `generate()` |

The gate is upstream of prompt building and upstream of every LLM call. At the
moment of deferral no prompt exists, no model has been called, no `tool_use`
block exists, and no conversation exists. `execution.py:292` states the property
this ordering exists to provide: "a reject/defer verdict means no LLM call, no
deliverable, and no ledger row."

So the reviewed thing is not an action. It is a **request** — an
`(agent_id, input)` pair. `edge/routes/hitl.py:49` is accurate about what it
shows ("WHAT WOULD EXECUTE if approved (the replay envelope's input)") and
`routes/hitl.py:204` renders exactly that (`request_input=request.get("input")`).
The approval semantics today are "re-run this agent on this input", and the UI
copy does not over-claim. What it does not tell the reviewer is that the model
will re-decide; that is a real disclosure gap, but it is a gap in the copy, not a
contradiction of it.

**Consequence for the goal.** Making approval execute the reviewed *action*
requires first **creating a suspension point that does not exist** — a second
gate, inside the tool loop, after the model has emitted a `tool_use` block. That
is not a modification of `HitlReplayEnvelope`. It is a new governance seam, and
it costs the D1 property quoted above.

### 0.2 GATE 1 TRIPPED — conversation state is not retained anywhere

Message history exists only as a local variable. `LLMMessage` is constructed at
exactly three places in the entire `src/` tree, all inside `_execute_with_tools`:
`execution.py:859-860` (seed), `:931` (assistant turn), `:941` (tool results).
The list is a local (`messages: list[LLMMessage]`, `:859`) and is discarded when
the function returns or raises. Nothing persists it:

* the adapter is stateless — `generate_with_tools` rebuilds the whole wire
  payload from `request.messages` on every call
  (`adapters/llm/anthropic_adapter.py:841`);
* `CoworkSessionService` holds no history at all — its only methods are `start`,
  `refresh`, `_mint` (`app/cowork/session.py:52-95`), which are token lifecycle;
* no table stores conversation turns; `hitl_queue.request_json` (migration
  `0015:46`) stores the envelope and nothing else.

Resumption of a mid-loop tool call needs the conversation prefix, because the
`tool_result` block that follows the resumed call must attach to an assistant
message the provider has already seen — the `tool_use_id` correlation is
positional in the message array (`anthropic_adapter.py:113-131`). So this is a
**new durable store of prompt and model output**, not a wider column on an
existing row. That is a storage-and-retention change with its own PII, size, and
tenancy questions (section 2.3), and this pass's gate says to say so rather than
fold it quietly into a schema diff. **Flagged, not designed around.**

### 0.3 GATE 2 TRIPPED — multi-call is structurally possible and has no per-call resolution

One assistant turn can carry several `tool_use` blocks, and the loop dispatches
all of them: `for call in (b for b in response.content if b.kind == "tool_use")`
(`execution.py:933`), each through `_invoke_tool` (`:955`), with the results
batched into one `user` message (`:932`, `:941`). The provider-neutral shape
allows it (`adapters/llm/gateway.py:80-87`: a message is a *list* of blocks) and
nothing caps the count per turn — `max_calls_per_run` (`contracts/base.py:100`)
is a per-run total enforced in the proxy (`tools/proxy.py:203-215`), not a
per-turn one.

Against that, the verdict surface is strictly single-valued:

* `hitl_queue.status` is one scalar column, `CHECK (status IN
  ('pending','approved','rejected','modified','expired'))` (migration
  `0001:212-214`);
* `'modified'` is in that vocabulary but is **written nowhere in `src/`** — the
  only occurrence is the comment listing the vocabulary at `dal/ports.py:446`;
* the verdict body accepts a note and nothing else — `HitlVerdictRequest` has
  one field, `note`, under `extra="forbid"` (`edge/routes/hitl.py:35-37`);
* `approve` claims the whole row with one `status_to="approved"`
  (`app/hitl/service.py:164-168`) and returns one `DeliverableRow`.

So "approve two of these three calls" is not expressible in the schema, the API,
or the service. Whether it *should* be is a product question — it determines
whether the approval UI must grow per-call controls — and it is not a decision
this document can make. **Reported as an ambiguity requiring a product
decision (section 4, section 8 D4).**

### 0.4 GATE 3 — in-flight requests do NOT break, but the cutover is still a choice

This one does not trip. `HitlReplayEnvelope` is `frozen=True, extra="forbid"`
(`schemas/hitl.py:57`), and the module already documents the exact pattern for
adding a field without breaking stored rows: "Optional with a default so every
row written before this field existed still parses under `extra="forbid"`"
(`schemas/hitl.py:43-46`, written for `on_behalf_of_principal`). A new optional
resumption field inherits that property, so every row enqueued today still
validates at `app/hitl/service.py:173` after deploy.

What in-flight rows cannot do is *resume*, because they carry no resumption
state. That leaves a genuine choice between dual-behaviour and drain, with a
precedent for each. It is a decision, not a break. Section 6 lays out both with a
recommendation.

---

## 1. Current-state audit

### 1.1 Where execution pauses, and what is captured versus discarded (REVIEW 1)

`_govern` (`execution.py:526`) builds a proposal (`:564`), evaluates it (`:567`),
and on `deferred_to_human` writes the queue row **before** the terminal event
(ordering decision D3, `:571-578`), then raises `AgentDeferredToHuman`
(`:613-616`), which the routes map to 202 (`edge/routes/agents.py`;
`edge/routes/cowork.py:124-137`).

What the evaluator is given is the whole story. `_build_execution_proposal`
(`execution.py:1009-1034`) constructs a `DecisionProposal` with
`requires_external_launch=False` (`:1029`), `metadata={}` (`:1033`), no
`spend_minor_units`, and no `security_verdict`. The terminal branch for this
vertical is `_decide_agent_execution` (`app/decision_engine/evaluator.py:198`),
whose entire verdict is:

* `FIRST_EXTERNAL_LAUNCH` declared on the contract, then defer
  (`evaluator.py:237-250`)
* no triggers, or `defers_on_trigger_presence=False`, then approve (`:251-258`)
* any other trigger declared, then defer

Every branch reads `contract.human_in_loop_triggers` (`contracts/base.py:158`).
None reads anything the run attempted, and `evaluator.py:211-220` says why: the
stage runs before the mint and before the model, so trigger *presence* is all it
can observe. The async-path `hitl_check` (`evaluator.py:479-492`) matches
triggers against `proposal.metadata` via `_trigger_matches` (`:495-506`), and
this vertical leaves `metadata` empty, so those matchers are structurally inert
here.

**State captured at deferral.** `_enqueue_hitl` (`execution.py:618`) writes a
`HitlEscalation` (`dal/ports.py:400-431`) carrying the parent decision fields
plus `request_json=envelope.model_dump(mode="json")` (`execution.py:677`).

**State discarded at deferral.** Nothing, because nothing else exists yet. The
prompts are not built until `:310-311`; the token is not minted until `:340`
(single-shot) or `:844` (tool loop); the message list is not created until
`:859`. There is no tool call, message, `tool_use.id`, or model output in
existence at `:302-307` to discard.

That distinction matters for scoping. This is not a case of the system throwing
away state it holds. It is a case of the state never having been produced.

### 1.2 `HitlReplayEnvelope` — exact schema and every site (REVIEW 2)

Schema, verbatim from `schemas/hitl.py:56-63`:

```python
class HitlReplayEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    user_id: str
    correlation_id: UUID
    on_behalf_of_principal: str | None = None
```

`org_id` is deliberately absent — derived from the authenticated principal and
the RLS-scoped row, never from a stored payload (`schemas/hitl.py:23-25`). The
principal binding is an **id only**, on purpose: storing the compiled scope set
or `authority_fingerprint` would let an approval execute against authority that
no longer exists (`schemas/hitl.py:27-41`). Any field this design adds must be
weighed against that same rule, and section 3.4 does so explicitly.

Every site, exhaustively (`grep HitlReplayEnvelope src tests`):

| site | role |
|---|---|
| `app/agents/execution.py:55` | import |
| `app/agents/execution.py:648-654` | **sole construction site** |
| `app/agents/execution.py:677` | serialized into `HitlEscalation.request_json` |
| `app/hitl/service.py:62` | import |
| `app/hitl/service.py:173` | **sole read site** — `model_validate(row.request_json)` |
| `app/hitl/service.py:352` | typed parameter of `_journal_replay` |
| `dal/ports.py:428-431` | documents `HitlEscalation.request_json` as this model |
| `dal/ports.py:445` | `HitlQueueItem.request_json` |
| `edge/routes/hitl.py:191-204` | reads the raw dict for the review summary |
| `schemas/hitl.py:56` | definition |
| `tests/integration/test_cowork_hitl_replay_pg.py:62,306` | round-trip assertion |
| `tests/unit/test_hitl_service.py:286` | invalid-envelope PERMANENT path |

One construction site and one read site is the whole surface. That is the good
news in this audit: the envelope itself is cheap to change.

Note that `edge/routes/hitl.py:191-204` reads `request_json` as an **untyped
dict**, not through the model. Any field added for resumption is invisible to the
review UI until `_summary` is changed — which matters, because the entire point
of the change is that the human sees what will execute.

### 1.3 `execute()`'s full call path, mechanically (REVIEW 3)

"Rebuilds prompts" and "runs the full tool loop" mean, precisely:

**Rebuilds prompts.** `_build_system_prompt(contract)` (`execution.py:310`,
defined `:1037`) and `_build_user_prompt(agent_id, validated_input)` (`:311`,
defined `:1069`) are pure functions of the contract and the validated input. They
are therefore *deterministic* across a replay: same envelope in, same two strings
out. The prompts are not the source of divergence.

**Runs the full tool loop.** `_execute_with_tools` (`:820`) does, in order:

1. mint a signed governance token (`:844`), with scope from
   `_principal_scope_for` (`:479`) — the intersection of the contract manifest
   with the human's *currently* compiled authority;
2. resolve the available tool definitions (`:852-857`);
3. seed `messages` with one user text block (`:859-861`);
4. loop `for iteration in range(contract.max_tool_iterations)` (`:870`):
   * re-run the ordered token pipeline `validate_tool_call` before every turn
     (`:877-885`) — signature, expiry, revocation, scope, budget, delegation
     (`contracts/token.py:344-349`);
   * call `generate_with_tools` (`:917`);
   * if `stop_reason != "tool_use"`, return the final text (`:928-929`);
   * append the assistant message verbatim (`:931`);
   * dispatch every `tool_use` block through `_invoke_tool` (`:933-940`);
   * append the batched `tool_result` blocks as one user message (`:941`);
5. exceeding `max_tool_iterations` is an audited governance escalation
   (`:943-953`), not a silent truncation.

**Where divergence comes from.** Exactly one place: the model's own sampling at
`:917`. Everything else on the path is a deterministic function of the envelope
plus current governance state. So a resumption mechanism does not need to
"skip prompt rebuilding" — rebuilding is harmless and correct. It needs to
**suppress or override the sampling** for the turn whose output was reviewed, and
only that turn.

**What the replay already keeps stable.** `HitlApprovalContext`
(`execution.py:188-210`) carries `hitl_id`, `decision_id`,
`original_correlation_id`, `approved_by`. It is threaded to the tool loop as
`hitl_id=` (`:322-325`, `:833`), then to `_invoke_tool` (`:938`) and into
`ToolProxy.invoke` (`:974`), landing on `ToolContext.hitl_id`
(`tools/base.py:60-72`). `execution.py:322-324` states its purpose: it is "the
ONLY value on this path that is stable across retries of the same approval". The
GCP compute-stop action already builds on exactly that —
`request_id_for(hitl_id, operation)` = `uuid5` of `f"{hitl_id}:{operation}"`
(`app/gcp/actions.py:127-135`), and refuses outright when `hitl_id` is absent
(`:246-249`, `MissingIdempotencyAnchor` at `:117`).

Also stable, and worth naming because a second suspension point would break it:
`run_id` is minted fresh per attempt (`:301`), `proposal_id == correlation_id ==
run_id` (`:1022-1023`), and both `decision_id_for` and `hitl_id_for` are `uuid5`
of `proposal_id` (`app/decision_engine/events.py:61-68`). `hitl_id` is
`hitl_queue`'s PRIMARY KEY (migration `0001:204`) and `decision_id` FKs to
`decisions` (`0001:207`). **Two deferrals inside one run would derive the same
`hitl_id` and the same `decision_id`, and collide on both primary keys.** Any
mid-loop gate therefore needs a per-suspension discriminator in the id
derivation. This is a concrete, citable constraint on section 3, not a
speculative one.

### 1.4 One tool call awaiting approval, or several? (REVIEW 4)

Several is possible. See section 0.3 for the evidence
(`execution.py:933`, `gateway.py:80-87`, `contracts/base.py:100` versus
`tools/proxy.py:203-215`).

Three further observations that bear on the design:

1. **In practice, today, zero.** No contract can currently reach a mid-turn
   deferral because no mid-turn gate exists. The multi-call question is entirely
   about the *proposed* gate, so it is answerable by design rather than by
   measurement.
2. **The three multi-turn contracts are small.** Per
   `docs/architecture/principal_dal_and_hitl_per_turn.md`, only
   `seo_keyword_agent`, `cfo_agent`, and `cowork_agent` declare
   `invocable_tools`. `cowork_agent`'s manifest is `llm.generate` +
   `memory.search` (`contracts/mvp/cowork.py:54-61`) — one generative, one
   read-only, neither externally mutating. So the first contract for which
   per-call approval semantics *matter* does not exist yet.
3. **That is an argument for deciding now, not for deferring.** The same doc
   warns that `cowork_agent`'s opt-out "stops costing nothing the moment a
   side-effecting tool joins that manifest". The per-call semantics question has
   the same shape: cheap to settle before a `stripe.refund`-class tool is
   reachable from a multi-turn contract, expensive after.

### 1.5 Is the adapter's message state retained anywhere? (REVIEW 5)

No. See section 0.2 for the exhaustive evidence. Two mechanical details matter
for the design:

**`tool_use.id` round-trips faithfully in both directions, when it is given.**
Inbound, `_normalize_anthropic_message` sets `tool_use_id=raw_block.id`
(`anthropic_adapter.py:154`). Outbound, `_to_anthropic_message` writes
`"id": block.tool_use_id` for a `tool_use` block (`:113-121`) and
`"tool_use_id": block.tool_use_id` for a `tool_result` block (`:122-131`). So a
`tool_use` block reconstructed from storage and replayed through
`generate_with_tools` reaches the provider with its **original** id. Nothing in
the adapter re-mints or validates it.

**`tool_use_id` never reaches the proxy.** `execution.py:978` and `:983` use it
only to build the `tool_result` block; the `invoke` call at `:967-975` passes
`tool_id`, `input_data`, `governance_token`, `contract`, `org_id`,
`correlation_id`, `hitl_id` — no `tool_use_id`. Closing that is one keyword
argument on `_invoke_tool` plus one on `ToolProxy.invoke` plus one field on
`ToolContext`. Cheap, and section 5 explains why it is only worth doing together
with the rest of this design.

---

## 2. What resumption actually requires — the honest scope

### 2.1 The suspension point must be created, not moved

There are only two candidate locations for a gate that can see a tool call.

**(A) Inside the tool loop, between sampling and dispatch.** After `:931`
(assistant message appended) and before `:933` (dispatch), evaluate whether any
`tool_use` block in this turn requires human approval. If so, persist the
resumption state and raise `AgentDeferredToHuman`.

**(B) Inside `ToolProxy.invoke`, as a fourth opt-in gate.** Alongside the OAuth,
permission, and spend gates (`tools/proxy.py:253-324` per the GCP design's
section 1.4 trace). Attractive because the proxy is the sole IF-TOOL path
(`tools/proxy.py:1-16`), so a gate there cannot be bypassed.

**Recommendation: (A), with the *declaration* of which tools require approval
living on the tool definition next to the other three profiles.** Reasons:

* (B) cannot capture resumption state without reaching back into the caller's
  `messages` local, which would either invert the dependency (the proxy learning
  about conversations) or require passing the whole history into every tool call.
  The proxy is deliberately ignorant of the loop; keeping it that way is worth
  more than the bypass-proofing, which (A) also gets because
  `_execute_with_tools` is the only caller of `_invoke_tool`.
* (A) sees the whole turn at once, which is what the multi-call semantics of
  section 4 need. (B) sees one call at a time and would have to defer each
  independently, multiplying tickets per turn.
* (A) is where the conversation prefix already is.

**Do not move the existing stage-2.5 gate.** It must stay: it is what refuses a
rejected request with zero spend, and `_decide_agent_execution`'s
`FIRST_EXTERNAL_LAUNCH` branch is unconditional by deliberate decision
(`evaluator.py:211-214`). The new gate is a **second, narrower** gate, not a
relocation. Two gates means two deferral shapes on one queue, which section 3.1
addresses with a discriminated envelope rather than by widening the existing one.

### 2.2 What is traded away, stated plainly

**The D1 property weakens for contracts that use the new gate.** Today, "a defer
means no LLM call, no deliverable, no ledger row" (`execution.py:292`). A
mid-loop deferral means at least one LLM call *has* happened and been billed
before the human sees anything. The tokens are real, the `ai_cost_ledger` row is
real (ADR-0006), and the spend cannot be refunded by a rejection.

This is not a bug to be engineered away; it is the necessary price of showing the
human an actual action instead of a request. But it must be stated as a governed
change, because two tests assert the current property directly:

* `tests/unit/test_agent_execute_gate.py:86` —
  `test_governed_defer_writes_hitl_and_skips_llm`, asserting
  `llm.generate.assert_not_called()` and
  `deliverables.create_deliverable.assert_not_called()` (`:104-106`);
* `tests/integration/test_cowork_hitl_replay_pg.py:310-312` — "A defer means
  nothing ran", `assert fake.attempts == before` and
  `assert await _deliverable_count(app_db, org) == 0`.

Both remain correct for the stage-2.5 gate and must keep passing unchanged. The
new gate needs its own tests asserting the *different* property: exactly one
sampling call happened, no tool was dispatched, one row was written. Section 7
lists this.

**A rejection after a mid-loop deferral leaves an abandoned run.** Today a
rejection means nothing happened. Under (A) it means one or more turns were
sampled, possibly some tools already ran in earlier iterations, and the run is
now terminated with no deliverable. That is a new state the audit trail must
describe honestly — see section 4.3.

### 2.3 The storage and retention change, named

Resumption state is not a widened column on a row that already holds a customer
payload. It is a new class of stored data:

* **Content.** The message prefix contains the rendered system prompt, the
  rendered user prompt (built from customer input), every assistant turn
  including the model's reasoning text, and every prior `tool_result` — which may
  include data a tool read from a customer's connected systems. Strictly more
  sensitive than `request_json`, which holds validated input only.
* **Size.** Unbounded in principle, and bounded in practice only by
  `max_tool_iterations` and `max_tokens` per turn
  (`execution.py:864`: `min(contract.max_token_budget // 2, 4096)`). A
  `JSONB` column is workable; a large one on a hot review-queue table is not
  free.
* **Lifetime.** `hitl_queue` rows have `expires_at` (48h,
  `execution.py:78 _HITL_EXPIRY_HOURS`) but nothing purges the payload after a
  verdict. `request_json` is described as "written ONCE at enqueue and never
  rewritten" (`app/hitl/service.py:23`), which is a correctness property for
  replay and an indefinite-retention property for the data.
* **Tenancy.** `hitl_queue` already carries ENABLE + FORCE RLS with the
  `tenant_isolation` policy (migration `0001:347-367`; noted at
  `0015:18-23`), so a new column inherits isolation. A new *table* would need its
  own policy.

**Recommendation: same table, new nullable `JSONB` column, plus an explicit
post-verdict redaction step.** Reusing `hitl_queue` inherits RLS, the expiry
column, the exactly-once claim, and the review query, and avoids a second
lifecycle to reason about. The redaction step is the new obligation: once a row
reaches a terminal status, the conversation prefix has no further use and should
be nulled, leaving the audit trail (which lives in `audit_log`, not here) as the
durable record. That is a deliberate departure from `request_json`'s
never-rewritten rule and needs owner sign-off (section 8 D5), because it trades a
retention reduction against the "frozen payload" invariant that
`_terminate_failed` (`app/hitl/service.py:452-482`) reasons from.

---

## 3. Proposed design

Everything in this section is conditional on the owner decisions in section 8.
It is written so that the shape can be judged, not so that it can be typed in.

### 3.1 Envelope schema change

Add one optional field. Do not restructure the existing five.

```python
class HitlResumptionPoint(BaseModel):
    """The exact model turn a human reviewed, frozen for deterministic replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The conversation prefix the reviewed turn was sampled from, INCLUDING the
    # assistant message that carries the reviewed tool_use block(s). Serialized
    # LLMMessage list (adapters/llm/gateway.py:80-87).
    messages: list[dict[str, Any]]

    # The tool_use_id values in the final assistant message that require the
    # human verdict. A subset of that message's tool_use blocks: a turn may mix
    # gated and ungated calls.
    pending_tool_use_ids: list[str] = Field(min_length=1)

    # Which loop iteration this turn was (execution.py:870), so the resumed run
    # re-enters with the same remaining iteration budget rather than a fresh one.
    iteration: int = Field(ge=0)

    # Running token total at suspension (execution.py:857 total_tokens), so the
    # BUDGET stage resumes against the real ledger, not zero.
    tokens_used_so_far: int = Field(ge=0)


class HitlReplayEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    user_id: str
    correlation_id: UUID
    on_behalf_of_principal: str | None = None
    # NEW. Absent means the pre-existing request-level shape: approval re-runs
    # the agent from `input`. Present means approval RESUMES the stored turn.
    # Optional-with-default for the reason schemas/hitl.py:43-46 already gives:
    # every row written before this field existed still parses under
    # extra="forbid".
    resumption: HitlResumptionPoint | None = None
```

Four notes on what is deliberately *not* in it:

* **No governance token, no scope set, no `authority_fingerprint`.** Same rule as
  `on_behalf_of_principal` (`schemas/hitl.py:27-41`): the resumed run must
  re-mint and recompile authority as it stands at approval time. A stored token
  would let an approval execute on authority that has since been revoked, which
  is the exact failure the principal kernel exists to prevent.
* **No `org_id`.** Unchanged reasoning (`schemas/hitl.py:23-25`).
* **`messages` as `list[dict]`, not `list[LLMMessage]`.** The envelope is
  persisted JSON; typing it as the adapter model would couple the stored shape to
  a class that may gain fields. Validate into `LLMMessage` at read time, where a
  failure is a PERMANENT disposition the existing code already handles
  (`app/hitl/service.py:173-184`).
* **The presence of `resumption` is the discriminator.** Two deferral shapes on
  one queue, distinguished by a field rather than by a new status value or a
  second table. `_summary` (`edge/routes/hitl.py:190-205`) then has one branch:
  render `request_input` as today when absent, render the pending tool calls when
  present.

**`hitl_id` derivation must change for the new gate.** Per section 1.3,
`hitl_id_for(proposal_id)` and `decision_id_for(proposal_id)` are both `uuid5` of
`proposal_id == run_id`, and both target primary keys. A mid-loop gate needs a
discriminator — the natural one is the reviewed turn itself, e.g.
`uuid5(ns, f"hitl:{proposal_id}:{iteration}")`. This is a real change to
`app/decision_engine/events.py:66-68`, whose current one-line docstring
("Deterministic HITL ticket id derived from the source proposal id") would become
false. Note the branch `fix/hitl-id-single-mint` exists and touched this area;
whoever implements must reconcile with it rather than assume this file is quiet.

### 3.2 The resumption call path

**A new entry parameter on `execute`, not a new public method.** `execute`
already takes `hitl_approval: HitlApprovalContext | None`
(`execution.py:259`), and that object is documented as unreachable from the HTTP
path by construction (`:198-203`). Extend it rather than adding a second entry
point, so the "unreachable from the request path" argument keeps holding without
a second proof:

```python
@dataclass(frozen=True, slots=True)
class HitlApprovalContext:
    hitl_id: UUID
    decision_id: UUID | None
    original_correlation_id: UUID
    approved_by: str
    # NEW: present when the approval resumes a stored turn.
    resumption: HitlResumptionPoint | None = None
```

`HitlQueueService.approve` (`app/hitl/service.py:187-204`) passes
`resumption=envelope.resumption`. No other call site changes.

**Steps 1, 2, and 2.5 of `execute` run unchanged on a resumption.** This is
important and easy to get wrong. Input re-validation at `:281-286` is the K7
schema-drift check and must still run; the gate is still skipped because
`hitl_approval is not None` (`:302`). Prompt rebuilding at `:310-311` still runs,
and is still deterministic. The only difference is what `_execute_with_tools`
does with them.

**Inside `_execute_with_tools`, resumption changes the loop's entry, not its
body.** Add one parameter and one pre-loop branch:

* mint the token as today (`:844`) — full recompilation, no shortcut;
* if `resumption` is None: seed `messages` as today (`:859-861`), start
  `iteration` at 0, `total_tokens` at 0;
* if `resumption` is present: hydrate `messages` from
  `resumption.messages` (validating each into `LLMMessage`), set `total_tokens =
  resumption.tokens_used_so_far`, and enter the loop at
  `resumption.iteration` — then **skip the sampling call for this one
  iteration** and take the `tool_use` blocks from the last hydrated assistant
  message instead of from a fresh `generate_with_tools` response.

Concretely, the loop body becomes: *obtain this turn's assistant content* —
either by sampling (`:917`) or, on the first resumed iteration only, by reading
the hydrated tail — then run the existing dispatch at `:932-941` unchanged. After
that first iteration the flag is spent and every subsequent turn samples
normally. That satisfies the requirement that only the approved call is
deterministic while later turns still get real model reasoning.

**Answering the either/or in the brief directly:** the model must **not** be
re-invoked with its output then overridden. Two reasons. It costs a full sampling
call whose result is thrown away, which the ledger then has to account for
against a run that did not use it. And it is unsound: to sample the turn at all
you must send the prefix, and the response may contain `tool_use` blocks with
*new* ids that then sit in `messages` alongside the stored ones — two competing
truths in the array the provider correlates positionally. Skip the call.

**The pre-turn governance pipeline must still run before the resumed dispatch.**
`validate_tool_call` at `:877-885` is not sampling; it is the ordered
signature/expiry/revocation/scope/budget/delegation check
(`contracts/token.py:344-349`). It must run on the resumed iteration exactly as
on any other, against the freshly minted token and with `tokens_used_so_far`
seeded from the envelope. This is what makes the answer to "does approval let a
stale action through" be no: the action is frozen, the *authority* is not.

### 3.3 What determinism this buys, and what it does not

**Buys:** the tool id, the tool input, and the `tool_use_id` of the approved
call(s) are byte-identical to what the human saw, because they are read from
storage rather than re-sampled. That is the whole point.

**Does not buy:** determinism of the *rest* of the run. Turns after the resumed
one are sampled fresh and may differ from anything a reviewer imagined. That is
correct and intended — the brief says so — but it means the honest UI claim is
"approving executes THIS call" and never "approving executes only this call". If
a contract needs the stronger property, the mechanism is
`max_tool_iterations`, not this design.

**Also does not buy:** protection on the ordinary (non-deferred) path. Nothing
here changes a run that was never deferred. As
`spend_reservation_replay_semantics.md` section 7 established, the ordinary-path
replay vector is a client retrying the HTTP request, which needs a
client-supplied request idempotency key that `/agents/execute` does not accept.
Separate work, explicitly out of scope.

### 3.4 What must NOT be skipped on resumption

A checklist, because "resume the stored action" is exactly the kind of phrase
that invites skipping safety checks along with the sampling:

| must still run | citation | why |
|---|---|---|
| input re-validation | `execution.py:281-286` | K7 schema drift; PERMANENT disposition if it fails |
| principal scope intersection | `_principal_scope_for`, `:479-522` | offboarded/descoped human must refuse |
| token mint | `:844` | recompiles authority at approval time |
| ordered token pipeline per turn | `:877-885` | revocation, kill switch, scope, budget |
| `ToolProxy.invoke` gates | `tools/proxy.py:190-215`, `:253-324` | scope, call-count, OAuth, permission, spend |
| `max_calls_per_run` | `tools/proxy.py:203-215` | see the caveat below |

**One real hazard: `ToolCallCounter` is keyed on `correlation_id`**
(`tools/proxy.py:83-100`: `dict[tuple[UUID, str, str], int]`, keyed
`(correlation_id, agent_id, tool_id)`, "in-process only ... accumulates for the
life of the process"). A resumed run gets a **fresh** `run_id` (`:301`), so its
counter starts at zero and calls made before the suspension are not counted
against `max_calls_per_run`. That is already true of today's re-sampling replay,
so this design does not introduce it — but a resumption makes it easier to
notice, because the pre-suspension calls are now visibly part of the same
logical run. Worth a decision (section 8 D6): either seed the counter from the
envelope, or state explicitly that the ceiling is per-attempt.

---

## 4. Multi-call and rejection semantics

### 4.1 The question, stated exactly

A gated turn carries `tool_use` blocks `[A, B, C]`. Suppose A and C require
approval and B does not. What can a human do?

Today's surface supports exactly two answers: approve the row, or reject the row
(section 0.3). Per-call verdicts are not expressible.

### 4.2 Recommendation: per-TURN approval, with per-call declaration

**One ticket per gated turn. One verdict. All gated calls in that turn are
approved together or none are.**

Rationale, in order of weight:

1. **A turn is the unit the model reasoned about.** The blocks in one assistant
   message were sampled together under one plan. Executing A and C but not B — or
   A but not C — hands the model a `tool_result` set it never contemplated, and
   the next turn's reasoning is then conditioned on a state no plan produced.
   Partial execution of a jointly-sampled turn is a correctness hazard dressed up
   as a governance feature.
2. **The schema says so.** One `status` (migration `0001:212-214`), one
   `verdict_json`, one claim (`app/hitl/service.py:164-168`). Per-call verdicts
   need a child table, a per-call claim, and a re-definition of what
   `status='approved'` means when two children disagree. That is a much larger
   change than the one this document already found too large to be a schema
   tweak.
3. **Ungated calls in a gated turn are the real ambiguity, and turn-level
   approval resolves it cleanly.** B executes when the turn is approved, and
   does not execute at all when the turn is rejected. The alternative — dispatch
   B immediately and hold A and C — splits one turn's side effects across a human
   review window, which is worse than either extreme.

**What this leaves open, and cannot settle here:** whether the product wants
per-call approval as a *user-visible feature*. If a reviewer must be able to say
"the refund yes, the email no", then turn-level approval is the wrong answer and
the queue schema needs the child table. **That is a product decision (section 8
D4).** This document's position is that per-turn is right on engineering grounds
and that the product question should be answered explicitly rather than settled
by default.

### 4.3 Rejection

**Reject terminates the whole run.** No tool in the gated turn dispatches, no
deliverable is created, and the run does not continue to a later turn. `reject`
already requires no envelope (`require_request=False`,
`app/hitl/service.py:306`) and executes nothing, so the service needs no change.

Two things the current rejection path does **not** yet say, and must:

* **Earlier turns' side effects are not undone.** If iteration 0 called a
  mutating tool and iteration 1 was gated and rejected, the iteration-0 effect
  stands. There is no compensation mechanism anywhere in the codebase and this
  design does not invent one. It must be disclosed in the reviewer UI: rejecting
  stops what has not happened, and does not reverse what has.
* **The sampled-but-rejected turn was billed.** Per section 2.2. The
  `ai_cost_ledger` row is correct and stays; a rejection is not a refund.

**Partial approval is deliberately NOT designed.** Per section 4.2 the
recommendation is that it should not exist. Designing it speculatively would
produce exactly the kind of unimplemented half-mechanism this repository's
evidence discipline warns about. If the owner decides per-call approval is
required, that is a separate design pass with a schema change of its own.

---

## 5. `tool_use.id` stability and the ledger-identity gap

**Does the original `tool_use.id` survive?** Yes, and by construction rather than
by luck. It is stored in `resumption.messages` inside the assistant message, and
`_to_anthropic_message` writes it back to the provider verbatim
(`anthropic_adapter.py:113-121`). Because the resumed iteration does not sample
(section 3.2), no new id is ever minted for that turn — there is no
`_normalize_anthropic_message` call (`:154`) to mint one.

**Does that close the gap `spend_reservation_replay_semantics.md` section 7
identified?** Yes for the HITL path, with three conditions that must be met
together, and no for the ordinary path.

The ledger doc's blocker B was "there is no stable identity for *this logical
tool call*", and its verdict on `tool_use.id` was that it "is not stable across
the retry it exists to protect", because "each approval attempt is a fresh LLM
sampling and mints fresh block ids". This design removes that cause directly: on
a resumption there is no fresh sampling for the reviewed turn, so
`(hitl_id, tool_use_id)` is stable across every retry of the same approval. Its
Option 1 was exactly this, and its own words apply — "then `tool_use.id` is
stable because it is stored rather than re-minted, and the owner's chosen
identity works as intended."

The three conditions:

1. **`tool_use_id` must be plumbed to the proxy.** It is not today
   (`execution.py:967-975`; section 1.5). One keyword argument on `_invoke_tool`,
   one on `ToolProxy.invoke`, one field on `ToolContext` (`tools/base.py:48-72`),
   mirroring how `hitl_id` was threaded.
2. **The ledger key must be `(hitl_id, tool_use_id)`, not `tool_use_id` alone.**
   `tool_use_id` is provider-scoped and carries no tenancy or ticket binding.
   Pairing it with `hitl_id` matches the existing `request_id_for(hitl_id,
   operation)` precedent (`app/gcp/actions.py:127-135`).
3. **The key must only be trusted when `resumption` is present.** A run that
   deferred at stage 2.5 and was approved still re-samples, so its
   `tool_use_id` values are freshly minted and unstable. The ledger must not key
   on them. Since `HitlApprovalContext.resumption` is the discriminator and it is
   already threaded to the loop, the condition is checkable at the point of use.

**Ordinary path: unchanged and still unprotected.** The ledger doc's re-answer to
its question 4 stands — ordinary runs are never replayed by the platform, so there
is nothing for a `tool_use.id`-derived key to guard, and the real vector is a
client HTTP retry needing a client-supplied idempotency key. This design does not
change that and does not claim to.

**Therefore `fix/toolproxy-ledger-commit-accounting` can resume on this design,
scoped to the HITL path, once conditions 1-3 above are accepted.** It should not
adopt `(hitl_id, tool_use_id)` as a universal key. Its Option 2 (a run-level
`uuid5(hitl_id, tool_id)` plus a once-per-run rule for spend-capable tools)
remains the correct fallback for any path where `resumption` is absent, including
every contract that keeps deferring at stage 2.5.

---

## 6. Migration and cutover for in-flight requests

### 6.1 The mechanical position

Nothing breaks. `resumption` is optional with a default, so a row written today
parses after deploy (`schemas/hitl.py:43-46`, the precedent set by
`on_behalf_of_principal`). The new column is `NULL` for existing rows, exactly as
`request_json` was `NULL` for pre-0015 rows.

An in-flight row simply has `resumption is None`, which means: approval re-runs
the agent from `input`, as it does today. So the *default* cutover is
dual-behaviour, and it requires no code beyond the branch section 3.2 already
needs.

### 6.2 The two options

**Option A — dual-read, drain naturally (RECOMMENDED).** Deploy with both shapes
supported. Rows enqueued before the deploy keep today's re-sampling semantics;
rows enqueued after by a contract using the new gate get resumption. Old rows
drain within `_HITL_EXPIRY_HOURS` = 48h (`execution.py:78`), after which the
population is uniform.

* Pro: no pending approval is destroyed. Nobody's queued work vanishes on a
  deploy. The dual-read branch is needed anyway, because the stage-2.5 gate is
  not going away (section 2.1) and will keep producing `resumption is None` rows
  indefinitely.
* Con: for up to 48h two approval semantics coexist. The reviewer UI must
  distinguish them or it will mislead — which is the *existing* honesty problem,
  not a new one, and is section 8 D3.
* Note: "dual-read" overstates it. Since the stage-2.5 gate persists, the
  `resumption is None` branch is permanent, not transitional. There is no second
  code path to remove later.

**Option B — drain first.** Refuse to deploy while `status='pending'` rows exist;
or expire them on deploy the way migration `0015:53-59` did (`UPDATE hitl_queue
SET status='expired' WHERE request_json IS NULL AND status='pending'`).

* Pro: one semantics at a time.
* Con: expiring pending rows destroys queued human work. 0015 could do it
  honestly because those rows were *unreplayable* — approval "could never
  execute them" (`0015:25-29`), and the observed count was "0 pending / 0 total"
  (`:28-29`). Neither holds here: today's rows are perfectly replayable under
  today's semantics, and the count at a future deploy is unknown.

**Recommendation: Option A.** Option B's precedent does not transfer, because the
condition that justified it (the rows were already worthless) is absent.

### 6.3 Downgrade

Dropping the column loses resumption state for any row still pending, which
downgrades those rows to today's re-sampling semantics rather than breaking them
— because `resumption is None` is a supported state, not an error. That is a
graceful downgrade and should be stated in the migration docstring, following
`0015:62-65`'s practice of saying explicitly what a downgrade does and does not
revert.

---

## 7. Test impact

Not exhaustive; the tests whose *meaning* changes, so that none of them is
edited by reflex during implementation.

**Must keep passing unchanged** (they describe the stage-2.5 gate, which is
unchanged):

* `tests/unit/test_agent_execute_gate.py:86`
  `test_governed_defer_writes_hitl_and_skips_llm` — `llm.generate.assert_not_called()`
  (`:104`), `deliverables.create_deliverable.assert_not_called()` (`:105`).
* `tests/integration/test_cowork_hitl_replay_pg.py:306-312` — envelope round-trip
  plus "A defer means nothing ran".
* `tests/unit/test_hitl_service.py:286` — invalid `request_json` is PERMANENT.

**New tests the design requires:**

* A mid-loop deferral: exactly one `generate_with_tools` call happened, zero
  `ToolProxy.invoke` calls, one `hitl_queue` row, and `resumption` is populated
  with the reviewed `tool_use_id`.
* A resumption: zero `generate_with_tools` calls for the resumed iteration, the
  dispatched `tool_id` and `input_data` are byte-identical to the stored block,
  and the `tool_use_id` reaching `ToolContext` equals the stored one.
* A resumption where a later turn samples normally — proving the determinism is
  scoped to one turn.
* A resumption after the principal was descoped: refuses via
  `_PERMANENT_PRINCIPAL_ERRORS` (`app/hitl/service.py:81-85`) exactly as the
  request-level replay does. This is the test that proves the frozen action did
  not freeze the authority.
* A resumption whose stored `messages` fail `LLMMessage` validation: PERMANENT
  disposition, terminal `expired`, nothing executed.
* An envelope with `resumption` absent still approves and re-runs — the
  dual-behaviour guarantee of section 6.2.
* Envelope forward-compatibility: a stored dict without the `resumption` key
  validates (guards the `extra="forbid"` trap).

**Gates:** `powershell -ExecutionPolicy Bypass -File scripts/ci_unit_gate.ps1`
for the unit job, and the Postgres-backed integration tests must be confirmed to
have RUN, not skipped, before any claim about the queue or tenancy — with
`SKYLIZE_TEST_APP_DB_URL` pointed at the non-superuser `skylize_app` role
(`tests/integration/conftest.py:30-31`).

---

## 8. Open decisions requiring owner sign-off

Nothing below is implementable until these are answered. D1, D2, and D4 are the
blocking ones.

**D1 — Accept a second, post-sampling HITL gate at all?** It costs the D1
property "a defer means no LLM call, no deliverable, no ledger row"
(`execution.py:292`) for contracts that opt in. Without it there is no reviewed
action to resume and this whole design is moot; today's approval semantics
("re-run this agent on this input") would stand, and the honest fix would be UI
copy that says so. *This is the decision the rest depends on.*

**D2 — Accept a new durable store of prompt and model output?** Section 2.3. New
data class, more sensitive than `request_json`, currently with no purge story.
Recommendation: same table, new nullable `JSONB`, plus post-verdict redaction.

**D3 — How does the reviewer UI distinguish the two deferral shapes?** A
request-level ticket ("re-run this agent on this input; the model may decide
differently") and an action-level ticket ("execute exactly this call") mean
materially different things and will coexist permanently (section 6.2). Showing
both as one undifferentiated queue is the honesty gap this work exists to close,
so leaving it unanswered would defeat the purpose. Note this also requires
changing `_summary` (`edge/routes/hitl.py:190-205`), which reads `request_json`
as an untyped dict today.

**D4 — Per-turn or per-call approval?** Section 4. Engineering recommendation:
per-turn. Product question: must a reviewer be able to approve some calls in a
turn and not others? If yes, this design is the wrong shape and the queue needs a
child table.

**D5 — May `request_json` be rewritten after a terminal verdict?** Redaction
(section 2.3) contradicts "written ONCE at enqueue and never rewritten"
(`app/hitl/service.py:23`), which `_terminate_failed` (`:452-482`) reasons from.
The narrow version — redact only after a terminal status, never while pending —
preserves that reasoning, but it is still a change to a stated invariant.

**D6 — Is `max_calls_per_run` per logical run or per attempt?** Section 3.4.
`ToolCallCounter` keys on `correlation_id` (`tools/proxy.py:83-100`) and
`run_id` is fresh per attempt (`execution.py:301`), so it is per attempt today.
Pre-existing behaviour, surfaced by this work; decide whether to seed the counter
from the envelope or document the per-attempt semantics.

**D7 — `hitl_id` derivation for a mid-loop gate.** Section 3.1.
`hitl_id_for`/`decision_id_for` are `uuid5` of `proposal_id` and both target
primary keys (`app/decision_engine/events.py:61-68`; migration `0001:204,207`),
so two deferrals in one run collide. Needs a per-suspension discriminator, and
must be reconciled with the `fix/hitl-id-single-mint` branch rather than assumed
quiet.

---

## 9. What this document does not do

* **No code.** No `src/`, `tests/`, or `migrations/` change was made.
* **No claim that the current behaviour is a bug.** It is a disclosed design:
  `edge/routes/hitl.py:49` says the reviewer is shown the replay envelope's
  input, and that is what approval re-runs. The gap is between that behaviour and
  the platform's broader promise that approval means the reviewed action
  executes — a promise this design would make true, at the costs in section 2.
* **No design for partial approval.** Deliberately (section 4.3).
* **No ordinary-path idempotency design.** Out of scope, and unchanged by this
  work (section 3.3).
* **No decision on the OPA-side path.** `decision_engine/resume.py` publishes
  terminal events for a human verdict and executes nothing — it holds no
  `EvaluationPipeline` and no execution service (`resume.py:1-33`). Whether an
  OPA-engine deployment ever needs resumption is a separate question, gated
  behind `SKYLIZE_DECISION_ENGINE="opa"` which ADR-0004 keeps unenablable.
* **No verification of `docs/REPO_STATE.md`.** Figures there describe a specific
  commit and go stale; nothing here was taken from it.
