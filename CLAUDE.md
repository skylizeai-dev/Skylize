# Skylize — orientation for Claude Code

Skylize is a multi-tenant, governance-first AI-agent platform: agent intent
becomes a real-world action only after passing an explicit, auditable governance
path (LangGraph governance nodes: token -> authority -> kill-switch). The vision
docs frame it as an "AI-native Business Operating System" (docs/01_vision/vision.md:21).

This file holds only things that do not change. For current, commit-specific
state — test counts, open defects, what is wired, branch topology — read
**docs/REPO_STATE.md** (see CURRENT STATE at the bottom).

## ARCHITECTURE CONSTRAINTS
- **ADR-0002** (docs/architecture/adr/0002-crewai-removal-langgraph-only.md):
  LangGraph OSS is the SOLE agent-orchestration framework. CrewAI and LangChain
  are forbidden as direct imports, CI-enforced (scripts/check_forbidden_imports.py
  + the import-linter "No direct LangChain/CrewAI imports" contract). langgraph's
  transitive langchain_core is the only accepted pull-in.
- **ADR-0004** (docs/architecture/adr/0004-opa-production-arbiter.md): the OPA/Rego
  engine is the DESIGNATED production governance arbiter; the inline evaluator is
  the dev stand-in and production fallback. Selection is per-environment via
  `SKYLIZE_DECISION_ENGINE`; exactly one engine emits terminal `decision.*` events
  per environment; misconfiguration fails closed at startup. `"opa"` is NOT yet
  enablable — it stays gated to `"inline"` until real Rego + a live OPA server +
  wire-parity land (bootstrap.py fails closed on any non-`"inline"` value).

## THE TWO ENGINES (do not confuse)
- **app/decision_engine** — the INLINE evaluator. Live: wired at bootstrap.py, used
  by the `/agents/execute` synchronous gate (app/agents/execution.py). The live
  request path (`app`, `edge`) MAY import it.
- **decision_engine** — the OPA package. NOT wired into the API process; runs only
  as its own worker (`python -m skylize.decision_engine.worker`). The request path
  MUST NOT import `skylize.decision_engine` (owner decision K3, dal/hitl.py:11).

## THE THREE LEDGERS (conflating them is forbidden — ADR-0006, docs/architecture/adr/0006-ai-cost-ledger.md)
- **run_ledger** (runtime/run_ledger.py) — in-flight per-run TOKEN ceiling; unit =
  tokens; RAM or Redis; discarded when the run ends.
- **budget_ledger** (migration 0001) — business spend against a ceiling (ad spend,
  vendor commitments); unit = currency MINOR units (cents); Postgres.
- **ai_cost_ledger** (migration 0012, dal/cost_ledger.py) — money value of consumed
  LLM tokens; unit = currency MICRO units (`cost_micros`); Postgres, append-only
  (corrections via reversing rows, never UPDATE).

## ENVIRONMENT
- Windows; PowerShell 5.1 is the primary shell. No `\` line-continuation. Em-dashes
  and curly quotes break git arguments — use plain ASCII in commit messages and CLI
  args. The Bash tool is available for POSIX syntax; each shell takes its own.

## WORKFLOW
- One terminal, one worktree, one branch, cut from main. Never run two terminals
  against the same working tree. Commit incrementally.

## TESTING
- Postgres-backed integration tests SKIP silently without their env vars. Before
  believing ANY claim about money, tenancy, or RLS, confirm those tests RAN, not
  skipped. Required (set session-scoped only): `SKYLIZE_TEST_DB_URL` (owner/superuser,
  migrations), `SKYLIZE_TEST_APP_DB_URL`, `SKYLIZE_TEST_OPA_URL`, `SKYLIZE_TEST_REDIS_URL`.
- `SKYLIZE_TEST_APP_DB_URL` MUST be the non-superuser, non-table-owner `skylize_app`
  role (tests/integration/conftest.py:26). A superuser or table owner bypasses RLS,
  so RLS / tenant-isolation tests prove nothing under the wrong role.
- **The gate before claiming green:** `powershell -ExecutionPolicy Bypass -File
  scripts/ci_unit_gate.ps1` runs every gate of CI's `unit` job in CI's order. `pytest`
  alone is NOT that gate — `ruff check src tests` is a CI step, and omitting it left
  CI red from `11b595e6` onward unnoticed. The script does not cover CI's `website`
  job (npm) or `integration` job; run those yourself when you touch those areas.

## EVIDENCE DISCIPLINE
- Code is ground truth. Docs, comments, and ADRs are CLAIMS to be tested against
  code. Cite `file:line` for every claim. Say UNVERIFIED rather than guess. This
  repo has a documented failure mode of stale claims propagating between sessions
  (e.g. a registry comment said "15 governed" while the code has 21).

## CURRENT STATE
- **docs/REPO_STATE.md** is the read-only state mirror. It describes a SPECIFIC
  commit and goes stale — re-verify against code before relying on any figure in it.
