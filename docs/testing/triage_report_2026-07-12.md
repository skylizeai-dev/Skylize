# Test Triage Report — 2026-07-12

**Mode:** Read-only audit (delta-baseline). No source, test, or config files modified.
**Baseline bar:** "no NEW failures" — met (there are **zero** failures of any kind).

## Environment

| Item | Value |
| --- | --- |
| Working directory | `c:\Users\HP\Documents\Work\Skylize` (main worktree) |
| Branch | `release/console-m1` @ `05bcde1a` |
| Python | 3.14.6 |
| pytest | 9.1.1 (plugins: asyncio 1.4.0, benchmark 5.2.3, respx 0.23.1, hypothesis 6.155.7, anyio 4.14.1, langsmith 0.9.4) |
| Integration env vars | `SKYLIZE_TEST_DB_URL`, `SKYLIZE_TEST_APP_DB_URL`, `SKYLIZE_TEST_REDIS_URL` — **all unset** |

> **Scope note:** The task brief referenced a worktree `<PATH-TO>\skylize-wt-test-triage` on branch `chore/test-triage`. That placeholder was never filled in and no such worktree exists; the audit ran in the main worktree on `release/console-m1`. The existing worktrees are `chore/build-audit` and `feat/tenant-isolation-port`.

> **Env-drift note (does not block launch):** The repo pins `requires-python >=3.12` and `pytest>=8.0`, but this machine runs Python **3.14.6** / pytest **9.1.1** — ahead of the pinned/CI toolchain. The suite is fully green here regardless, but a green run on 3.14 is not a substitute for the CI matrix (3.12).

## Result

```
929 collected — 914 passed, 15 skipped, 0 failed, 0 errors  (65s)
```

Exit code `0`. No collection errors, no xfails, no unexpected passes.

## Failure classification

**No failures to classify.** The delta baseline ("no NEW failures") is trivially satisfied — the suite has neither pre-existing nor new failures.

| Test | file:line | Category | Root cause | Owner terminal | Severity |
| --- | --- | --- | --- | --- | --- |
| _(none)_ | — | — | — | — | — |

## Skips — accounted for (15 total: 10 infra-skip / 5 intentional dead-code)

All skips are deliberate `skipif`/`skip` markers, not silent failures. None represent a regression.

| Test(s) | file:line | Category | Root cause | Owner terminal | Severity |
| --- | --- | --- | --- | --- | --- |
| `test_postgres_isolation` (6 cases) | [tests/integration/test_postgres_isolation.py:64,79,101,118,140,186](../../tests/integration/test_postgres_isolation.py) | Infra-skip | `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_APP_DB_URL` unset — conftest gating by design (real Postgres) | CI integration job | Doesn't block |
| `test_redis_bus` (4 cases) | [tests/integration/test_redis_bus.py:56,79,104,138](../../tests/integration/test_redis_bus.py) | Infra-skip | `SKYLIZE_TEST_REDIS_URL` unset — conftest gating by design (real Redis) | CI integration job | Doesn't block |
| `test_decision_engine` (2 cases) | [tests/unit/test_decision_engine.py:145,168](../../tests/unit/test_decision_engine.py) | Stale expectation (intentional skip) | HITL resume `Payload.reason` drifted; decision_engine unwired from bootstrap — M5 excision/rework per launch plan | Decision-engine / M5 terminal | Doesn't block |
| `test_decision_evaluator` (1 case) | [tests/unit/test_decision_evaluator.py:134](../../tests/unit/test_decision_evaluator.py) | Stale expectation (intentional skip) | `BudgetCeiling.currency` drifted; evaluator unwired — M5 rework | Decision-engine / M5 terminal | Doesn't block |
| `test_llm_agent_runner` (1 case) | [tests/unit/test_llm_agent_runner.py:61](../../tests/unit/test_llm_agent_runner.py) | Stale expectation (intentional skip) | `runtime/` `LLMAgentRunner` ctor drifted; runtime alt-stack is dead code — M5 excision (live runner is `LLMStepRunner`) | Runtime / M5 terminal | Doesn't block |
| `test_memory_gateway` (1 case) | [tests/unit/test_memory_gateway.py:79](../../tests/unit/test_memory_gateway.py) | Stale expectation (intentional skip) | `chief_security_officer` contract not in MVP registry; memory gateway unwired — M5 rework | Memory / M5 terminal | Doesn't block |

The 5 intentional unit skips were introduced deliberately in commit `9861cf3c` (2026-07-09, "feat(governance,api): governed execution paths…"), each carrying an explicit M5-rationale reason string. They are staged debt tied to the post-launch M5 excision, not test rot from recent changes.

## Forbidden-stack check (CrewAI / LangChain)

No forbidden-stack imports in `src/` or `tests/`. The scan surfaced a single **prose** match: a comment at [src/skylize/adapters/llm/gateway.py:153](../../src/skylize/adapters/llm/gateway.py#L153) mentions "CrewAI" while describing the `generate_sync` thread-pool path. It is a comment, not an import — flagged here per read-only policy; scoping any cleanup is someone else's call.

## Observation (report-only, not fixed per read-only mandate)

- Deprecation warnings only, none failing: Starlette `TestClient`/httpx deprecation (fastapi), and `datetime.utcnow()` in `test_memory_gateway` / `test_memory_service`. These will eventually break on newer deps; not launch-blocking today.
- **Post-hoc addendum (2026-07-12, separate import-audit pass):** `src/skylize/app/orchestrator/temporal/activities.py` unconditionally fails to import (`ImportError: cannot import name 'WorkflowRepository' from 'skylize.dal.ports'` — confirmed via clean-venv `pip install -e ".[dev]"` + direct import). The 929-collected count above did not exercise this file: it's not imported by `temporal/__init__.py`, not imported by any test, and not wired into app startup, so pytest collection legitimately passes it by (no swallowed exception). This does not reopen the "0 new / 0 pre-existing failures" gate above, but it is a real latent break for any future Temporal worker bootstrap or test that imports this module — worth a follow-up ticket.
  - **RESOLVED by `fix/dal-ports-workflow-repo`:** added `WorkflowRunStepRow` and `WorkflowRepository` (Protocol) to `src/skylize/dal/ports.py`, matching the existing repository-port style. Direct import of `activities.py` now succeeds (verified with `temporalio` installed); `pytest --collect-only` still shows 929 collected / 0 errors (unchanged, since nothing imported this module during collection either way); mypy clean on both files. No concrete DB-backed implementation was added — still a follow-up before a Temporal worker can actually run.

## Hard exit gates

- [x] `git status` shows only the new report file (+ pre-existing untracked `.claude/`) — zero source/test/config files modified.
- [x] All current failures accounted for — there are none; all 15 skips classified.

## Summary

**0 NEW failures / 0 pre-existing failures / 15 skips (10 infra-skip, 5 intentional M5 dead-code skips).** Suite is fully green on the local Python 3.14 toolchain; delta-baseline bar met. Not launch-blocking. Recommend a confirmatory run on the CI 3.12 matrix (with integration service containers) before relying on this as the release gate.
