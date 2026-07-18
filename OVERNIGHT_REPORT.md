# Overnight Report — 2026-07-14

Branch: `chore/overnight-2026-07-14` (from `release/console-m1` @ 9f798744).
All 8 queue tasks completed; none skipped; no architecture conflicts hit.
Nothing was pushed; no worktree was touched; no merges performed.

## 1. Tasks completed (one checkpoint commit each)

| Task | Commit | What landed |
|---|---|---|
| T0 | 3c7d8c9a | WF-05 content-gate diff was **already committed pre-session** as 9f798744 (nothing was staged). Verified both separation invariants (content_gate ↔ decision_engine: zero cross-references, both directions). Committed the leftover triage-report addendum. |
| T1 | 86ce5a78 | `SKYLIZE_OPENAI_API_KEY` documented (empty) in .env.example — consumed only by knowledge-ingestion embeddings, gated on qdrant_url+key. |
| T2 | 43ee81e2 | DecisionEngine wired at the composition root behind existing ports; idle by default; org subscriptions from `SKYLIZE_DECISION_ENGINE_ORG_IDS`; stopped via container closer. Fixed both schema drifts (`ceiling.currency` → `proposal.currency`; restored optional `Payload.reason`). **All 3 skipped decision_engine tests unskipped and passing** + 3 new wiring tests. Engine deliberately never receives the LLM gateway — guardrail separation intact. |
| T3 | 754ea7aa | Concrete `LLMJudge` behind a typed `NodeJudge` port. Receives the single shared `GuardedLLMGateway` (now exposed as `Container.llm`; identity-tested). Independent verifier: own logical model ("reasoning") at temperature 0. Fail-closed on unparseable verdicts, bad context, and content-gate blocks; provider outages propagate for Temporal retry. 8 new tests incl. an injection-payload gating test. |
| T4 | 0e73d603 | Postgres `PgWorkflowRepository` + `workflow_run_steps` migration 0010 (RLS org_id, policy/grant shape copied from migration 0006) + 3 RLS integration tests. Cherry-picked (`-x`) from `feat/workflow-repository-postgres` — content verified byte-identical, source worktree untouched. |
| T5 | 826407d7 | Demo-adapter full-lifecycle e2e: onboard → agent produces (demo LLM) → decision-bearing event → decision engine → HITL defer → human verdict resumes → audit trail. **Found and fixed a real gap**: `GovernanceHumanApprovalReceived` was never in `EVENT_REGISTRY`, so the HITL verdict couldn't ride the bus at all. |
| T6 | 83a5a382 | `WORKTREE_AUDIT.md` — all 11 worktree branches, read-only (merge-tree trial merges). 5 fully merged, 2 absorbed via cherry-pick this session, 3 docs-only low-risk, 1 medium-risk with documented security-sensitive resolution. |
| T7 | 9c6abb05 + 1ab9bc81 | Two complementary CI gates: all-modules-importable (cherry-picked from `chore/import-linter-orphan-check`, conflict resolved keeping both steps) proves every module **loads**; new grimp-based orphan contract (`scripts/find_orphan_modules.py` + frozen allowlist ratchet) proves something **reaches** it. 15 orphans listed, none deleted. |

## 2. Tasks skipped
None. Judgment calls and stubs are in [DECISIONS_PENDING.md](DECISIONS_PENDING.md).

## 3. Test delta
- Baseline: **934 passed / 15 skipped / 0 failed**
- Final: **949 passed / 15 skipped / 0 failed** (`+15 passed, 0 new failures`)
  - +3 formerly-skipped decision_engine tests unskipped
  - +3 decision-engine bootstrap wiring tests
  - +8 LLMJudge/activity tests
  - +1 demo-lifecycle e2e
  - Skips: −3 (unskipped) +3 (new env-gated PG integration tests for T4 → run in CI's integration job). Only 2 remaining skips are code-drift (memory_gateway, llm_agent_runner — both M5-scoped, not in this queue).

## 4. Gate results (final, whole branch)
- pytest: 949/15/0 — green
- `scripts/check_forbidden_imports.py`: 0 violations
- Key-leak grep over `git diff 9f798744..HEAD`: no matches (sk-/api_key=/bearer/private-key patterns)
- mypy strict `src/`: clean (190 files); ruff `src tests`: clean
- `check_all_modules_importable.py`: 190/190; `find_orphan_modules.py`: no new orphans
- Frontend: untouched (as preferred), so no tsc/build run

## 5. Needs your input
See [DECISIONS_PENDING.md](DECISIONS_PENDING.md), ranked. Top three:
1. **Pg CapitalRepository/ProcessedEventStore** — engine idempotency/budgets aren't durable on the postgres backend.
2. **feat/tenant-isolation-port rebase** — 2-file conflict where careless resolution silently drops the prompt-injection screen.
3. **Orphan dispositions** — 15 modules listed (esp. `contracts.models`, `dal.memory_adapter`, `schemas.workflow`).

## 6. Recommended next 3 moves
1. Review + merge `chore/overnight-2026-07-14` into `release/console-m1` (14 commits, every gate green), then decide the Pg-stores question before enabling `SKYLIZE_DECISION_ENGINE_ORG_IDS` anywhere real.
2. Rebase `feat/tenant-isolation-port` using the resolution documented in WORKTREE_AUDIT.md (or ask me to — it's the one medium-risk branch, and it's security work worth landing).
3. Housekeeping: delete the 5 fully-merged worktrees/branches + the 2 absorbed ones (list in WORKTREE_AUDIT.md), and decide the egg-info gitignore question — it removes permanent working-tree noise.

## 7. Fragile / stubbed — know before you rely on it
- **DecisionEngine on postgres backend** uses in-memory idempotency + capital stores (DECISIONS_PENDING #1). Memory backend is fully honest.
- **Closer ordering**: container closers run in append order, so on the postgres backend db/redis close *before* the engine/subscriber consumers stop. Pre-existing pattern (governance subscriber has the same quirk) — engine wiring follows it for consistency; worth a deliberate fix pass.
- **No Temporal worker bootstrap exists** — judge + repo + migration are implemented and tested, but nothing constructs `WorkflowActivities` yet; the migration has never run against a real DB outside CI's integration job.
- **Demo-mode judge blocks** (fail-closed unverified) rather than fake-passing — intentional; see DECISIONS_PENDING #5 if you want keyless workflows to pass judge gates.
- **`Payload.reason`** on the HITL verdict event is my restoration of drifted intent — additive + contract-suite green, but it's a wire-schema change you should eyeball.

---

## CORRECTION (2026-07-15) — "M5" is an unsourced term

Section 6 above (the skips line) describes `memory_gateway` and `llm_agent_runner` as **"M5-scoped."** That framing is **unsourced**: no document in this repository defines "M5" or any "launch plan," and the term appears only to have been self-propagated across reports, test skip reasons, and config comments.

The **underlying technical fact is unchanged and still accurate**: those two modules are genuinely dead/unwired code (memory gateway unwired from bootstrap; the `runtime/` `LLMAgentRunner` alt-stack is dead, `LLMStepRunner` is the live runner). Only the authority attached to them — that they belong to a defined "M5" milestone tracked by a "launch plan" — was fabricated. There is no tracked removal plan.

The live config/test comments that carried this framing were corrected on branch `fix/unsourced-m5-references`. This point-in-time report is left intact with this note appended rather than silently rewritten.
