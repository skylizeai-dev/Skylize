# Test-Suite Health — 2026-07-19

**Run:** `python -m pytest -q` (Python 3.12; `Python312\python.exe`).
**Branch / commit:** `feat/durable-governance` @ `1dc3cb30` (end of the 2026-07-19 overnight session).
**Result:** **982 passed · 23 skipped · 0 failed** (18 warnings, ~62 s).
**Integration env:** `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_APP_DB_URL` / `SKYLIZE_TEST_REDIS_URL` /
`SKYLIZE_TEST_OPA_URL` all unset → integration tests skip by design.

> This is a read-only health snapshot. **No test was modified to make it pass or skip.**

---

## Failures

**None.** 0 new failures, 0 pre-existing failures. The suite is fully green on the local 3.12 toolchain.

---

## Skip inventory (all 23 classified)

Every skip is **intentional** and falls into one of two buckets: environment-gated (needs an external
service that is not provisioned locally) or documented dead/unwired code. **No skip is test-rot from this
session's merges.**

### A. Environment-gated integration skips — 21 (pre-existing: 19; new this session: 2)

| File | Count | Gate | Origin |
|------|-------|------|--------|
| `tests/decision_engine/test_opa_client_integration.py` | 2 | `SKYLIZE_TEST_OPA_URL` (live OPA server) | **NEW** — landed via `feat/opa-infra-skeleton` (`31d2e8b6`) this session |
| `tests/integration/test_decision_engine_stores.py` | 6 | `SKYLIZE_TEST_DB_URL` + `SKYLIZE_TEST_APP_DB_URL` | pre-existing |
| `tests/integration/test_postgres_isolation.py` | 6 | `SKYLIZE_TEST_DB_URL` (+APP) | pre-existing |
| `tests/integration/test_redis_bus.py` | 4 | `SKYLIZE_TEST_REDIS_URL` | pre-existing |
| `tests/integration/test_workflow_repository.py` | 3 | `SKYLIZE_TEST_DB_URL` + `SKYLIZE_TEST_APP_DB_URL` | pre-existing |

These run in CI's integration job (with service containers) and locally when the env vars point at a real
Postgres/Redis/OPA. Skipping without infra is the designed behavior (`pyproject.toml` `integration` marker
+ per-file `skipif`). The 2 OPA tests assert **DENY** against the fail-closed placeholder bundle — a live
`allow=true` there would be a bug.

### B. Documented dead/unwired-code skips — 2 (both pre-existing)

| Test | Line | Reason (verbatim, current) |
|------|------|----------------------------|
| `test_llm_agent_runner.py::test_runner_dispatches_through_proxy_and_validates_output` | `:61` | "runtime/ LLMAgentRunner ctor drifted; the runtime alt-stack is dead code **with no tracked removal plan** (LLMStepRunner is the live runner)" |
| `test_memory_gateway.py::test_safety_agents_read_raises_permission_denied` | `:79` | "chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap **(dead code, no tracked rework plan)**" |

Both describe genuinely unwired code (the live LLM runner is `LLMStepRunner`; the memory gateway is not
wired into `bootstrap.py`). Both are `@pytest.mark.skip` with explicit reasons.

---

## Unsourced-label check (anti-M5)

**PASS.** No skip reason references "M5", a "launch plan", or any other unsourced milestone label. The two
dead-code reasons in bucket B previously read "M5 excision/rework per launch plan"; they were scrubbed to
sourced phrasing ("no tracked removal/rework plan") on `fix/unsourced-m5-references`, merged this session
(`275d991e`). Verified by enumerating all 23 skip reasons via `pytest -rs` — none contains `M5`.

---

## Delta vs the stated session baseline

| | passed | skipped | failed |
|---|--------|---------|--------|
| Baseline (`933cbcb6`, session start) | 982 | 21 | 0 |
| Now (`1dc3cb30`, session end) | 982 | **23** | 0 |

Skip delta **+2** = the two new OPA integration tests (`test_opa_client_integration.py`), both env-gated on
`SKYLIZE_TEST_OPA_URL` and inert without a live OPA server. Passed (982) and failed (0) held throughout.
No skip was removed; none was silently added beyond those two accounted-for integration tests.
