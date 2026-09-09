# HITL approval resumption — making an approval execute the reviewed action

> **Type:** architecture design note for a CORE GOVERNANCE MECHANISM change.
> It adds a second HITL gate inside the tool loop, a durable snapshot of the
> reviewed turn, and a resume path that dispatches the stored `tool_use`
> block(s) verbatim instead of re-sampling the model.
>
> **Status: DESIGN FINAL.** The three blocking decisions this document raised
> (D1, D2, D4) plus the drain strategy were signed off by the owner on
> 2026-09-09 and are recorded as CONFIRMED in section 8, not as proposals. The
> four non-blocking decisions (D3, D5, D6, D7) are resolved in this document
> with their reasoning stated. Implementation follows this document.
>
> **Commit designed against:** `95c3e79`, branch `feat/hitl-approval-resumption`
> (which contains `b0abf13`). **Date:** 2026-09-09. Every codebase claim is cited
> `file:line` and was re-verified in the tree at this commit. Where the previous
> revision of this document was wrong, the correction is inline and flagged
> **CORRECTION**.
>
> **Predecessor / blocked on this:**
> `docs/architecture/spend_reservation_replay_semantics.md` section 7 (branch
> `fix/toolproxy-ledger-commit-accounting`, commit `fcde853`). That document
> rejected `tool_use.id` as a replay identity and named "make replay a
> resumption instead of a re-sampling" as its Option 1. This document is that
> option, designed and built. Section 5 states exactly what it unblocks.
>
> **Sibling that must be read with this one:**
> `docs/architecture/principal_dal_and_hitl_per_turn.md`. It established that the
> synchronous gate runs before the model is called and can therefore only observe
> that a trigger is *declared*. That finding is the load-bearing constraint on
> everything here, and is the reason the new gate's arbiter is not the evaluator
> (section 3.1.2).

---

## 0. The four gates, and where they stand now

The goal: make HITL approval a **resumption** of the specific reviewed action
rather than a **re-sampling** of the agent run, so that approving a request
executes what the human was shown.

The previous revision of this document found that the premise did not hold at
this commit, and tripped three hard exit gates. All have now been answered. This
section records the answers, because the reasoning that produced them is the
reason the design has the shape it has.

### 0.1 There is no suspended tool call to resume — so one is created

**Unchanged finding.** There is no point in the request path at which a tool call
is suspended pending human approval. The only HITL deferral on the live request
path is `_govern` (`app/agents/execution.py:526`), called from `execute` at
`:303`. It runs at step 2.5:

| step | line | what happens |
|---|---|---|
| 1 | `:277` | resolve contract |
| 2 | `:281-286` | validate input against the contract's `input_schema` |
| **2.5** | **`:301-307`** | **the decision gate — the only place a request defers today** |
| 3 | `:310-311` | build system + user prompt |
| 4 | `:315-316` | tool loop (`_execute_with_tools`) or single-shot `generate()` |

At the moment of that deferral no prompt exists, no model has been called, no
`tool_use` block exists, and no conversation exists. `execution.py:292-293`
states the property the ordering provides: "a reject/defer verdict means no LLM
call, no deliverable, and no ledger row."

So the reviewed thing is a **request**, not an action, and making approval
execute the reviewed *action* requires creating a suspension point that does not
exist. Section 3.1 creates it.

### 0.2 GATE 1 (conversation state is not retained) — ANSWERED BY OWNER DECISION D2

The finding stands: message history exists only as a local variable.
`LLMMessage` is constructed at exactly three places in `src/`, all inside
`_execute_with_tools`: `execution.py:859-861` (seed), `:931` (assistant turn),
`:941` (tool results). The list is a local (`messages: list[LLMMessage]`, `:859`)
and is discarded when the function returns or raises. Nothing persists it: the
adapter is stateless (`adapters/llm/anthropic_adapter.py` rebuilds the wire
payload from `request.messages` on every call), `CoworkSessionService` holds no
history (`app/cowork/session.py:52-95` is token lifecycle only), and no table
stores conversation turns.

**Owner decision D2 accepts a new durable store, scoped minimally:** the
`tool_use` block(s) pending at suspension plus the message history up to that
point, and nothing more. Section 3.2 is that store, and section 3.2.4 answers the
retention question D2 attached to it.

### 0.3 GATE 2 (retention: is the snapshot categorically new content?) — CHECKED, DOES NOT TRIP

Owner decision D2 attached a condition: stop and report if the message-history
snapshot routinely contains content categorically different from what today's
audit trail retains. That check was run against the code. **It does not trip at
this commit — but for a reachability reason rather than an invariant, so the
condition under which it would trip is named here explicitly.**

What the audit trail retains today: `AuditService.record` takes `inputs` and
`outputs` and stores only `hash_payload(...)` of each — a SHA-256 hex digest,
documented "(PII-safe)" (`app/audit/service.py:28-33`, applied at `:59-60`). Tool
inputs and outputs are deliberately NOT retained in cleartext anywhere in the
audit trail. `ToolProxy._audit_call` passes `outputs=output.model_dump(mode="json")`
(`tools/proxy.py:364-368`) into exactly that hashing path.

What a snapshot holds in cleartext:

* the rendered user prompt — built from the validated input by `_build_user_prompt`
  (`execution.py:311`), and that validated input is **already** persisted in
  cleartext in `hitl_queue.request_json` (`execution.py:677`). Not new.
* assistant turns — model text plus `tool_use` blocks (tool id and tool input).
  New in cleartext, but derived from the input above.
* prior `tool_result` blocks — `json.dumps(result.output_json())`
  (`execution.py:978`). **This is the categorically new class:** data a tool read
  back from a customer's connected system, which today exists only as a hash.

Whether that is a stop condition turns on which tools are actually reachable from
a tool loop. Exactly four contracts declare `invocable_tools`:

| contract | `invocable_tools` |
|---|---|
| `contracts/mvp/cowork.py:64` | `llm.generate`, `memory.search` |
| `contracts/mvp/finance.py:176` | `utility.current_datetime` |
| `contracts/mvp/infrastructure.py:53` | `integration.gcp_stop_instance` |
| `contracts/mvp/seo.py:29` | `search.web`, `memory.search` |

None can reach `integration.hubspot_search_contacts`
(`tools/builtin/hubspot_tools.py:226`), the Drive tools, the Notion tools, or the
Asana tools — and `_invocable_tools_subset_of_allowed` (`contracts/base.py:182-187`)
enforces that `invocable_tools` is a subset of `allowed_tools`, so the manifest is
a real bound and not a naming convention. Of the five reachable outputs,
`memory.search` returns the org's own memory rows (already durable and
RLS-scoped), `search.web` returns public content, `utility.current_datetime`
returns a timestamp, `llm.generate` returns model text derived from the input, and
`integration.gcp_stop_instance` returns GCP operation metadata. **No reachable
tool output is unredacted third-party personal data or a credential.** Credentials
in particular cannot land there: `_ensure_oauth_credential` "never returns the
token ... Keeping the secret off the call path means it never lands on
`ToolContext`" (`tools/proxy.py:399-404`).

**The condition that would make this trip.** The moment any contract adds a tool
whose output carries third-party personal data — `integration.hubspot_search_contacts`
is the concrete one that exists today — to its `invocable_tools`, snapshots begin
holding that data in cleartext in `hitl_queue`, and this decision needs re-review
with its own retention and security sign-off. That trigger is recorded at the
snapshot construction site in the code, not only here, so the next person to widen
a manifest meets it.

### 0.4 GATE 3 (multi-call has no per-call resolution) — ANSWERED BY OWNER DECISION D4

The finding stands: one assistant turn can carry several `tool_use` blocks and
the loop dispatches all of them (`execution.py:933-940`, batched into one `user`
message at `:941`; the provider-neutral shape allows a list of blocks,
`adapters/llm/gateway.py:80-87`), while the verdict surface is strictly
single-valued — one `status` column
(`migrations/versions/0001_initial_schema.py:212-214`) and a `HitlVerdictRequest`
carrying one optional `note` under `extra="forbid"` (`edge/routes/hitl.py:35-37`).

**Owner decision D4 confirms per-TURN approval.** Approve resumes every pending
call in the reviewed turn; reject discards all of them. No change to the verdict
surface, no child table, no per-call partial approval this pass. Section 4 is the
confirmed semantics.

### 0.5 GATE 4 (must the new gate reuse `_govern`'s pattern?) — DOES NOT TRIP, with the divergence stated

The fourth exit condition was to stop if the mid-loop gate cannot cleanly reuse
`_govern`'s pattern and would need a structurally different mechanism. It reuses
it, but not in every part, so the split is stated plainly rather than glossed.

`_govern` is two separable things:

1. **Who decides.** `self._evaluator.evaluate(proposal)` (`execution.py:567`),
   whose terminal branch for this vertical is `_decide_agent_execution`
   (`app/decision_engine/evaluator.py:198`).
2. **What happens on a defer.** Durable `hitl_queue` row first (owner decision
   D3, `execution.py:571-578`), then the terminal decision event and audit under
   the "row written but emit failed" error guard (`:583-601`), then
   `AgentDeferredToHuman` (`:613-616`).

**Part 2 is reused literally.** `_govern` and the new gate call the same extracted
method, `_defer_to_human` (section 3.1.3). That is the part gate 4 exists to
protect: no second, drift-prone deferral path is created, and a change to the
D3 ordering or the emit-failure guard changes both gates at once because it is
one body of code.

**Part 1 cannot be reused, and the reason is in the evaluator's own docstring.**
`_decide_agent_execution` reads only `contract.human_in_loop_triggers`
(`evaluator.py:237-258`), and `evaluator.py:211-220` says why: "this stage runs
before the mint and before the model, against a proposal with no spend, no scope
and no security verdict, so trigger PRESENCE is all it can observe". Calling
`evaluate` a second time from inside the tool loop would ask a question the method
is documented as structurally unable to answer differently — and would return
`approved`, because a run that reached the tool loop is one whose stage-2.5
evaluation already approved it. The alternative, forcing the tool call into
`proposal.metadata` so `_trigger_matches` (`evaluator.py:495-506`) fires, would
require bending an existing trigger's meaning (`LOW_CONFIDENCE_IRREVERSIBLE`
needs a model confidence score that does not exist on this path), which is worse
drift than an honestly separate arbiter.

**So the new gate's arbiter is a declaration on the tool definition** — the same
pattern the proxy's four existing opt-in profiles already use (`spend`, `oauth`,
`permission`, `wif`; `tools/base.py:209-224`). Section 3.1.2.

---

## 1. Current-state audit

### 1.1 Where execution pauses, and what is captured versus discarded

`_govern` (`execution.py:526`) builds a proposal (`:564`), evaluates it (`:567`),
and on `deferred_to_human` writes the queue row **before** the terminal event
(ordering decision D3, `:571-578`), then raises `AgentDeferredToHuman`
(`:613-616`), which the routes map to 202.

`_build_execution_proposal` (`execution.py:1009-1034`) constructs a
`DecisionProposal` with `requires_external_launch=False` (`:1029`), `metadata={}`
(`:1033`), no `spend_minor_units`, and no `security_verdict`. Every branch of
`_decide_agent_execution` reads `contract.human_in_loop_triggers`
(`contracts/base.py:158`). None reads anything the run attempted.

**State captured at deferral.** `_enqueue_hitl` (`execution.py:618`) writes a
`HitlEscalation` (`dal/ports.py:398-431`) carrying the parent decision fields plus
`request_json=envelope.model_dump(mode="json")` (`execution.py:677`).

**State discarded at deferral.** Nothing, because nothing else exists yet. The
prompts are not built until `:310-311`; the token is not minted until `:844`; the
message list is not created until `:859`.

### 1.2 `HitlReplayEnvelope` — exact schema and every site

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

`org_id` is deliberately absent — derived from the authenticated principal and the
RLS-scoped row, never from a stored payload (`schemas/hitl.py:23-25`). The
principal binding is an **id only**, on purpose: storing the compiled scope set or
`authority_fingerprint` would let an approval execute against authority that no
longer exists (`schemas/hitl.py:27-41`). Section 3.2.2 applies that same rule to
the new snapshot.

One construction site (`execution.py:648-654`) and one read site
(`app/hitl/service.py:173`) is the whole surface. `edge/routes/hitl.py:191-204`
additionally reads `request_json` as an **untyped dict** for the review summary,
which is why the reviewer UI change (D3, section 3.4) is a real edit and not a
free consequence of adding a field.

### 1.3 The tool loop, mechanically

`_execute_with_tools` (`:820`) does, in order:

1. mint a signed governance token (`:844`), scope from `_principal_scope_for`
   (`:479`) — the intersection of the contract manifest with the human's
   *currently* compiled authority;
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

**Divergence comes from exactly one place:** the model's own sampling at `:917`.
Everything else is a deterministic function of the envelope plus current
governance state. So resumption does not need to skip prompt rebuilding —
rebuilding is harmless and correct. It needs to **suppress the sampling for the
one turn whose output was reviewed**, and only that turn.

### 1.4 `tool_use.id` round-trips verbatim — confirmed at this commit

Inbound, `_normalize_anthropic_message` sets `tool_use_id=raw_block.id`
(`adapters/llm/anthropic_adapter.py:154`). Outbound, `_to_anthropic_message`
writes `"id": block.tool_use_id` for a `tool_use` block (`:108-117`) and
`"tool_use_id": block.tool_use_id` for a `tool_result` block (`:122-131`).
Nothing in the adapter re-mints or validates it. So a `tool_use` block
reconstructed from storage and replayed reaches the provider with its **original**
id, and a `tool_result` correlates to it exactly as it would have in the original
run. This is the mechanical fact section 5's ledger identity rests on.

**`tool_use_id` never reaches the proxy today.** `execution.py:978` and `:983` use
it only to build the `tool_result` block; the `invoke` call at `:967-975` passes
`tool_id`, `input_data`, `governance_token`, `contract`, `org_id`,
`correlation_id`, `hitl_id` — no `tool_use_id`. Section 5 closes that.

### 1.5 Id derivation, and the collision — CORRECTION on both counts

`run_id` is minted fresh per attempt (`:301`), `proposal_id == correlation_id ==
run_id` (`:1022-1023`), and both `decision_id_for` and `hitl_id_for` are `uuid5`
of `proposal_id` (`app/decision_engine/events.py:61-68`). `hitl_id` is
`hitl_queue`'s PRIMARY KEY (`0001_initial_schema.py:204`) and `decision_id` FKs to
`decisions` (`:207`).

**CORRECTION 1 — the collision is not reachable at this commit.** The previous
revision said "two deferrals inside one run would derive the same `hitl_id` and
the same `decision_id`, and collide on both primary keys". The derivation claim is
right; the reachability is not. Both gates *raise* on defer — `_govern` at
`execution.py:613-616` and the new gate likewise — and the raise terminates the
run. One `execute()` call therefore produces at most one deferral, and every
`execute()` call mints its own fresh `run_id` at `:301`, including every approval
retry (`HitlQueueService.approve` does not pass a correlation id into `execute`;
`app/hitl/service.py:186-204`). So no two `hitl_queue` rows can currently derive
the same id.

The discriminator is still added (section 3.5), for a reason that is stated
honestly as defence in depth rather than as a fix for a live bug: today's
non-collision is a property of control flow (exactly one raise per run), not of
the id derivation, and the failure mode when a future edit breaks that property is
a primary-key violation on a governance table at the worst possible moment. Making
the derivation itself collision-free costs one optional keyword argument whose
default leaves every existing id byte-identical.

**CORRECTION 2 — `fix/hitl-id-single-mint` is merged, and it never touched this
file.** The previous revision said the branch "exists and touched this area" and
told the implementer to reconcile with it. Verified at this commit:

* `99ad381` ("fix(decision-engine): mint hitl_id once (deterministic uuid5), flow
  through event + queue row") is an ancestor of both `main` and this branch
  (`git merge-base --is-ancestor 99ad381 main` succeeds), and
  `git rev-list --left-right --count main...fix/hitl-id-single-mint` is `231 0` —
  the branch is fully contained in `main`, with `origin` at the same commit.
  **Status: MERGED, nothing open.**
* Its diff touches `src/skylize/decision_engine/{hitl_writer,orchestrator,pipeline,publisher}.py`
  and `tests/decision_engine/*` — the **OPA package**, which is not wired into the
  API process and which the request path must not import (CLAUDE.md, owner
  decision K3, `dal/hitl.py:1-21`). It changed `decision_engine/pipeline.py:75`
  `hitl_id_for(decision_id)`, a *different function in a different engine* from
  `app/decision_engine/events.py:66` `hitl_id_for(proposal_id)`.

So there is nothing to reconcile: this design's discriminator lands in the inline
engine's `events.py`, a file that branch never touched, and the OPA package's own
single-mint work is unaffected. The two engines keep their separate derivations,
as the two-engine separation requires.

---

## 2. What resumption requires

### 2.1 What is traded away, stated plainly

**The D1 property weakens for contracts that reach the new gate.** Today, "a
reject/defer verdict means no LLM call, no deliverable, and no ledger row"
(`execution.py:292-293`). A mid-loop deferral means at least one LLM call has
happened and been billed before the human sees anything. The tokens are real, the
`ai_cost_ledger` row is real (ADR-0006), and a rejection is not a refund.

Owner decision D1 accepts this knowingly. It is the necessary price of showing the
human an actual action instead of a request. Two existing tests assert the old
property and **must keep passing unchanged**, because they describe the stage-2.5
gate, which is not changing:

* `tests/unit/test_agent_execute_gate.py:86` —
  `test_governed_defer_writes_hitl_and_skips_llm`, asserting
  `llm.generate.assert_not_called()` and
  `deliverables.create_deliverable.assert_not_called()` (`:104-106`);
* `tests/integration/test_cowork_hitl_replay_pg.py:310-312` — "A defer means
  nothing ran".

The new gate gets its own tests asserting the *different* property: exactly one
sampling call happened, zero tools were dispatched, one row was written
(section 7).

**A rejection after a mid-loop deferral leaves an abandoned run.** Earlier turns'
side effects are not undone; there is no compensation mechanism anywhere in the
codebase and this design does not invent one. Section 4.2 states what the reviewer
must therefore be told.

### 2.2 The stage-2.5 gate does not move

It must stay: it is what refuses a rejected request with zero spend, and
`_decide_agent_execution`'s `FIRST_EXTERNAL_LAUNCH` branch is unconditional by
deliberate decision (`evaluator.py:211-214`). The new gate is a **second, narrower**
gate, not a relocation. Two deferral shapes coexist on one queue permanently —
not transitionally — which is why the reviewer UI must distinguish them
(section 3.4) and why the `resumption is None` branch is a permanent code path
rather than a migration artefact (section 6).

---

## 3. The confirmed design

### 3.1 The mid-loop suspension gate

#### 3.1.1 The insertion point, exactly

**Inside `_execute_with_tools`, between `execution.py:931` and `execution.py:933`.**

```
:928-929   if response.stop_reason != "tool_use": return ...   (unchanged)
:931       messages.append(LLMMessage(role="assistant", content=response.content))
           <-- THE GATE GOES HERE
:933-940   for call in (b for b in response.content if b.kind == "tool_use"): ...
:941       messages.append(LLMMessage(role="user", content=result_blocks))
```

Three reasons this is the only correct point:

* **After `:931`, not before.** The snapshot must include the assistant message
  carrying the reviewed `tool_use` blocks, because that is the message the
  eventual `tool_result` blocks correlate against. Gating before the append would
  force the gate to reconstruct a message the loop is about to build anyway.
* **Before `:933`, not later.** `:933-940` is dispatch. One line later the side
  effect has happened and there is nothing left to approve.
* **Not in `ToolProxy.invoke`.** The proxy is deliberately ignorant of the
  conversation; a gate there could not capture the snapshot without either
  inverting that dependency or passing the whole message history into every tool
  call. It also sees one call at a time, which would produce one ticket per call
  and directly contradict the per-turn atomicity owner decision D4 confirms. The
  bypass-proofing the proxy would have bought is already available at `:933`,
  because `_execute_with_tools` is the only caller of `_invoke_tool`.

#### 3.1.2 What trips the gate

A new fifth opt-in profile on `ToolDefinition`, alongside `spend`, `oauth`,
`permission`, and `wif` (`tools/base.py:209-224`):

```python
class ToolApprovalProfile(BaseModel):
    """Declares that invoking this tool requires a HUMAN APPROVAL of the exact
    sampled call, not merely of the request that started the run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str          # shown to the reviewer; why this tool is gated
    irreversible: bool = True
```

`ToolDefinition.approval: ToolApprovalProfile | None = None`, defaulting to None
for the same reason the other four do: **every tool registered before this field
existed is unaffected, so no existing contract's behaviour changes.** The gate
fires when any `tool_use` block in the just-sampled turn names a tool whose
definition declares `approval`.

**The gate is NOT scoped by `governed_org_ids`.** `_govern` runs only for
`org_id in self._governed_org_ids` (`execution.py:302`) because its arbiter is org
policy. This gate's arbiter is a property of the tool, identical for every tenant,
and the four sibling profiles in the proxy are likewise unconditional. Scoping a
money-moving tool's approval requirement on tenant enrolment would mean an
unenrolled org executes the exact call an enrolled org must have approved — the
wrong way to fail. It fails closed instead: if the tool is gated and the HITL
repository is not wired, the run raises `RuntimeError`, mirroring `_govern`'s own
`if self._hitl is None` guard (`execution.py:642-646`). No behaviour changes today
regardless, because no tool declares `approval` yet.

#### 3.1.3 What the gate does — `_govern`'s tail, shared

`_govern` is refactored so its deferral tail becomes a method both gates call:

```python
async def _defer_to_human(self, *, contract, proposal, result, hitl_id,
                          validated_input, user_id, on_behalf_of_principal,
                          resumption=None) -> NoReturn:
    # D3: durable row FIRST.
    await self._enqueue_hitl(contract, proposal, result, hitl_id,
                             validated_input=validated_input, user_id=user_id,
                             on_behalf_of_principal=on_behalf_of_principal,
                             resumption=resumption)
    try:
        await self._emit_decision(proposal, result, hitl_id=hitl_id)
    except Exception:
        log.error("hitl_row_written_but_decision_emit_failed", ...)
        raise
    raise AgentDeferredToHuman(hitl_id=hitl_id, reason=...)
```

This is `execution.py:571-616` moved, not rewritten: the same ordering, the same
error guard, the same exception. `_govern` calls it on its deferred branch and
emits directly on the other two outcomes, which is byte-equivalent to today
because no row exists on those paths for the error guard to name.

The mid-loop gate then needs a `DecisionResult` to hand it. It synthesises one
rather than calling the evaluator, for the reason section 0.5 gives:

* `outcome="deferred_to_human"`, `stages_completed=["tool_approval"]`,
  `stage_failed_at="tool_approval"`,
* `hitl_trigger="TOOL_APPROVAL_REQUIRED"`,
* `reasons` naming each gated `tool_id` and its profile `reason`,
* `policy_version=POLICY_VERSION` (`app/decision_engine/evaluator.py:44`),
* `decision_id` and `hitl_id` from the discriminated derivations (section 3.5).

The proposal is `_build_execution_proposal(...)` for the same run, so
`partition_key`, `department`, and `action_kind` match the request's own decision
records and the defer/approve/execute chain stays traceable (K8).

`_execute_with_tools` gains two parameters it does not have today —
`validated_input` and `user_id` — because `_enqueue_hitl` builds the
`HitlReplayEnvelope` from them (`execution.py:648-654`). It already receives
`on_behalf_of_principal`.

### 3.2 The snapshot store

#### 3.2.1 A new column, not a new table, and not inside `request_json`

Migration `0027_hitl_resumption_json.py`:

```sql
ALTER TABLE hitl_queue ADD COLUMN resumption_json JSONB
```

**Why the same table.** `hitl_queue` already carries ENABLE + FORCE RLS with the
`tenant_isolation` policy (`0001_initial_schema.py:347-367`, restated at
`0015_hitl_request_json.py:18-23`), already has `expires_at`, already has the
exactly-once claim, and is already read by the review query. A new column inherits
all of it; a new table would need its own policy and its own lifecycle.

**Why its own column and not a field inside `request_json`.** This is migration
0015's own rule, applied again. That migration chose a dedicated column over "a
key inside `proposal_json`" because "the two have different lifecycles"
(`0015_hitl_request_json.py:7-11`). The same distinction holds here:
`request_json` is the frozen replay identity, "written ONCE at enqueue and never
rewritten" (`app/hitl/service.py:23`), which `_terminate_failed` (`:452-482`)
reasons from. The snapshot is a *larger*, *more sensitive*, and *shorter-lived*
payload. Keeping them in separate columns means the never-rewritten invariant on
`request_json` is preserved untouched by this work — which is how open decision D5
is resolved (section 8).

No backfill. `NULL` is the correct and honest value for every existing row: they
carry no resumption state and are approved under today's semantics. Downgrade
drops the column, which downgrades any still-pending row to re-sampling rather
than breaking it, because `resumption is None` is a supported state — stated in
the migration docstring, following `0015:62-65`'s practice.

#### 3.2.2 The stored shape

```python
class HitlResumptionPoint(BaseModel):
    """The exact model turn a human reviewed, frozen for verbatim replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The conversation prefix the reviewed turn was sampled from, INCLUDING the
    # assistant message carrying the reviewed tool_use block(s). Serialized
    # LLMMessage list (adapters/llm/gateway.py:80-87).
    messages: list[dict[str, Any]] = Field(min_length=1)

    # Every tool_use_id in that final assistant message, in dispatch order.
    # Per-TURN atomicity (owner decision D4): all of these resume together.
    pending_tool_use_ids: list[str] = Field(min_length=1)

    # Which loop iteration this was (execution.py:870), so the resumed run
    # re-enters with the remaining iteration budget, not a fresh one.
    iteration: int = Field(ge=0)

    # Running token total at suspension (execution.py:863), so the BUDGET stage
    # resumes against the real ledger rather than zero.
    tokens_used_so_far: int = Field(ge=0)
```

Deliberately **not** in it, all for the reason `schemas/hitl.py:27-41` already
gives about the principal binding: no governance token, no compiled scope set, no
`authority_fingerprint`, no `org_id`. The resumed run re-mints and recompiles
authority as it stands at approval time. **The action is frozen; the authority is
not.**

`messages` is typed `list[dict]` rather than `list[LLMMessage]` because the column
is persisted JSON and must not be coupled to a model that may gain fields.
Validation into `LLMMessage` happens at read time, where a failure is a PERMANENT
disposition the existing code already handles (`app/hitl/service.py:173-184`).

`pending_tool_use_ids` is redundant with `messages[-1]` by construction, and that
is the point: the stored tail's `tool_use` ids are checked against it, so a
truncated, reordered, or tampered snapshot is refused instead of silently
dispatching a different set of calls than the one the human approved.

**That check is a validator on the model, not a check inside the tool loop**, and
the placement is load-bearing. The approval path validates this model before
`execute()` is called, where a failure is a PERMANENT disposition that terminates
the row — the same treatment an invalid `request_json` gets, and for the same
reason: it fails identically on every retry. Performed during execution instead,
the identical corruption would surface as a transient failure and release the row
to `pending` forever.

#### 3.2.3 Write on suspension, read on approval

* **Write** happens once, inside `_enqueue_hitl`, in the same transaction as the
  `decisions` and `hitl_queue` INSERTs (`dal/hitl.py:73-122` opens one
  `tenant_session`), so a row can never exist with a decision but without its
  snapshot. `HitlEscalation` gains `resumption_json: dict | None = None`;
  `HitlQueueItem` gains the same field. The OPA-side writer
  (`decision_engine/hitl_writer.py`) never sets it, exactly as it never sets
  `request_json`.
* **Read** happens once, in `HitlQueueService.approve`, immediately after the
  envelope parse (`app/hitl/service.py:172-184`). A `resumption_json` that fails
  `HitlResumptionPoint` validation is PERMANENT and terminal, for the same reason
  an invalid `request_json` is: it is written once and will fail identically on
  every retry.

#### 3.2.4 Expiry — the existing 48h window, and nothing new

**No new expiry mechanism is built, because the correct one already exists.**
`_HITL_EXPIRY_HOURS = 48` (`execution.py:78`) is stamped onto `expires_at` at
enqueue (`:675`), and the conditional claim already refuses an expired row:

```sql
UPDATE hitl_queue SET status=$3, ... WHERE hitl_id=$1 AND org_id=$2
  AND status='pending' AND (expires_at IS NULL OR expires_at > $6) ...
```
(`dal/hitl.py:169-179`; the in-memory repository mirrors it at
`dal/memory.py:295`). `_raise_refusal` then turns that into
`HitlExpired` (`app/hitl/service.py:418-419`), which the route maps to HTTP 410
(`edge/routes/hitl.py:181-182`).

**An expired snapshot is explicitly REJECTED, not downgraded to re-sampling.**
That is the deliberate answer, and the reason is that the alternative is worse in
both directions: falling back to today's semantics would execute an agent run
nobody can still review, using a row the queue has already declared dead, and it
would resurrect a row that both repositories refuse to claim. Refusing is also
free — it is the behaviour the existing claim predicate already produces, for
resumption rows and request-level rows alike.

#### 3.2.5 Retention after a verdict

`resumption_json` persists after a terminal verdict, exactly as `request_json`
does. This is the deliberate application of owner decision D2's instruction to
treat the snapshot as part of the existing HITL audit trail's retention
discipline rather than as a new category with its own policy. No post-verdict
redaction step is built, and that is a decision rather than an omission: a
redaction step would have to run on some terminal paths but not on the
transient-failure path that releases a claimed row back to `pending`
(`app/hitl/service.py:427-437`), and getting that distinction wrong would destroy
resumption state for a row a human is about to retry — silently downgrading a
verbatim resume into a re-sample. The 48h window bounds how long the snapshot is
*actionable*; section 0.3 bounds what it can contain and names the condition that
would reopen this decision.

### 3.3 The resume path

#### 3.3.1 Threading

`HitlApprovalContext` (`execution.py:189-210`) gains one field:

```python
resumption: HitlResumptionPoint | None = None
```

extending the existing object rather than adding a second entry point, so its
"unreachable from the HTTP path by construction" argument (`:198-203`) keeps
holding without a second proof. `HitlQueueService.approve` populates it from
`row.resumption_json`; no other call site changes.

#### 3.3.2 Steps 1, 2, and 2.5 of `execute` run unchanged

Input re-validation at `:281-286` is the K7 schema-drift check and still runs. The
stage-2.5 gate is still skipped, by the same `hitl_approval is None` condition at
`:302`. Prompt rebuilding at `:310-311` still runs and is still deterministic. The
only thing that changes is what `_execute_with_tools` does with them.

#### 3.3.3 Inside the loop: hydrate, then skip exactly one sampling call

* `resumption is None` — seed `messages` as today (`:859-861`), `iteration` starts
  at 0, `total_tokens` at 0. Byte-identical to current behaviour.
* `resumption` present — hydrate `messages` by validating each stored dict into
  `LLMMessage`, set `total_tokens = resumption.tokens_used_so_far`, and start the
  loop at `resumption.iteration`. On that first iteration only, **do not call
  `generate_with_tools`**: take the turn's assistant content from the hydrated
  tail, verify its `tool_use` ids equal `pending_tool_use_ids`, and fall straight
  into the existing dispatch at `:933-940`. The flag is spent after that
  iteration; every later turn samples normally.

**The model is not re-invoked and then overridden.** Two reasons, both decisive.
It would cost a full sampling call whose result is discarded, which the
`ai_cost_ledger` then has to account for against a run that did not use it. And it
is unsound: the response could contain `tool_use` blocks with *new* ids sitting in
`messages` alongside the stored ones, giving the provider two competing truths in
an array it correlates positionally.

**Determinism is scoped to one turn, and the UI must say so.** The tool id, the
tool input, and the `tool_use_id` of the approved call(s) are byte-identical to
what the human saw. Turns *after* the resumed one are sampled fresh. The honest
claim is "approving executes THIS call", never "approving executes only this
call". A contract needing the stronger property should use `max_tool_iterations`,
not this mechanism.

#### 3.3.4 What must NOT be skipped on resumption

| must still run | citation | why |
|---|---|---|
| input re-validation | `execution.py:281-286` | K7 schema drift; PERMANENT if it fails |
| principal scope intersection | `_principal_scope_for`, `:479-522` | an offboarded or descoped human must refuse |
| token mint | `:844` | recompiles authority at approval time |
| ordered token pipeline, every turn | `:877-885` | revocation, kill switch, scope, budget |
| `ToolProxy.invoke` gates | `tools/proxy.py:180-215`, `:253-330` | scope, call count, convergence, OAuth, permission, WIF, spend |

The pre-turn `validate_tool_call` runs on the resumed iteration with the same
`requested_token_cost=max_tokens` as any other turn, even though that turn does no
sampling. That is deliberate: the resumed turn is followed by a sampling turn in
the same run, so the headroom is genuinely needed, and a uniform check is the one
that cannot drift from the ordinary path.

### 3.4 The reviewer surface (resolves D3)

Two deferral shapes will coexist on one queue permanently, and showing them
identically is the honesty gap this work exists to close. `HitlItemResponse`
(`edge/routes/hitl.py:40-52`) gains two additive fields, and `_summary`
(`:190-205`) gains one branch:

```python
approval_semantics: Literal["rerun_request", "resume_action"]
pending_tool_calls: list[dict[str, Any]] | None   # {tool_use_id, tool_name, tool_input}
```

* `resumption_json is None` -> `"rerun_request"`, `pending_tool_calls=None`,
  `request_input` as today. The copy this shape needs is "re-run this agent on
  this input; the model may decide differently" — which is what
  `edge/routes/hitl.py:49` already describes, now said out loud.
* `resumption_json` present -> `"resume_action"`, `pending_tool_calls` rendered
  from the final hydrated assistant message's `tool_use` blocks. The copy is
  "execute exactly these calls", plus the two disclosures section 4.2 requires.

Both fields are additive on a response model, so no existing client breaks.

### 3.5 Id derivation with a per-suspension discriminator (resolves D7)

`app/decision_engine/events.py:61-68` gains an optional keyword on both helpers:

```python
def decision_id_for(proposal_id: UUID, *, discriminator: str | None = None) -> UUID:
    suffix = f":{discriminator}" if discriminator else ""
    return uuid5(_DECISION_NS, f"decision:{proposal_id}{suffix}")


def hitl_id_for(proposal_id: UUID, *, discriminator: str | None = None) -> UUID:
    suffix = f":{discriminator}" if discriminator else ""
    return uuid5(_DECISION_NS, f"hitl:{proposal_id}{suffix}")
```

With no discriminator the uuid5 input is character-for-character what it is today,
so every existing id — including every id already written to `decisions` and
`hitl_queue` — is unchanged. The mid-loop gate passes `discriminator=f"turn:{iteration}"`,
which is unique within a run because a loop index is, and stable across retries of
the same approval because it is stored in the snapshot.

Per CORRECTION 1 in section 1.5, this is defence in depth against a control-flow
invariant, not a fix for a reachable collision. Per CORRECTION 2, it does not
collide with `fix/hitl-id-single-mint`, which is merged and touched only the OPA
package's separate derivation.

---

## 4. Multi-call and rejection semantics (owner decision D4, CONFIRMED)

### 4.1 Per-turn approval, per-tool declaration

**One ticket per gated turn. One verdict. Every call in that turn resumes
together or none does.** A turn that mixes a gated call A with an ungated call B
produces one ticket; approving it dispatches both, rejecting it dispatches
neither.

The reasoning, in order of weight:

1. **A turn is the unit the model reasoned about.** The blocks in one assistant
   message were sampled together under one plan. Executing some and not others
   hands the model a `tool_result` set it never contemplated, and the next turn's
   reasoning is conditioned on a state no plan produced. Partial execution of a
   jointly sampled turn is a correctness hazard dressed up as a governance
   feature.
2. **The schema says so.** One `status` (`0001_initial_schema.py:212-214`), one
   `verdict_json`, one claim (`app/hitl/service.py:164-168`). Per-call verdicts
   need a child table, a per-call claim, and a re-definition of what
   `status='approved'` means when two children disagree.
3. **Dispatching B immediately and holding A would be worse than either
   extreme**, because it splits one turn's side effects across a human review
   window.

Owner decision D4 confirms this and rules per-call partial approval out of scope
for this pass. `HitlVerdictRequest` (`edge/routes/hitl.py:35-37`) stays
single-valued; no schema change.

### 4.2 Rejection

**Reject terminates the whole run.** No tool in the gated turn dispatches, no
deliverable is created, the run does not continue. `reject` already requires no
envelope (`require_request=False`, `app/hitl/service.py:306`) and executes
nothing, so the service needs no change.

Two things the reviewer UI must disclose, because the code cannot make them
untrue:

* **Earlier turns' side effects are not undone.** If iteration 0 called a mutating
  tool and iteration 1 was gated and rejected, the iteration-0 effect stands.
  There is no compensation mechanism in the codebase and this design does not
  invent one. Rejecting stops what has not happened; it does not reverse what has.
* **The sampled-but-rejected turn was billed.** Per section 2.1. The
  `ai_cost_ledger` row is correct and stays.

### 4.3 An approval that defers again — a loop this design has to close

**Surfaced during implementation, and recorded here because it is a new state
this work creates rather than one it inherits.** Before the mid-loop gate,
`execute()` could not raise `AgentDeferredToHuman` on the approval path at all:
the stage-2.5 gate is skipped whenever a `HitlApprovalContext` is present
(`execution.py:302`). It can now. A request-level ticket, once approved,
re-samples; if that sampling asks for an `approval`-declaring tool, the mid-loop
gate fires and files a second ticket. `infrastructure_executor` is exactly this
shape in production today — `FIRST_EXTERNAL_LAUNCH` plus a tool loop
(`contracts/mvp/infrastructure.py:53,62`) — so this is a realistic combination,
not a corner case.

Left to the pre-existing handling it would have fallen into
`HitlQueueService.approve`'s catch-all transient branch
(`app/hitl/service.py:234-246`), which **releases the row back to `pending`**.
That produces an unbounded loop: approve, re-sample, defer, released to pending,
approve again — with a fresh ticket filed on every pass. It is the
`pending -> approve -> fail -> pending` loop the module already eliminated for
input drift, made worse by the ticket growth.

**Resolution: `HitlDeferredAgain`, and the row is NOT released.** The claim
already set `status='approved'`, and that status stands, because it is true —
the human's verdict on that row was applied and the run really did execute. The
row is therefore no longer claimable, so a second POST to it gets the existing
409. The new question lives on the new row, whose id the 202 response names. 202
is the same code `/agents/execute` uses to say "a human must decide this", so no
new vocabulary is invented. An audit record (`hitl.approve_deferred_again`)
carries both ids so the chain is traceable.

---

## 5. `tool_use.id` stability and the ledger identity

**Does the original `tool_use.id` survive a resumption?** Yes, by construction
rather than by luck. It is stored inside `resumption.messages`, and
`_to_anthropic_message` writes it back to the provider verbatim
(`anthropic_adapter.py:113-121`). Because the resumed iteration does not sample
(section 3.3.3), there is no `_normalize_anthropic_message` call (`:154`) to mint
a new one.

**Does that close the gap `spend_reservation_replay_semantics.md` section 7
identified?** Yes for the HITL resumption path, under three conditions that must
hold together — and no for the ordinary path.

That document's blocker B was "there is no stable identity for *this logical tool
call*", and its verdict on `tool_use.id` was that it "is not stable across the
retry it exists to protect", because "each approval attempt is a fresh LLM
sampling and mints fresh block ids". This design removes that cause: on a
resumption the reviewed turn is not sampled, so `(hitl_id, tool_use_id)` is stable
across every retry of the same approval.

The three conditions, all implemented here:

1. **`tool_use_id` is plumbed to the proxy.** It was not
   (`execution.py:967-975`). One keyword argument on `_invoke_tool`, one on
   `ToolProxy.invoke`, one field on `ToolContext` (`tools/base.py:49-72`),
   mirroring exactly how `hitl_id` was threaded.
2. **The key is `(hitl_id, tool_use_id)`, never `tool_use_id` alone.**
   `tool_use_id` is provider-scoped and carries no tenancy or ticket binding.
   Pairing it with `hitl_id` matches the existing `request_id_for(hitl_id, operation)`
   precedent (`app/gcp/actions.py:127-138`).
3. **The key is trusted only when the call came from a resumed turn.** A run that
   deferred at stage 2.5 and was approved still re-samples, so its `tool_use_id`
   values are freshly minted and unstable. `ToolContext` therefore carries
   `is_hitl_resumption`, set True only for the dispatch of the resumed iteration —
   not for the whole resumed run, because later turns in that run are sampled
   fresh and their ids are no more stable than any other run's.

To make condition 3 impossible to get wrong at the point of use, the derivation is
a method on `ToolContext` rather than three fields a handler must combine
correctly:

```python
def replay_key(self) -> UUID | None:
    """Retry-stable identity for THIS logical tool call, or None when there is
    none. None is the honest answer on every path except a resumed turn."""
    if not self.is_hitl_resumption or self.hitl_id is None or self.tool_use_id is None:
        return None
    return uuid5(_REPLAY_KEY_NS, f"{self.hitl_id}:{self.tool_use_id}")
```

**Ordinary path: unchanged and still unprotected.** Ordinary runs are never
replayed by the platform, so there is nothing for such a key to guard; the real
vector is a client HTTP retry needing a client-supplied idempotency key that
`/agents/execute` does not accept. Out of scope, and unchanged by this work.

**Therefore `fix/toolproxy-ledger-commit-accounting` can resume its `replay_key`
work on top of this, scoped to the resumed-turn path.** It must not adopt
`(hitl_id, tool_use_id)` as a universal key. Its Option 2 (a run-level
`uuid5(hitl_id, tool_id)` plus a once-per-run rule for spend-capable tools)
remains the correct fallback wherever `replay_key()` returns None — which is every
contract that keeps deferring at stage 2.5, and every ordinary run.

---

## 6. Migration and cutover (drain strategy CONFIRMED)

**Confirmed: dual behaviour, natural drain, 48h window.** Deploy with both shapes
supported. `resumption_json` is `NULL` for every existing row, which means
`resumption is None`, which means approval re-runs the agent from `input` exactly
as it does today. Rows enqueued before the deploy drain within
`_HITL_EXPIRY_HOURS = 48` (`execution.py:78`), after which the pre-deploy
population is gone.

`HitlReplayEnvelope` itself is unchanged by this design — the snapshot lives in
its own column — so the `extra="forbid"` forward-compatibility question does not
even arise for stored envelopes. The dual-behaviour branch is needed regardless of
migration, because the stage-2.5 gate is not going away (section 2.2) and will
keep producing `resumption is None` rows indefinitely. **There is no second code
path to remove later.**

**The migration-0015 expire-pending precedent is explicitly NOT used**, per the
owner's instruction and for the reason the previous revision gave: 0015 could
expire pending rows honestly because those rows were *already unreplayable* —
approval "could never execute them" (`0015_hitl_request_json.py:25-29`) — and the
observed count was 0. Neither holds here. Today's pending rows are perfectly
replayable under today's semantics, and expiring them would destroy queued human
work.

---

## 7. Tests

**Must keep passing unchanged** (they describe the stage-2.5 gate, which is not
changing):

* `tests/unit/test_agent_execute_gate.py:86` `test_governed_defer_writes_hitl_and_skips_llm`;
* `tests/integration/test_cowork_hitl_replay_pg.py:306-312` — envelope round-trip
  and "A defer means nothing ran";
* `tests/unit/test_hitl_service.py:286` — invalid `request_json` is PERMANENT.

**New coverage this design requires:**

| what | asserted property |
|---|---|
| suspension capture | exactly one `generate_with_tools` call happened, zero `ToolProxy.invoke` calls, one `hitl_queue` row, `resumption_json` holds the exact `tool_use` block(s) and the correct message prefix |
| verbatim resume | zero sampling calls for the resumed iteration; dispatched `tool_id` and `input_data` byte-identical to the stored block; `tool_use_id` reaching `ToolContext` equals the stored one |
| scoped determinism | a later turn in the resumed run samples normally |
| rejection | nothing dispatched, no deliverable, run terminated |
| per-turn atomicity | a two-call turn approves both or rejects both; never one |
| expiry | a row past `expires_at` cannot be claimed; approval raises `HitlExpired` (410) and nothing executes — it does NOT fall back to re-sampling |
| id discrimination | two suspensions deriving from the same `proposal_id` at different iterations produce different `hitl_id` and `decision_id`, and neither snapshot nor verdict clobbers the other |
| ledger identity | `replay_key()` is identical across two approval attempts of the same resumed call, and None on a stage-2.5 approval |
| pre-migration rows | `resumption_json IS NULL` approves and re-runs under old semantics, no error |
| authority is not frozen | a resumption after the principal was descoped refuses via `_PERMANENT_PRINCIPAL_ERRORS` (`app/hitl/service.py:81-85`) |
| corrupt snapshot | a snapshot whose stored ids disagree with its stored turn is PERMANENT, terminal, nothing executed |
| approval that defers again | 202 naming the new row; the original stays `approved`, so a second POST is a 409 rather than another re-run (section 4.3) |

**Gates.** `powershell -ExecutionPolicy Bypass -File scripts/ci_unit_gate.ps1` for
the unit job. The Postgres-backed integration tests must be confirmed to have RUN,
not skipped, before any claim about the queue, money, or tenancy — with
`SKYLIZE_TEST_APP_DB_URL` pointed at the non-superuser, non-table-owner
`skylize_app` role (`tests/integration/conftest.py:26-31`), because a superuser
bypasses RLS and the isolation assertions would prove nothing.

---

## 8. Decisions

### Confirmed by the owner, 2026-09-09

**D1 — a second, post-sampling HITL gate: ACCEPTED.** Approval becomes a
resumption of a specific sampled action rather than a re-sampling of the run. The
D1 property at `execution.py:292-293` ("no LLM call, no deliverable, no ledger
row" on defer) is knowingly given up **for contracts that reach the new gate**;
it is untouched for the stage-2.5 gate and for every contract that does not invoke
an `approval`-declaring tool. Rationale of record: the platform's core governance
claim — every action is HITL-escalatable *and* auditable — requires that approving
a reviewed action executes THAT action.

**D2 — a new durable store: ACCEPTED, minimally scoped.** Only the pending
`tool_use` block(s) and the message history up to the suspension point, with the
48h expiry. Treated as part of the existing HITL audit trail's retention
discipline. The attached "flag if the assumption is wrong" condition was checked
and does not trip at this commit; section 0.3 records the check and the condition
that would reopen it.

**D4 — per-turn, not per-call: ACCEPTED.** Approve resumes every pending call in
the turn; reject discards all. No per-call partial approval this pass. The verdict
surface stays single-valued; no schema change there.

**Drain strategy: ACCEPTED as recommended.** Dual behaviour, `resumption` absent
means today's semantics, natural drain inside the 48h window. The 0015
expire-pending precedent is explicitly not used.

### Resolved in this document

**D3 — how the reviewer UI distinguishes the two shapes: RESOLVED** (section 3.4).
Two additive response fields, `approval_semantics` and `pending_tool_calls`, and
one branch in `_summary`. Not owner-shaped: additive fields on a response model
with no behavioural or schema consequence, closing a disclosure gap the owner
already identified as the point of the work.

**D5 — may `request_json` be rewritten after a terminal verdict: RESOLVED as NO,
and the question is dissolved rather than answered** (sections 3.2.1, 3.2.5). The
snapshot lives in its own column, so `request_json`'s "written ONCE at enqueue and
never rewritten" invariant (`app/hitl/service.py:23`) — which `_terminate_failed`
(`:452-482`) reasons from — is preserved untouched. No post-verdict redaction step
is built either, because it would have to distinguish terminal paths from the
transient-failure release path (`:427-437`) and getting that wrong would silently
downgrade a verbatim resume into a re-sample.

**D6 — is `max_calls_per_run` per logical run or per attempt: RESOLVED as
per-attempt, documented, not changed** (section 3.3.4). `ToolCallCounter` keys on
`(correlation_id, agent_id, tool_id)` and is "in-process only"
(`tools/proxy.py:85-100`); `run_id` is fresh per attempt (`execution.py:301`). A
suspend/resume pair therefore gets two fresh counters, so a contract's declared
ceiling can be reached twice across one logical run. Not changed here, for two
reasons: it is pre-existing behaviour affecting every run rather than something
this design introduces, and "seeding" an in-process counter from the envelope
would suggest a durability the counter cannot deliver across processes. Storing
per-tool counts would also exceed the "smallest snapshot resumption mechanically
requires" scope of owner decision D2. Recorded here so the next person to touch
the ceiling has the evidence.

**D7 — `hitl_id` derivation for a mid-loop gate: RESOLVED** (sections 1.5, 3.5).
An optional `discriminator` keyword on both helpers, leaving every existing id
byte-identical; `turn:{iteration}` for the mid-loop gate. Two corrections to the
previous revision are recorded in section 1.5: the collision is not reachable at
this commit, and `fix/hitl-id-single-mint` is merged and touched only the OPA
package.

---

## 9. What this document does not cover

* **No per-call partial approval.** Deliberately (section 4.1). If the product
  later requires it, that is a separate design pass with a child-table schema
  change of its own.
* **No ordinary-path idempotency.** Out of scope and unchanged (section 5).
* **No compensation for side effects of earlier turns.** None exists in the
  codebase and none is invented (section 4.2).
* **No decision on the OPA-side path.** `decision_engine/resume.py` publishes
  terminal events for a human verdict and executes nothing — it holds no
  `EvaluationPipeline` and no execution service. Whether an OPA-engine deployment
  ever needs resumption is a separate question, gated behind
  `SKYLIZE_DECISION_ENGINE="opa"`, which ADR-0004 keeps unenablable.
* **No figures taken from `docs/REPO_STATE.md`.** Every number and citation here
  was read from the tree at `95c3e79`.
