# Spend-reservation replay semantics — state enumeration and open decisions

Status: **DESIGN NOTE / BLOCKED ON OWNER DECISIONS.** No replay behaviour is
implemented. This note records what the code actually does today, so the decision
is made against the real state model rather than an assumed one.

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
