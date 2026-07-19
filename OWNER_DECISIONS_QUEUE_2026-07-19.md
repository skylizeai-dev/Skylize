# Owner Decisions Queue — 2026-07-19

Items requiring **Mr. Özkan's** judgment. Nothing here was decided by the overnight session.
Each entry states: the decision, why it could not be made autonomously, options with
consequences, and a **recommendation** (a recommendation — *not* a decision taken).
Every code reference below was confirmed present in-repo before it was written down.

---

## S1 — ADR-0005 department vocabulary (category-vs-department) — OPEN, HIGHEST PRIORITY

**Decision:** How the OPA decision engine's AUTHORITY stage should model "department". Today it
derives `ALLOWED_DEPARTMENTS` from event-type *prefixes* (`constants.py:31-49`, splitting
`"sales.campaign_proposed"` → `"sales"`), but the bus routes by *real department* and `director_growth`
emits `sales.*` events under `department="growth"` (`contracts/mvp/growth.py:17`). Prefix ≠ department.

**Why not autonomous:** Hard-stop S1. It sets cross-cutting routing/authorization semantics across the
AUTHORITY stage, agent contracts, and the bus routing key. The ADR (now merged, `2cb665ad`, status
**"Proposed — resolution pending"**) explicitly defers this to owner acceptance. Guessing = the M5 failure class.

**Consequence of the conflict (verified against `pipeline.py:193-223`):** both possible wirings fail —
subscribing per-`department` (`growth`) makes AUTHORITY reject every spend proposal (`"growth" ∉ {"creative","sales"}`)
with a policy-shaped audit trail; subscribing to the `sales` channel means the consumer receives nothing
(that channel is the SDR agents'). This is why `SKYLIZE_DECISION_ENGINE=opa` stays fail-closed/unwired.

**Options (from the ADR's Alternatives A–E):**
- **A (ADR's recommendation, and mine):** replace prefix-derivation with an explicit
  `department → event-type` table in `constants.py` (`growth → {sales.campaign_proposed, sales.budget_reallocation_proposed}`,
  `creative → {creative.review_requested}`); subscriptions fall out of the same table so transport and
  authority can't drift. Confined to `constants.py`. **Cost:** makes "which departments the engine serves"
  an explicit reviewable declaration — which is *why* it needs your sign-off.
- **B:** restamp campaign proposals to `department="sales"`. **Rejected in ADR** — collides Growth and SDR
  teams onto one channel, breaks the (correct) inline engine, makes the data wrong to satisfy a string split.
- **C:** insert a category→department mapping layer. **Rejected** — a third routing concept to maintain in lockstep.
- **D:** drop the department check, gate on event_type alone. **Rejected** — deletes a real governance control.
- **E:** ship as-is (subscribe `growth`, let AUTHORITY reject). **Rejected** — "denies all spend, cites policy."

**Recommendation (NOT a decision):** Accept the ADR with **Alternative A**. It is the only option that keeps
both AUTHORITY checks intact while sourcing department ownership from the agent contracts (the real authority),
and it is cheapest now — no producer for `sales.campaign_proposed` exists yet (zero construction sites in
`src/` or `tests/`), so settling it costs a constants table rather than a live-routing migration later.
**This is the single highest-priority decision blocking the OPA engine from ever being wireable.**

---

## S2 — policy_inputs.md [OWNER-DECISION-REQUIRED] values — OPEN + **BLOCKED: source doc missing**

**Blocker discovered (Phase 3A):** `docs/04_decision_engine/policy_inputs.md` **does not exist anywhere** —
not on durable HEAD, not on any of the 29 branches, not in git history, not on the filesystem (worktrees +
scratchpad searched). This is corroborated by ground truth: **ADR-0004 states four times** that the file
"does not yet exist and must be authored" (`docs/architecture/adr/0004-opa-production-arbiter.md:27,43,65,67`).

Phase 3A asked to "land policy_inputs.md **with its DRAFT banner intact**", which presupposes an existing
draft to move into place. There is none. The session **did NOT author one**, because doing so would mean
inventing its structure and the very set of [OWNER-DECISION-REQUIRED] items — an owner-gated act (S2) and
the M5 fabrication failure the anti-M5 protocol forbids.

**What is needed from you:** either (a) provide the DRAFT `policy_inputs.md` you referenced so it can be
landed verbatim (banner + markers intact, no values filled), or (b) confirm it should be authored fresh —
in which case its section layout and the list of decisions it gates is itself an owner-level act, not
overnight work.

**The values it will gate (unchanged, still OPEN):** dollar thresholds; over-ceiling behavior (`hard_deny`
vs `defer_to_human`); currency model; graduated-autonomy N. Real `.rego` authoring (S3) and the engine
flip (S4) remain blocked on these regardless.

**Two confirmed facts to fold into its §0.5 once it exists** (verified this session, so they are not lost):
(i) the safety veto is now **unconditional** via the stage-0 `safety_veto_check` (see session report 3B);
(ii) the `SafetyVerdictOut → DecisionProposal.security_verdict` **producer bridge is the last gap**, blocked on S1.

---

## MERGE-CONFLICT (Phase 2) — feat/grammar-gateway ABORTED — OPEN

**What happened:** `git merge --no-ff feat/grammar-gateway` (commit `3fa3d7cd`,
"grammar-constrained structured output port") conflicted in 3 files and was aborted per protocol
(`git merge --abort`; tree restored to `33d0b94c`). Conflicts:
`src/skylize/adapters/llm/__init__.py` (content), `src/skylize/adapters/llm/gateway.py` (content),
`tests/contract/test_structured_output_contract.py` (add/add).

**Why not autonomous:** Hard-stop S8. Durable's `gateway.py` has since evolved (the `GuardedLLMGateway` /
`LLMContentGate` wrapper wired in `bootstrap.py:256-261`). Reconciling how a grammar-constrained
structured-output port composes with the current content-gated gateway is a design judgment between two
plausible architectures, not a mechanical resolution.

**Options:** (1) rebase `feat/grammar-gateway` onto current durable and re-resolve `gateway.py` by hand,
deciding whether `structured()` wraps or sits beside `GuardedLLMGateway`; (2) re-author the port fresh
against today's gateway; (3) drop the branch if the structured-output port is no longer wanted.
**Recommendation (NOT a decision):** option (1) — the feature (grammar-constrained structured output) is
self-contained and valuable; it needs a human to decide its seam with the content gate. Small, well-scoped.

---

## ARCHITECTURE — tenant-isolation branch pair (NOT merged) — OPEN

**What:** The tenant-isolation core (`src/skylize/memory/identity.py`, injective tenant identity +
tenant-keyed Qdrant IDs + knowledge hardening) is **NOT on durable** and is carried by TWO branches whose
`identity.py` is **byte-identical**:
- `feat/tenant-isolation-rebase` (`0df89215`, 3 commits) — Python-only; + `SESSION_B_REPORT.md`,
  `test_bootstrap_wiring`, `deliverable_approval_embed`.
- `fix/knowledge-tenant-identity` (`3e1dca3f`, 2 commits) — same core + website console-auth/proxy +
  a fail-closed auth guard on the `/api/console` n8n proxy (`25fd405c`).

**Why not autonomous:** Hard-stop S8. Both are plausible; they have different bases and pull in different
unrelated files (one carries playwright logs / a `caveman` skill / `deploy-staging.yml` in its wider tree).
Choosing the canonical variant — or cherry-composing the Python core from one plus the website security
work from the other — is an architecture decision.

**Options:** (1) adopt `fix/knowledge-tenant-identity` as the superset (it has the core + the console
security fix) and retire the other; (2) adopt `feat/tenant-isolation-rebase` for the Python core and
port only the website auth guard separately; (3) author a fresh tenant-isolation branch from current
durable taking the byte-identical `identity.py` core.
**Recommendation (NOT a decision):** option (1) appears to be the superset, but this needs a human to
confirm the two `identity.py`-adjacent implementations (`knowledge_ingestion.py`, `qdrant_adapter.py`,
`edge/routes/knowledge.py`) are the intended ones and that the extra files in each branch's tree are wanted.
Tenant isolation is security-critical — it should land deliberately, not via an overnight guess.

---

## Report-only (NO decision required, listed for morning awareness)

- `feat/workflow-repository-postgres`, `feat/tool-dedup-convergence`, `chore/import-linter-orphan-check`:
  **superseded** — their content is already byte-identical on durable (see session report Phase 2 table).
  No action; do not delete (git-discipline).
- `fix/c3-investor-status` (`2ae91bf4`): investor-facing wording, its own commit says "NEEDS HUMAN
  SIGN-OFF before external use." Not merged. Awaits your review.
- `release/console-m1` (`128ac0f3`): S7 — left untouched.

---
---

# APPENDED QUEUE — 2026-07-19 (later run)

> Appended, not rewritten. Items above stand. New items are numbered **Q1–Q5** to avoid
> colliding with the earlier list's numbering.
> Verified against commit `639c1cf0` on `feat/durable-governance`. Nothing was pushed.
>
> **Recommendations below are recommendations, not decisions taken.** Nothing in this
> session acted on any of them.

---

## Q1 — Import-linter: where do process entrypoints live?

**Status:** CI gate `lint-imports` is **RED** and has been before this session. `Contracts: 4 kept, 1 broken`.

**The decision:** `src/skylize/app/orchestrator/temporal/worker.py:32,34` imports `skylize.bootstrap`
and `skylize.dal.workflows`, breaking the contract *"Application logic contains no SQL (depends on dal
ports only)"* (`pyproject.toml:171`) via three forbidden-module clauses. Fixing it requires choosing a
layering, which is why it was not fixed.

**Why I could not decide it:** the two mechanical escapes are both ruled out by evidence, not opinion —
`grimp` counts import *statements*, so neither a function-body import (`bootstrap.py:137`, already inside
an `else:` and still reported) nor an `if TYPE_CHECKING:` guard (`bootstrap.py:22`, guarded and still in
the reported chain) removes the edge. And injecting a factory from a composition root does not apply:
the worker **is** its own process's composition root (`python -m ...worker`). That leaves only *delete
the edge* or *amend the contract*, both layering decisions.

**Option A — process entrypoints live OUTSIDE `skylize.app`.** Move the module out of the `app` package.
*Consequence:* contract text unchanged, gate stays maximally strict, and it matches existing precedent —
the sibling `src/skylize/decision_engine/worker.py:44` imports `..dal.connection` directly and is legal
purely because it sits outside `skylize.app`. *Cost:* touches the docstring run command, `config.py:111`,
`scripts/find_orphan_modules.py:48`, `tests/unit/test_temporal_worker.py:26`, and a mypy override in
`pyproject.toml:228`.

**Option B — `skylize.app` may host entrypoints; the contract means app *logic*, not app *entrypoints*.**
Add `ignore_imports` for the two edges, or narrow `source_modules`. *Consequence:* the config's own
comment at `pyproject.toml:155-157` already states this principle — "bootstrap (the composition root) and
edge (the process entrypoint) are allowed to construct concrete adapters at startup" — so there is a real
gap between what the comment promises and what the contract implements. *Cost:* weakens the gate and makes
"entrypoint" a status any `app` module can claim.

**My recommendation (a recommendation, not a decision):** **Option A**, on the strength of the
`decision_engine/worker.py` precedent — the codebase already answers this question one way in the newer
of the two workers. Option B is legitimately arguable given that comment, and if you prefer it, the
comment should become the contract rather than the contract silently contradicting the comment.

---

## Q2 — Event delivery: there is no redelivery, and fixing it changes the production engine

**Status:** the highest-severity *technical* finding of this session. Documented and made executable;
deliberately not fixed.

**The decision:** should the shared event transport gain real at-least-once delivery, and on what terms?

**What the code actually does** (all verified): `RedisEventBus.consume` reads `{stream: ">"}` only
(`redis_adapter.py:55`) and the adapter issues no XAUTOCLAIM/XCLAIM/XPENDING anywhere. A message left
un-acked by a failing handler is never re-read — not by a peer, not by the same worker after restart. It
sits in the PEL forever. So `router.py`'s `# else: no ack → redelivery` strands the message,
`_attempts[event_id]` can never exceed 1, and the retry/DLQ budget (`dlq_after_retries` default 5,
`config.py:88`) is **unreachable dead config**. Effective semantics: **at-most-once for failures.**

Seven docstrings across the codebase claimed at-least-once. All corrected in `6cf271f2`; two of them had
been introduced by this session's own Phase 1 commit, which inherited the false framing.

**Why I could not decide it:** it cannot be contained to the OPA engine. `EventRouter` and
`RedisEventBus.consume` have exactly two construction sites — `app/decision_engine/engine.py:104` (the
**inline** engine, which is the one in production, `config.py:103` defaults to `"inline"`) and
`decision_engine/consumer.py:102`. There is no seam that gives the OPA worker reclaim without the inline
engine getting it in the same commit. Turning it on would mean a reclaimed message whose owner died
between `_emit` and `mark_processed` (`engine.py:144-145`) **publishes a second terminal `decision.*`
event** — a governance-visible outcome, not a transport detail. Correct reclaim also needs a durable
delivery count, and `DeliveredEvent` (`bus.py`) has no field for one, so it changes a shared port Protocol.

An off-by-default flag does not make it contained — it makes it inert, shipping the same behaviour plus a
knob and a false impression of coverage. That is precisely how the previous reclaim loop failed: it ran
against event-type-named streams that never existed on the live bus, so it never reclaimed a real message
either. **There is no working prior art to port, only a design to re-derive.**

**Scope a design record should cover:** (i) `DeliveredEvent` gaining a durable delivery count;
(ii) whether reclaim lives in `consume` or a separate `bus.reclaim(...)` port method; (iii) both engines
sharing the consumer group `cg:decision_engine` (`engine.py:102`, `decision_engine/config.py:19`), which
means a flag flip hands the newly-started engine the other's stranded PEL backlog; (iv) an adapter-level
recovery test.

**My recommendation (a recommendation, not a decision):** treat it as a design record before any code,
and sequence it **after** the OPA flag flip rather than before — it touches the production inline engine,
and doing both at once makes an incident un-diagnosable. In the meantime the gap is no longer invisible:
`tests/integration/test_decision_engine_consumer_redis.py` asserts the current behaviour and will fail
loudly the day reclaim lands.

---

## Q3 — `policy_inputs.md` is untracked, misnamed, and cites an unsourced label

**The decision:** three small things only you can settle about the document that gates all Rego work.

1. **It is not in version control.** `git ls-files docs/04_decision_engine/` does not list it. The file
   on disk is `policy_inputs (1).md` — a duplicated download. It is one `git clean` from deletion and
   invisible in any diff or review.
2. **The canonical filename.** Everything refers to `policy_inputs.md`; the file is `policy_inputs (1).md`.
   Renaming someone's document is an owner act, and the `(1)` implies there may be another copy elsewhere
   that is actually the newer one.
3. **Line 9 says the file "institutionalizes the M5 lesson."** `M5` has **no tracked source anywhere in
   this repo** — it is the invented milestone label that propagated through four prior sessions. It is now
   embedded in the very document meant to prevent that class of error, and if committed as-is it becomes
   citable provenance for a thing that never existed.

**Why I could not decide it:** it is your draft, awaiting your approval, and every section still reads
`[RESEARCH-SUGGESTED]` or `[OWNER-DECISION-REQUIRED]` with **none** marked `[APPROVED]`. Editing or
committing it would be putting words in your mouth on the document that governs S1 and S2.

**My recommendation (a recommendation, not a decision):** rename to `policy_inputs.md`, replace the
`M5` phrase with a plain description of the lesson ("no value enters code without a traceable,
owner-approved source"), and commit it in DRAFT so the approval history is reviewable. **S2 stays blocking
either way** — committing it does not approve it.

---

## Q4 — Is staging Railway or AWS ECS?

**The decision:** the brief and the locked stack describe staging as **Railway**. The repo deploys staging
to **AWS ECS**: `.github/workflows/deploy-staging.yml:13-17` sets `ECS_CLUSTER: skylize-staging`,
`ECS_SERVICE: skylize-staging-api`, and the ECS deploy runs at `:151-173`. The only tracked Railway
artifacts are `infra/opa/railway.json` and `website/railway.json` — an OPA service definition and the
marketing site, not the app.

**Why it matters now:** it changes who does the work for blockers B3/B4/B5 and where the OPA server has to
live. `infra/opa/railway.json` exists and is Railway-shaped, so the OPA server was *designed* for Railway
while the app it must serve deploys to ECS. Those two facts do not compose on their own.

**Why I could not decide it:** this is a platform decision with cost and operational consequences, and
S4 forbids creating any environment. Nothing was deployed or created.

**My recommendation (a recommendation, not a decision):** state the intended target explicitly in the ADR
or stack doc before B3 is worked, because the OPA server's home determines the network path from the
decision worker and whether `infra/opa/railway.json` is current or vestigial.

---

## Q5 — Cheap, unambiguous, but I was told not to touch it: the broken live-OPA test

**Not really a decision — a request for permission.** `tests/decision_engine/test_opa_client_integration.py:59`
and `:76` do `allow, deny_reasons = await client.evaluate(...)`, but `evaluate` returns an `OPAResult`
with four fields (`opa_client.py:82`, `models.py:41-47`). The unpack raises
`ValueError: too many values to unpack (expected 2)` — reproduced against the real class. It is gated on
`SKYLIZE_TEST_OPA_URL`, which no CI config sets, so it has **always skipped** and has never been executed.

I did not fix it because this session's brief says do not modify tests. It is a one-line-per-site fix.
**Consequence of leaving it:** the first person who stands up an OPA server — i.e. the person working
blocker B3 — has their only smoke test fail for a reason that has nothing to do with their server.

**My recommendation (a recommendation, not a decision):** authorise the fix as a standalone commit before
B3 is started.

---

## Still blocking, unchanged from the earlier list

**S1 / S2 remain the critical path and remain entirely yours.** No section of `policy_inputs (1).md` reads
`[APPROVED]`, so no real Rego may be authored; and with the bundle a fail-closed placeholder
(`policy/skylize/decision/decision.rego:14` `default allow := false`, with all six class files carrying a
comment confirming no rule sets `allow := true`), the OPA engine would today **reject every proposal it
received.** That, not the plumbing, is what stands between here and a flag flip — the plumbing is now done.

**The single highest-priority decision blocking progress:** approve (or amend) the sections of
`policy_inputs.md` marked `[OWNER-DECISION-REQUIRED]` — dollar thresholds, over-ceiling behaviour
(`hard_deny` vs `defer_to_human`), currency model, graduated-autonomy N. Everything else in this queue is
sequenceable after it; nothing else unblocks it.
