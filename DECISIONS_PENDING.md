# DECISIONS_PENDING — overnight run 2026-07-14 (branch `chore/overnight-2026-07-14`)

Ranked by what unblocks the most.

## Session A addendum (2026-07-15, branch `feat/durable-governance`)

RESOLVED: #1 (Pg stores) is done — durable PgCapitalRepository +
PgProcessedEventStore, migration 0011, runtime-proven against real Postgres.
See SESSION_A_REPORT.md. New judgment calls from that session:

- **[JUDGMENT] Branch base**: the session brief said "branch from
  release/console-m1 (post-merge HEAD)", but the overnight branch was never
  merged — everything A1/A2 build on (engine wiring, PgWorkflowRepository,
  migration 0010, LLMJudge) exists only on `chore/overnight-2026-07-14`.
  `feat/durable-governance` is cut from that branch's HEAD (8ff18d17), which
  contains release/console-m1. Merge order to intend: overnight branch →
  release/console-m1, then this branch.
- **[JUDGMENT] ProcessedEventStore port now carries `org_id`** (keyword-only
  param on both methods). Not a new port, but it IS a port-signature change:
  without the tenant, the Pg impl could not run inside `tenant_session` and
  the new table could not be RLS'd — which the brief required. Engine had the
  tenant at every call site; in-memory impl keys by (org_id, key) to match.
- **[JUDGMENT] No second budget table**: `budget_ledger` (migration 0001)
  already owns the budget-ceiling domain and maps 1:1 onto the BudgetCeiling
  port row. PgCapitalRepository reads it; 0011 adds only the UNIQUE
  (org_id, scope, period) natural key (plus the new processed-events table).
  Code-is-ground-truth beat the brief's literal "budget-ceiling table".
- **[JUDGMENT] Historical migration 0009 edited** (search_path leak fix):
  behavior-preserving for real deployments (public was the target anyway);
  only test-schema replays change. First real-DB run of the chain exposed it.
- **[NOTE] Container now exposes `db`** (postgres pool; None on memory) for
  sibling processes composed from the root (the Temporal worker). Services
  above bootstrap still depend on ports only.

## [DECISION — RESOLVED in Session A] Pg implementations for CapitalRepository / ProcessedEventStore
- Task: T2
- Need from me: green-light (and priority) for Postgres-backed budget-ceiling +
  idempotency stores, or explicit acceptance that the engine's stores are
  in-memory for M1.
- ~~What I did instead: best-effort default~~ RESOLVED 2026-07-15: durable Pg
  stores implemented + wired on the postgres backend; migration 0011; proofs
  (a)/(b) executed against real Postgres. See SESSION_A_REPORT.md.
- Files: src/skylize/dal/decision_stores.py, migrations/versions/0011_decision_engine_stores.py

## [DECISION] feat/tenant-isolation-port rebase (security-sensitive conflicts)
- Task: T6
- Need from me: who resolves it, and confirmation of the resolution strategy.
- What I did instead: audited only (audit-only mandate). Documented the exact
  resolution that avoids silently dropping the prompt-injection screen from the
  new ingest paths.
- Files: WORKTREE_AUDIT.md (feat/tenant-isolation-port section)

## [DECISION] Orphan-module dispositions (15 modules, listed — none deleted)
- Task: T7
- Need from me: per-module verdict: delete / wire / test. Highest-value calls:
  `skylize.contracts.models`, `skylize.dal.memory_adapter`,
  `skylize.app.runtime_adapters`, `skylize.schemas.workflow` (NodeSpec the
  Temporal engine will need), `skylize.contracts.mvp.safety`.
  Note: `skylize.schemas.agents.*` are NOT dead — they're dynamically imported
  via contracts/registry.py string paths; they stay allowlisted.
- What I did instead: froze all 15 into scripts/orphan_modules.txt; CI now
  fails only on NEW orphans (ratchet).
- Files: scripts/orphan_modules.txt, scripts/find_orphan_modules.py

## [DECISION] `reason` field restored on governance.human_approval_received
- Task: T2
- Need from me: confirm the wire-schema addition (optional `reason: str | None`,
  schema_version kept at 1.0 as an additive change; no producer exists yet).
  The engine's resume path and the drifted tests were both written to use it —
  I judged restoration as the intent, but it IS an event-schema change.
- What I did instead: added the optional field; contract suite green.
- Files: src/skylize/schemas/events/governance.py:150-160

## [DECISION] Demo-mode judge fails closed (no fake [DEMO] pass)
- Task: T3
- Need from me: choose: keep fail-closed (judge on DemoLLMAdapter returns
  unverified block — safe, but demo workflows can't pass a judge gate), or add
  a clearly-marked [DEMO] verdict template to the demo adapter so full
  workflows run green keyless.
- What I did instead: fail-closed (a verifier that silently passes in demo mode
  is worse than one that blocks).
- Files: src/skylize/app/orchestrator/temporal/judge.py, src/skylize/adapters/llm/demo_adapter.py

## [DECISION] src/skylize.egg-info tracked in git (constant churn)
- Task: T0
- Need from me: OK to `git rm --cached -r src/skylize.egg-info` + gitignore it?
  It's a setuptools build artifact; it dirties the tree every editable install
  (it is dirty right now — left uncommitted deliberately).
- What I did instead: left the 3 files dirty; excluded them from every commit.
- Files: src/skylize.egg-info/*

## [NOTE] Temporal worker bootstrap still absent (not in queue — flagging)
- Tasks T3/T4 delivered the judge, the repo impl, and the migration, but
  nothing in src/ constructs WorkflowActivities or runs a Temporal worker yet.
  The composition recipe is documented (LLMJudge(container.llm) +
  PgWorkflowRepository(db)); the worker entrypoint is the missing piece.
