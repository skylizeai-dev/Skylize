# DECISIONS_PENDING — overnight run 2026-07-14 (branch `chore/overnight-2026-07-14`)

Ranked by what unblocks the most.

## [DECISION] Pg implementations for CapitalRepository / ProcessedEventStore
- Task: T2
- Need from me: green-light (and priority) for Postgres-backed budget-ceiling +
  idempotency stores, or explicit acceptance that the engine's stores are
  in-memory for M1.
- What I did instead: best-effort default — DecisionEngine is wired at the
  composition root behind the existing ports; on the postgres backend it falls
  back to the in-memory defaults. Functionally correct single-process, but
  idempotency does not survive a restart and budget ceilings aren't durable.
  NOT production-grade for the postgres backend.
- Files: src/skylize/bootstrap.py (comment marks the gap), src/skylize/app/decision_engine/engine.py:73-74

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
