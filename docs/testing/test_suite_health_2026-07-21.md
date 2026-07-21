# Test Suite Health — 2026-07-21

Point-in-time report. Measured, not quoted: every number below came from a run in this
session, on `feat/durable-governance`, Windows / Python 3.14 / pytest 9.1.1.

No test was modified to make it pass or skip.

## Counts

| Checkpoint | Commit | Passed | Skipped | Failed |
|---|---|---|---|---|
| Session baseline | `652393fd` | 1068 | 28 | 0 |
| After PEL-reclaim merge | `897d75ce` | 1074 | 31 | 0 |
| End of session | `88eedeed` | 1080 | 31 | 0 |

**+12 passed, +3 skipped, 0 failed throughout.** No test that passed at baseline
regressed at any checkpoint.

- The +6 at the reclaim merge are redelivery/DLQ tests that now drive real behaviour
  through the bus instead of fabricating it.
- The +3 skips are new real-Redis reclaim tests (see "Coded but not executed" below).
- The +6 at the end are the OPA fail-closed boundary tests and the input-shape
  characterization test added in `bf3d8dcb`.

## Other gates

| Gate | Command | Result |
|---|---|---|
| Ruff | `ruff check src tests` | **pass** |
| Types | `mypy src` | **pass** — 196 files |
| Forbidden imports | `python scripts/check_forbidden_imports.py` | **pass** |
| Module importability | `python scripts/check_all_modules_importable.py` | **pass** — 196 modules |
| Orphan modules | `python scripts/find_orphan_modules.py` | **pass** — 14 known, allowlisted |
| Import boundaries | `lint-imports` | **FAIL — pre-existing** |

`lint-imports` fails on the contract *"Application logic contains no SQL (depends on dal
ports only)"* (`pyproject.toml:170-174`), via `skylize.app.orchestrator.temporal.worker`
importing `skylize.bootstrap` and `skylize.dal.workflows`. CI runs this exact command
(`.github/workflows/ci.yml:25`), so **CI is red on this branch**. It is not red on `main`:
that module does not exist there. It arrived in `1ef9281d` (2026-07-15). Resolving it is a
layering decision, queued as D4 — this session did not decide it.

> **A note on how this gate must be run.** `python -m importlinter.cli lint-imports` exits
> 0 with no output — the module has no `__main__` guard, so it is a silent no-op. Only the
> console script `lint-imports` actually runs the contracts, and it exits 1. Every result
> in this report came from the console script.

## Skip classification — all 31 accounted for

**Infrastructure-gated (29).** None is a defect; each names the env var that would enable
it, and none can run in this environment (Docker is not installed and nothing listens on
`127.0.0.1:6379`).

| Count | Files | Guard |
|---|---|---|
| 7 | `tests/integration/test_redis_bus.py` | `SKYLIZE_TEST_REDIS_URL` |
| 5 | `tests/integration/test_decision_engine_consumer_redis.py` | `SKYLIZE_TEST_REDIS_URL` |
| 6 | `tests/integration/test_decision_engine_stores.py` | `SKYLIZE_TEST_DB_URL` + `SKYLIZE_TEST_APP_DB_URL` |
| 6 | `tests/integration/test_postgres_isolation.py` | `SKYLIZE_TEST_DB_URL` (+ app URL) |
| 3 | `tests/integration/test_workflow_repository.py` | `SKYLIZE_TEST_DB_URL` + `SKYLIZE_TEST_APP_DB_URL` |
| 2 | `tests/decision_engine/test_opa_client_integration.py` | `SKYLIZE_TEST_OPA_URL` |

**Dead code with no tracked plan (2).**

- `tests/unit/test_llm_agent_runner.py:61` — "runtime/ LLMAgentRunner ctor drifted; the
  runtime alt-stack is dead code with no tracked removal plan (LLMStepRunner is the live
  runner)".
- `tests/unit/test_memory_gateway.py:79` — "chief_security_officer contract not in MVP
  registry; memory gateway is unwired from bootstrap (dead code, no tracked rework plan)".

**No skip reason cites an unsourced label.** Both dead-code skips say "no tracked ... plan"
rather than naming a milestone that does not exist. That phrasing is correct and should be
preserved.

## Coded but not executed — read this before trusting the reclaim work

The PEL-reclaim change merged this session is covered by real-Redis integration tests that
**have never run**:

- `test_unacked_message_is_redelivered_by_the_adapter` (`test_redis_bus.py:105`)
- `test_acked_message_is_not_redelivered` (`:141`)
- `test_reclaim_respects_the_idle_window` (`:168`)
- `test_retry_budget_exhausts_into_the_dlq_against_real_redis` (`:198`)

All four skip without `SKYLIZE_TEST_REDIS_URL`. Docker is not installed in this environment
and no Redis is reachable on `127.0.0.1:6379`, so they could not be executed here.

What IS proven by tests that actually ran: the in-memory bus redelivers un-acked messages,
`_attempts` increments across redeliveries, the retry budget exhausts into the DLQ, and
redelivery does not double-process on either engine. What is NOT proven by execution: that
`XAUTOCLAIM` behaves as expected against a real Redis. That is a code-review-level
assurance, not a test-level one, and it should be closed by running these four tests
against a real Redis before the reclaim behaviour is relied on in production.

The same caveat, more sharply, for OPA: **no OPA server has ever been contacted by this
codebase.** Every pipeline-level OPA test substitutes a double for the real client — a
hand-written `_MockOPA` (`tests/decision_engine/test_consumer_integration.py:36`,
`test_orchestrator_integration.py:28`) or an `AsyncMock` on `evaluate`
(`test_department_vocabulary.py:30`) — so `OPAClient`'s own request/response code never
executes in them. The client's fail-closed branches are covered separately, by unit tests
using `httpx.MockTransport`: real bytes off a real response object, but no real server.
The only tests that would reach an OPA process are the two skip-guarded integration tests
above, and they have never run.
