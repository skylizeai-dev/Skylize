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
