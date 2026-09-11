# Spend-reservation replay semantics — state enumeration and open decisions

Status: **RESOLVED AND IMPLEMENTED** (see sections 7 and 8). This began as a
design note recording a blockage, and sections 1-6 are kept as the evidence the
decision was made on rather than rewritten after the fact. The blockers they
enumerate are now closed; each carries a RESOLVED marker saying how.

The one thing that changed everything: **section 3B's missing call identity now
exists**. `feat/hitl-approval-resumption` made an approval REPLAY the reviewed
turn verbatim instead of re-sampling it -- option 1 of section 7 -- and that made
`tool_use.id` stable across an approval retry. `ToolContext.replay_key()`
(`src/skylize/tools/base.py:88`) is built on it.

Context: `03e7081` fixed the reserve CTE's phantom-hold leak and `ee34bd2` made
`ToolProxy` settle for actual spend. Neither closes the HITL replay double-count
described at `src/skylize/tools/proxy.py:844-866`. This note is the design pass
for that third defect.

## 1. The reservation state set is FOUR states, not three

`src/skylize/app/principal/models.py:238`

```python
state: Literal["held", "committed", "released", "expired"]
```

Confirmed independently against the live database, which carries the same set as
a CHECK constraint on `spend_reservation`:

```
spend_reservation_state_check
  CHECK (state = ANY (ARRAY['held','committed','released','expired']))
```

| State | Written by | What it tells you about the real-world side effect |
|---|---|---|
| `held` | `try_reserve` (`spend.py:342`) | In flight. Unknown. |
| `committed` | `commit` (`spend.py:358-361`) | It happened, and `committed_minor` is what it cost. |
| `released` | `release` (`spend.py:392-393`) | **Unknown — see §2.** |
| `expired` | `sweep_expired` (`spend.py:446-457`) | Unknown. The worker died holding it. |

`expired` is a real, reachable, terminal state and it was absent from the brief.
It is not a variant of `released`: `released` is written by a live process that
chose to release, `expired` is written by a sweeper on behalf of a process that
never came back. Any state-differentiated replay rule has to answer for it.

## 2. `released` does NOT mean "nothing happened"

The brief's premise for the `released` case — *"released means 'nothing
happened,' not 'something happened for $0'"* — does not hold against the code.

`ToolProxy` releases the hold on **two** paths, and only one of them implies the
side effect did not occur:

- `proxy.py:388-399` — the handler raised `ToolPermissionDenied`. Refused; almost
  certainly nothing happened.
- `proxy.py:400-411` — the handler raised **anything else**. The comment there is
  explicit that this exists so a failing tool does not leak budget for 15
  minutes. It is not an assertion that the tool did nothing. A handler that
  completed its provider call and then threw while parsing the response takes
  this path, and the hold is released for a spend that really occurred.

So "re-attempt fresh on `released`" is *usually* right, but the ledger alone
cannot know that. It is safe only because the handler deduplicates provider-side
on `ctx.hitl_id` (`tools/base.py:60-72`). That makes the provider, not the
ledger, the authority on whether a retry moves money a second time — which is
worth stating explicitly before building ledger rules that assume otherwise.

## 3. Four structural blockers

### A. The idempotency key is consumed permanently, across all states

```
CREATE UNIQUE INDEX spend_reservation_org_id_idempotency_key_key
    ON public.spend_reservation USING btree (org_id, idempotency_key)
```

Unconditional — it does not exclude terminal states. Once a key has been used it
can never back a second reservation, in any state. So the desired `released` →
"re-attempt fresh" behaviour **cannot be expressed with a stable
hitl_id-derived key at all**: the retry has nowhere to put its new hold.

Reviving the row in place (`released` → `held`) is not an option worth taking. It
destroys the historical record the audit trail is reconciled against, and it
races `sweep_expired`, which selects on `state = 'held'` (`spend.py:457`).

The clean expression of the intended rule is a **partial** unique index —
at most one live-or-settled reservation per replay key:

```sql
UNIQUE (org_id, replay_key) WHERE replay_key IS NOT NULL
                              AND state IN ('held', 'committed')
```

`held` and `committed` block a replay; `released` and `expired` free the key for
a genuine re-attempt. That is exactly the semantics wanted, enforced by the
database rather than by application logic that can be forgotten. **It requires a
migration**: a new nullable `replay_key` column plus that index. `idempotency_key`
would keep its current meaning and its current unconditional index.

> **RESOLVED -- migration 0028.** Built as described: a nullable `replay_key`
> column plus
> `UNIQUE (org_id, replay_key) WHERE replay_key IS NOT NULL AND state IN ('held','committed')`.
> Verified against the live database. Note it is a partial unique INDEX, not a
> table constraint: Postgres constraints cannot carry a `WHERE`. The same table
> already used that pattern for `spend_reservation_sweep ... WHERE (state = 'held')`.
>
> **A trap this section predicted, then walked into anyway.** The obvious wiring
> -- derive `idempotency_key` from `replay_key` -- reintroduces exactly the
> problem described above. The unconditional index still spans all four states, so
> a re-attempt after a `released` or `expired` predecessor collides with the
> settled row, and `try_reserve`'s re-read branch hands that SETTLED reservation
> back as though it were a fresh hold. `commit` matches only `state = 'held'`, so
> the re-attempt settles to nothing and UNDER-counts a real spend. So
> `idempotency_key` stays unique per ATTEMPT and `replay_key` carries the replay
> identity in its own column; the two answer different questions and must not be
> collapsed. Caught by `tests/integration/test_tool_proxy_replay_states_pg.py`,
> not by review.

### B. There is no stable identity for "this logical tool call"

A single agent run makes many tool calls. `app/agents/execution.py:933-940`
dispatches every `tool_use` block in a response, inside the tool loop, and one
`hitl_id` covers the entire replayed run.

So `hitl_id` alone is not a usable reservation key — two legitimately distinct
spends in one run would collide on it, and the second would settle against the
first's hold. This is precisely the hazard the existing comment at
`proxy.py:849-853` warns about.

The obvious discriminators do not survive scrutiny:

- **Call ordinal** — stable only if the LLM emits identical calls in identical
  order on replay. It does not guarantee that.
- **Input hash** — stable for identical inputs, but two legitimately identical
  calls (refund the same amount twice) collide, and collapsing them silently
  loses a real spend.

A correct key needs a call identity that is both stable across replays and
distinct across calls. Nothing in the current execution path produces one. This
is the core unsolved problem, and it is an architecture decision, not wiring.

> **RESOLVED -- `ToolContext.replay_key()`** (`src/skylize/tools/base.py:88`).
> The architecture decision was taken: approval now resumes the reviewed turn
> instead of re-sampling it, so a `tool_use` id read back from storage IS stable
> across retries. The key is `uuid5(_REPLAY_KEY_NS, f"{hitl_id}:{tool_use_id}")`,
> returned only when all three of `is_hitl_resumption`, `hitl_id` and
> `tool_use_id` are present and `None` everywhere else -- which is most calls.
> `hitl_id` binds it to a ticket and a tenant; `tool_use_id` distinguishes the
> calls within that turn, which is what this section said was missing.

### C. Nothing stores the original result for a `committed` replay

The brief requires a `committed` replay to "return the recorded actual amount /
result from the original commit". The ledger stores `committed_minor` — the
amount — but never the tool's output.

The audit record is the only place the output is written
(`proxy.py:421-431`, `outputs=output.model_dump(mode="json")`), and it is not
addressable for this purpose: `_audit_call` (`proxy.py:922-945`) records
`correlation_id` and no `hitl_id`, and `HitlQueueService.approve` mints a fresh
correlation on every approval attempt (`app/hitl/service.py:234`). A replay
therefore cannot find its predecessor's output.

Returning a prior result idempotently needs a result store keyed by whatever §B
settles on. That is a second migration and a second subsystem.

> **RESOLVED -- `spend_reservation.result_snapshot`** (migration 0028). Not a
> second subsystem in the end: a JSONB column on the row whose `state` already
> decides the replay, written at commit and read instead of re-executing.
>
> Deliberately NOT 0027's `hitl_queue.resumption_json`, which was checked first
> precisely to avoid building a parallel mechanism. The two hold opposite halves
> of a call and have different lifetimes: `resumption_json` is the PRE-execution
> action (message prefix, pending `tool_use` ids), bounded by the ticket's 48h
> `expires_at` and rejected once expired; `result_snapshot` is the POST-execution
> result and must OUTLIVE the ticket, because a replay arriving after expiry still
> must not re-execute a committed spend.
>
> `runtime/exec_fingerprint.py`'s `DedupCache` was also considered and rejected:
> content-addressed on `(org_id, tool_name, args)` with a 60s TTL, and wired
> nowhere in production. Content-addressing is option 3 below, rejected there.
>
> A `committed` row with NO snapshot refuses rather than falling through: "we
> cannot produce the original result" must never collapse into "there was no
> original" and re-run a spend that already happened.

### D. `ReservationConflict` is raised but caught nowhere

`errors.py:116` defines it; `spend.py:317` raises it when a repeated key arrives
with a different amount. No handler exists anywhere in the tree.

`_reserve_spend` catches only `CeilingExceeded` and `EnvelopeNotFound`
(`proxy.py:875`, `proxy.py:896`), so a `ReservationConflict` would escape
`ToolProxy.invoke` as a bare `BudgetError`. Callers branch on `ToolError`
(`execution.py:980`), so it would bypass tool-error handling and fault the agent
run.

Latent today only because the proxy never repeats a key. It goes live with the
first caller-supplied key. This is the abandoned half-built path the
re-read branch at `spend.py:314-332` was written to serve.

> **RESOLVED -- `ToolSpendKeyConflict`** (`src/skylize/tools/base.py`), mapped in
> `_reserve_spend`. Its own branch with `failed_stage="reservation"`, deliberately
> NOT a `ToolSpendDenied`: a key collision and an exhausted ceiling are unrelated
> conditions with unrelated remedies, and subclassing would hand it a
> `defer_to_human` flag that could route a caller-side idempotency fault into a
> human approval queue as though it were an overspend. It IS a `ToolError`, which
> is the substance of the fix -- that is what stops it faulting the agent run. The
> containment auto-hook does not fire for it.

## 4. `hitl_id` is not universally available

Asked directly by the brief. It is not.

`ToolContext.hitl_id` is documented as "None on an ordinary (non-deferred)
request path" (`tools/base.py:66-67`), and the value threads down from the agent
loop (`execution.py:935-939`), which receives `None` for any run that is not a
HITL replay.

So an hitl_id-derived replay key protects **only** HITL-escalated calls. A
spend-gated tool invoked on the ordinary path gets no replay protection from it
and needs its own strategy — or an explicit decision that it does not get one,
justified by the fact that ordinary runs have no automatic retry loop
(the release-to-pending retry at `app/hitl/service.py:240-246` is HITL-specific).

> **RESOLVED -- HITL-only is the accepted limit**, on the justification above.
> `replay_key()` returns `None` on the ordinary path and the reservation gets a
> fresh per-attempt `idempotency_key`, which is the pre-existing behaviour and is
> correct there: no platform replay exists for a stable key to protect against.
> The real ordinary-path vector is a CLIENT retrying the HTTP request, which needs
> a client-supplied request idempotency key that `/agents/execute` does not
> accept. That remains separate, open work.

## 5. What needs deciding before any of this is built

1. **Call identity (§B).** What makes two tool invocations "the same logical
   spend" across a replay? Until this is answered nothing else can be keyed.
2. **Migration shape (§A).** Approve the `replay_key` column + partial unique
   index, or choose a different mechanism.
3. **Result store (§C).** Where does a completed call's output live so a replay
   can return it without re-executing?
4. **Ordinary-path scope (§4).** Do non-HITL spend calls get replay protection,
   or is HITL-only an accepted limit?
5. **`expired` disposition (§1).** Treat as `released` and allow a fresh attempt,
   or treat as unknown-and-escalate? A swept hold means a worker died mid-call;
   the side effect status is genuinely unknown.

Items 1–3 are each a migration or a subsystem. None should be chosen by whoever
happens to write the patch.

> **ALL FIVE DECIDED.** 1 -- `ToolContext.replay_key()`, via option 1 in section
> 7. 2 -- the `replay_key` column plus the partial unique index, migration 0028.
> 3 -- `spend_reservation.result_snapshot`, same migration. 4 -- HITL-only is the
> accepted limit (section 4). 5 -- `expired` is treated as `released`: the key is
> freed and a re-attempt spends for real, because settling it to zero would
> under-count a spend that then succeeds, and the handler remains the authority on
> provider-side deduplication.

## 6. What IS safe to state now

Independent of the decisions above:

- `held` on replay must never re-execute and never place a second hold. Note
  there is no in-flight concurrency guard in `ToolProxy` today for
  same-key calls — `ToolCallCounter` (`proxy.py:82`) limits calls per run, which
  is a different concern.
- `committed` on replay must never re-execute and never re-commit.
- `released` and `expired` on replay must be able to place a real hold and spend
  for real. Settling them to zero, or reusing the settled row, would under-count
  a spend that subsequently succeeds — worse than the double-count being fixed.

These three are not in dispute. What blocks them is that none can be *keyed*
until §B is answered.

> **IMPLEMENTED.** Section 3B is answered, so all three are now enforced rather
> than merely agreed. `held` and `committed` are blocked by
> `ToolProxy._replay_guard` and, underneath it, by migration 0028's partial unique
> index; `released` and `expired` fall through to a real reservation because that
> same index does not cover them, so `find_by_replay_key` cannot see them. Each
> state has a live-Postgres test in
> `tests/integration/test_tool_proxy_replay_states_pg.py`.
>
> The in-flight concurrency gap noted above closes as a side effect: a second
> same-key call now meets either the guard or the unique index.

## 7. Call identity: `tool_use.id` -- rejected, then MADE valid by option 1

Proposed as the answer to §B: key the reservation on `(hitl_id, tool_use.id)`,
where `tool_use.id` is the Anthropic Messages API's per-tool-call block id.
**Verified and rejected — it is not stable across the retry it exists to
protect.**

> **OUTCOME: option 1 was chosen and shipped** (`feat/hitl-approval-resumption`,
> merged to main). Approval no longer re-samples the run; it replays the reviewed
> turn verbatim from `hitl_queue.resumption_json` (migration 0027). The block id is
> therefore READ FROM STORAGE rather than re-minted, which is precisely the
> property the analysis below found missing. `ToolContext.replay_key()` combines it
> with `hitl_id` and an explicit `is_hitl_resumption` flag and returns `None`
> unless all three hold, so the key exists only on the one path where it is
> trustworthy.
>
> The rejection below was correct on the code as it stood, and is kept in full
> because its reasoning is what identified the only change that could make the
> idea work. Read it as "why this is true only on a resumed turn", not as a live
> objection.

### Why it fails

A HITL approval does not resume a suspended tool call. It **re-executes the
agent run from its stored input**:

- `HitlReplayEnvelope` (`schemas/hitl.py:56-63`) stores `agent_id`, `input`,
  `user_id`, `correlation_id`, `on_behalf_of_principal`. There is no tool call,
  no message history, and no `tool_use` block in it.
- `HitlQueueService.approve` calls
  `self._execution.execute(agent_id=envelope.agent_id, input_data=envelope.input, ...)`
  (`app/hitl/service.py:187-204`).
- `execute` mints a fresh `run_id` (`app/agents/execution.py:301`), rebuilds the
  prompts (`:310-311`), and runs the full tool loop (`:315-325`).
- Every `tool_use_id` is taken straight off the new API response —
  `adapters/llm/anthropic_adapter.py:154`, `tool_use_id=raw_block.id`.

So each approval attempt is a fresh LLM sampling and mints fresh block ids. The
codebase already says so, at the exact place the id would have to come from
(`app/agents/execution.py:320-324`):

> The approval's ticket id, when this run IS a HITL replay. This is the ONLY
> value on this path that is stable across retries of the same approval.

The failure is worse than instability. Because the model re-samples, a replay may
produce a **different set of tool calls entirely** — different count, order, or
arguments. There is no per-call thing to key on because the run genuinely
re-decides what to do. This is deliberate and disclosed, not a bug: the review UI
shows the human "WHAT WOULD EXECUTE if approved (the replay envelope's input)"
(`edge/routes/hitl.py:49`). Approval means "re-run this agent on this input", not
"perform this exact call".

### What IS stable across an approval retry

Frozen in `request_json`, which is "written once at enqueue and never rewritten"
(`app/hitl/service.py:175-176`):

| Value | Source |
|---|---|
| `hitl_id` | `row.hitl_id` (`service.py:193`) |
| `decision_id` | `row.decision_id` (`service.py:194`) |
| `original_correlation_id` | `envelope.correlation_id` (`service.py:195`) |
| `agent_id`, `input`, `user_id`, `on_behalf_of_principal` | the envelope |

Not stable: `run_id` (`execution.py:301`), the per-attempt `fresh_correlation`
(`service.py:234`), `tool_use.id`, and the model output generally.

**Every stable value is run-level. Nothing at tool-call granularity survives a
retry**, because tool calls are re-sampled rather than replayed.

### The plumbing question, answered separately

`tool_use_id` also never reaches the proxy today. `execution.py:978,983` uses it
only to build the `tool_result` block; the `invoke` call at `execution.py:967-975`
passes `tool_id`, `input_data`, `governance_token`, `contract`, `org_id`,
`correlation_id`, `hitl_id` — no `tool_use_id`. That gap is about one line, and
closing it would not help, per the above.

### Ordinary-path scope (question 4), re-answered

The hoped-for graceful degradation to `(None, tool_call_id)` **does not hold**,
for the opposite reason: ordinary runs are never re-executed by the platform.
The release-to-pending retry loop (`app/hitl/service.py:240-246`) is HITL-only,
and no edge route carries a request idempotency key — the only match in
`edge/` is the HITL verdict's 409 (`edge/routes/hitl.py:129`).

So on the ordinary path there is no platform replay to protect against, and a
`tool_use.id`-derived key would guard nothing. The real ordinary-path vector is a
**client retrying the HTTP request**, which re-runs the agent and re-samples the
model — so it needs a *client-supplied* request idempotency key, which
`/agents/execute` does not currently accept. That is a separate piece of work.

### Options that remain

1. **Make replay a resumption instead of a re-sampling.** Persist the approved
   tool call (or the message history) in `HitlReplayEnvelope` at defer time and
   execute *that* on approve. Then `tool_use.id` is stable because it is stored
   rather than re-minted, and the owner's chosen identity works as intended. This
   also closes the gap where an approved run can execute different actions than
   the ones reviewed — worth weighing on its own merits, not only for spend.
   Largest change; the only one that makes replay deterministic in general.
2. **Run-level, enforced:** key on `uuid5(hitl_id, tool_id)` plus an explicit
   rule that a spend-capable tool may be invoked at most once per approved run.
   A second spend call to the same tool in one replay is refused as a policy
   violation rather than silently keyed onto the first. Crude and over-restrictive
   (it blocks a legitimate two-refunds-in-one-run), but it never double-counts
   and never misattributes, and it needs no architecture change.
3. **Content-addressed:** `uuid5(hitl_id, tool_id, canonical(input), occurrence)`.
   Rejected unless someone argues for it: when the model *does* re-sample
   differently, this silently maps a different action onto a prior reservation,
   which is worse than the double-count being fixed.

Option 1 or 2. Not 3.

> **Option 1 was taken**, and on its own merits as much as for spend: it also
> closes the gap where an approved run could execute different actions than the
> ones a human reviewed. Option 2's once-per-run rule proved unnecessary and would
> have blocked a legitimate two-refunds-in-one-run. Option 3 stays rejected.

## 8. What was built

| Section | Blocker | Closed by |
|---|---|---|
| 3B | no stable call identity | `ToolContext.replay_key()` (`tools/base.py:88`), via HITL resumption |
| 3A | idempotency key consumed across all four states | migration 0028: `replay_key` column + partial unique index over `held`/`committed` |
| 3C | no store for a committed replay's result | migration 0028: `spend_reservation.result_snapshot` |
| 3D | `ReservationConflict` caught nowhere | `ToolSpendKeyConflict`, mapped in `_reserve_spend` |
| 6 | the three rules could not be keyed | `ToolProxy._replay_guard` + the partial index |

End to end: a resumed HITL turn derives a `replay_key`; a `held` or `committed`
predecessor stops the call before dispatch, returning the recorded result in the
`committed` case; a `released` or `expired` predecessor is invisible to the
lookup, so the call reserves and spends for real. Ordinary non-HITL calls derive
no key and are unaffected.

What deliberately did NOT change: `idempotency_key` keeps its meaning and its
unconditional index (see the trap in 3A), ordinary-path replay protection stays
out of scope (section 4), and `sweep_expired`'s missing RLS GUC is a separate
defect owned by `fix/sweep-expired-rls-org-binding`.
