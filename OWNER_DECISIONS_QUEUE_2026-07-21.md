# Owner Decisions Queue — 2026-07-21

Items requiring **Mr. Özkan's** judgment. Nothing here was decided by the session.
Each entry states: the decision, why it could not be made autonomously, options with
consequences, and a **recommendation** (a recommendation — *not* a decision taken).

Every code reference below was read at commit `85e7990f` (branch `feat/durable-governance`)
before it was written down. Where a claim from an earlier document could not be
re-confirmed against current code, it is marked as such rather than repeated.

**Carried-forward status from `OWNER_DECISIONS_QUEUE_2026-07-19.md`:**
- Its **S1** (ADR-0005 department vocabulary) is **RESOLVED** — verified merged, not assumed.
  The explicit table is live at `src/skylize/decision_engine/constants.py:28-44`.
- Its **S2** (policy_inputs.md missing) is **PARTIALLY RESOLVED** — the file now exists on
  disk but is untracked and misnamed. See **D1** below; this is urgent.

---

## D1 — `policy_inputs.md` is present but UNTRACKED and MISNAMED — one `git clean` from deletion

**Decision:** Where this file should live in git, and under what name.

**State found (verified, not inferred):**
- `docs/04_decision_engine/policy_inputs (1).md` — 23,775 bytes, modified 2026-07-19 16:13.
- `git ls-files docs/04_decision_engine` returns six files; **this is not one of them.**
  It shows in `git status` only as `?? "docs/04_decision_engine/policy_inputs (1).md"`.
- The `(1)` suffix is the signature of a browser download landing beside an existing name.

**Why this is urgent, not cosmetic:** an untracked file is destroyed by `git clean -fd`,
is invisible to every other worktree (27 exist), and is absent from every branch. This
document is the gate on all real Rego authoring. Losing it loses the gate.

**Why not autonomous:** the session instruction forbids rename-guessing this file into
place. Renaming is *probably* right — but "probably" is how the unsourced-label incident
started, and this file's whole purpose is to stop values entering code without a traceable
owner-approved source. It should enter git by your hand, not by inference.

**Options:**
- **A (recommended):** `git mv` it to `docs/04_decision_engine/policy_inputs.md` and commit.
  This is the path `ADR-0004` refers to. **Cost:** none, if that is the intended name.
- **B:** keep the current name and track it as-is. **Cost:** every existing reference to
  `policy_inputs.md` becomes a dangling reference.
- **C:** leave untracked. **Cost:** unacceptable — one routine cleanup deletes it.

**Recommendation (NOT a decision):** Option A. Two commands, and it removes a
data-loss risk that currently sits on the single most decision-bearing document in
the repository.

---

## D2 — The `[OWNER-DECISION-REQUIRED]` values inside `policy_inputs (1).md` (hard stop S1)

**Decision:** every value the document marks as yours.

**State (read from the file, which is explicitly DRAFT):** its banner reads
`Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)` (line 3), and
*"Nothing here is `[APPROVED]` until the owner changes the banner at the top of each
section"* (line 19). **No section reads `[APPROVED]`.** Sections still marked
`[OWNER-DECISION-REQUIRED]`:

| § | Subject | What is open |
|---|---|---|
| 0.1 | Authority / delegation-of-authority matrix | dollar thresholds + level assignments (line 48, 116) |
| 0.2 | Spend ceilings | every dollar figure; **over-ceiling behaviour `defer_to_human` vs `hard_deny`** (line 151); currency model (line 157) |
| 0.3 | External action | tier assignments; **graduated-autonomy N** (line 207) |
| 0.4 | Brand / legal | the REJECT vs ASK-HUMAN split across a 36-category taxonomy (line 215) |
| 0.5 | Security veto | the one-line policy stance (line 255) |
| 0.7 | Policy version schema | hybrid model confirmation (line 350) |

§0.6 (Data Access) is marked `[CODE-VERIFIED]` and needs framework confirmation only.

**Why not autonomous:** hard stop S1, and the document's own rule at line 19-20.

**Recommendation (NOT a decision):** approve section-by-section rather than all at once.
§0.5's single line is the cheapest and unblocks the most: it is one sentence, and the
recommendation already written into the file is *absolute, deny-overrides, fail-closed* —
which is what the code already implements unconditionally.

---

## D3 — Real Rego policy content (hard stop S2)

**Decision:** authoring any `.rego` rule that can produce `allow := true`.

**Why not autonomous:** hard stop S2 — the hardest in the list. Blocked until the relevant
section of `policy_inputs.md` reads `[APPROVED]`, which today none does (see D2).

**No action was taken.** The fail-closed placeholder bundle was left exactly as it is.

**Recommendation (NOT a decision):** none possible — this is definitionally yours. It
unblocks only after D2.

---

## D4 — `lint-imports` fails on this branch: where do process entrypoints live?

**This one is new, and it blocks merging `feat/durable-governance` into `main`.**

**State (verified by running the real console script, not the module no-op):**
`lint-imports` **exits 1**. The broken contract is
*"Application logic contains no SQL (depends on dal ports only)"*
(`pyproject.toml:170-174`), violated by three import chains, all rooted in one module:

```
skylize.app.orchestrator.temporal.worker -> skylize.bootstrap        (worker.py:32)
  skylize.bootstrap -> skylize.dal.connection  -> asyncpg
  skylize.bootstrap -> skylize.dal.repositories
skylize.app.orchestrator.temporal.worker -> skylize.dal.workflows    (worker.py:34)
```

- CI runs this exact command (`.github/workflows/ci.yml:25`, `deploy-staging.yml:39`), so
  **CI is red on this branch.**
- It is **not** red on `main`: `src/skylize/app/orchestrator/temporal/worker.py` does not
  exist on `main` (verified with `git cat-file -e main:…`). It arrived in commit
  `1ef9281d` (2026-07-15) on this line of development.

**The actual question:** `worker.py` is a *process entrypoint* — its job is to build the
composition root and run. The contract's own comment (`pyproject.toml:153-157`) already
says entrypoints are allowed to construct concrete adapters, naming `bootstrap` and `edge`
as the sanctioned places. The new Temporal worker does exactly that legitimate thing, but
it sits *inside* `skylize.app`, which is the subtree the contract polices.

**Why not autonomous:** this is a layering decision — "where does a process entrypoint
belong?" — not a stale allowlist entry. The session brief permits fixing only unambiguous,
tool-prescribed allowlist staleness.

**Options:**
- **A (recommended):** move the entrypoint out of `skylize.app` to sit beside the other
  sanctioned entrypoints. **Cost:** the documented run command changes
  (`python -m skylize.app.orchestrator.temporal.worker`, worker.py:6); one module moves.
  **Benefit:** the contract keeps its full strength and the codebase gains one consistent
  answer for where entrypoints live.
- **B:** add an import-linter exemption for this module. **Cost:** the "app holds no SQL"
  contract now has a hole that future modules can widen by precedent; the exemption list
  becomes the thing to maintain. **Benefit:** one line, nothing moves.
- **C:** leave CI red. **Cost:** unacceptable — it masks every future violation.

**Recommendation (NOT a decision):** Option A, because `pyproject.toml:153-157` already
documents the intended pattern and A makes the code match the documented intent rather
than amending the intent to match the code. But B is defensible and much cheaper, and the
choice is genuinely yours.

---

## D5 — A live-fed security veto needs a new entry in the department vocabulary table

**Decision:** whether the decision engine should learn a security/safety verdict event type.

**State (verified):** the safety veto machinery exists and is unit-tested, but nothing
feeds it in production. The department vocabulary table
(`src/skylize/decision_engine/constants.py:28-44`) declares exactly three departments and
four event types:

```
creative   -> creative.review_requested
growth     -> sales.campaign_proposed, sales.budget_reallocation_proposed
governance -> governance.human_approval_received
```

**None of them carries a security or safety verdict.** Feeding the veto through an event
therefore requires adding a department and/or event type to this table — and that same
file states at lines 25-27 that *"Adding a department here is an explicit, reviewable
governance decision."*

**Why not autonomous:** it is a vocabulary decision of exactly the kind ADR-0005 was
written to stop being made implicitly. The alternative (a synchronous inline safety call)
is architecturally forbidden: bootstrap deliberately withholds the LLM gateway from the
evaluator so business-authz and LLM-content-safety stay separate, and the evaluator is
contractually deterministic for replay.

**Three further findings that change the shape of this decision. All verified:**

1. **There is no runtime producer of `SafetyVerdictOut` at all.** Across `src/` it appears
   only as a class definition, an import, and four `output_schema` strings. The single
   assignment to `security_verdict=` anywhere in the repository is in a test
   (`tests/unit/test_decision_evaluator.py:68`).
2. **The event envelopes cannot carry a verdict even if one existed.** All three proposal
   payloads and `BaseEvent` are `extra="forbid"`, and `EventCategory` is a closed
   taxonomy with no security or safety member. So "embed the verdict on the business
   event" is not a small change — it is a schema change to every proposal event.
3. **The veto does not exist in the engine the flag would activate.** See D6.

**Recommendation (NOT a decision):** do not build this bridge until D6 is settled, because
D6 changes what the bridge should even connect to. If it is built, the
new-security-event shape is the only one of the three that does not require a schema
change to existing events — but it is precisely the one that needs your vocabulary
sign-off.

---

## D6 — The absolute safety veto DOES NOT EXIST in the OPA engine — it would be lost at the flag flip

**This contradicts a premise the session was given, and it is the most serious finding of
the night.**

**The premise:** that the safety veto is fully built (carrier + unconditional stage-0
check + `rule_applied` enum) and merely lacks a live producer.

**What is actually true.** That is correct **only of the inline engine**. There are two
same-named packages, and the veto exists in one of them:

- `src/skylize/app/decision_engine/` — **the inline engine.** Has the stage-0 veto:
  `evaluator.py:104-114` runs `safety_veto_check` ahead of authority, unconditionally, and
  terminates on a rejecting verdict. Has `SecurityVerdict` (`events.py:59`), the mapper
  (`events.py:77-90`), and `DecisionProposal.security_verdict` (`events.py:118`).
- `src/skylize/decision_engine/` — **the OPA engine, the one `SKYLIZE_DECISION_ENGINE=opa`
  activates.** Searching this entire package for `security_verdict`, `safety_veto` or
  `SafetyVerdict` returns **zero matches.** Its pipeline runs six stages beginning at
  `_stage_authority` (`pipeline.py:177-187`). **There is no stage 0.**

**The consequence:** flipping the flag today would silently remove the absolute safety
veto from the running system. Not degrade it — remove it. The inline engine that holds the
veto stops running, and the OPA engine that starts has no equivalent. Nothing in the code
or the tests would report this, because the veto is unit-tested against the inline
evaluator only.

**Why not autonomous:** porting a governance control into a second engine is not a
mechanical edit. The OPA engine's whole premise is that policy lives in Rego, so the
correct home for a veto stage there is a genuine design question (a Python stage-0 mirror
vs a Rego `security_veto` class vs both), and `policy/skylize/decision/security_veto.rego`
already exists as a fail-closed placeholder — implying the intended answer is Rego, which
is hard-stop S2 territory.

**Options:**
- **A (recommended):** treat "the OPA engine has a working absolute veto" as a blocking
  precondition of the flag flip, and decide its shape (Python stage-0 mirror vs Rego
  class) as an explicit, recorded decision before any flip.
- **B:** flip anyway and accept the veto gap. **Consequence:** a governance product ships
  with its advertised absolute security veto silently absent. Not recommended under any
  reading.
- **C:** rely on the placeholder `security_veto.rego`. **Consequence:** it is
  `default allow := false` with no rules, so it denies everything — the veto would be
  "present" only in the sense that nothing is ever approved.

**Recommendation (NOT a decision):** Option A, and add this as an explicit line item to
whatever gates the flip. The cheapest correct version is likely a Python stage-0 mirror in
the OPA pipeline (matching `evaluator.py:104-114`), because it keeps the veto deterministic
and replayable and does not depend on Rego authoring — but that is your call, not mine.

---

## D7 — The spend amount never reaches OPA (input contract gap)

**Decision:** how business fields should be projected into the OPA input document, which
requires the amount/currency model you have not yet approved.

**State (verified, and now locked by a test):** `consumer.py:229-240` sets
`DecisionContext.payload` to the **whole event envelope**, so business fields sit under
`payload["payload"]`. `OPAClient._build_input` filters against `SAFE_PAYLOAD_KEYS` at the
**top level only** (`opa_client.py:61-65`). For a real `sales.campaign_proposed`, exactly
two keys survive:

```
{"authority_level": ..., "governance_token_id": ...}
```

`campaign_id`, `channel`, `currency` and `proposed_budget_minor_units` are all dropped.
**OPA would be asked to police spend without being told the amount.**

Worse for the spec: `guardrails.md` §4 names `amount` among the inputs OPA reads, but **no
event in the tracked vocabulary has a field called `amount`.** The spend-bearing field is
`proposed_budget_minor_units`, and it is not in `SAFE_PAYLOAD_KEYS` at all.

**Why this survived:** the existing allowlist test passes a **flat** payload — a shape no
producer emits — so a green test sat directly over the function that causes the bug. It is
now characterized by
`tests/decision_engine/test_opa_client.py::test_real_event_loses_its_spend_fields_before_reaching_opa`,
which asserts the wrong behaviour on purpose and must be rewritten when this is fixed.

**Why not autonomous:** the fix requires deciding what `amount` means — minor units vs
major, and USD-only vs multi-currency. Both are marked `[OWNER-DECISION-REQUIRED]` in
`policy_inputs` §0.2 (lines 151, 157). Inventing the mapping would put an unapproved
value into the policy path, which is the exact failure this whole gate exists to prevent.

**Recommendation (NOT a decision):** settle `policy_inputs` §0.2 first (D2), then project
the nested payload explicitly rather than widening `SAFE_PAYLOAD_KEYS` — a flat
top-level filter over a nested envelope is what produced a silent drop once already.

---

## D8 — Items explicitly NOT acted on (hard stops honoured)

Listed so their inaction is a record, not an oversight:

- **S3 — the engine flag.** `SKYLIZE_DECISION_ENGINE` was not flipped anywhere.
- **S4 — no live OPA server, no environment created.** Nothing was deployed.
- **S5 — `feat/grammar-gateway`** left untouched (two-architecture composition judgment).
- **S6 — no branch or worktree deleted.** All 27 worktrees remain; all were clean at start
  except this one (which held only the untracked file in D1).
- **S7 — `fix/c3-investor-status`** not merged; it carries investor-facing wording whose
  own commit message demands human sign-off.
- **S8 — no credential or secret operation.** No command was run that prints a secret value.
- **Nothing was pushed.** No force-push, no reset, no branch deletion.
