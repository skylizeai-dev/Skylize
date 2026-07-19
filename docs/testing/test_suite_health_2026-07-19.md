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

---

# APPENDED — 2026-07-19 (later run), commit `639c1cf0`

> Append, not a rewrite. The section above is a point-in-time record from an earlier run
> on the same date and stands as written.

## Counts

| Checkpoint | Passed | Skipped | Failed |
|---|---|---|---|
| Baseline (`12ac6684`, session start — **measured, not carried over**) | 1051 | 23 | 0 |
| After Phase 1 (`33a42a24`) | 1068 | 23 | 0 |
| After compose entry (`fe4f8f9d`) | 1068 | 23 | 0 |
| After delivery-semantics correction (`6cf271f2`) | 1068 | 23 | 0 |
| Session end (`639c1cf0`) | **1068** | **28** | **0** |

Passed delta **+17**, all new tests from this session (14 in
`tests/decision_engine/test_resume.py`, 3 added to `tests/decision_engine/test_department_vocabulary.py`).
Skip delta **+5**, all from the new `tests/integration/test_decision_engine_consumer_redis.py`.
**Zero failures at every checkpoint. No test was modified to pass or to skip.**

Note the earlier section's baseline (982 passed / 23 skipped at `1dc3cb30`) is not comparable to this
one — 69 tests have landed on the branch between that commit and `12ac6684`.

## Every skip, classified (28 total)

| Count | Reason | Class |
|---|---|---|
| 15 | `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_APP_DB_URL` not set | Pre-existing — infra-gated, CI supplies Postgres |
| 9 | `SKYLIZE_TEST_REDIS_URL not set` | 4 pre-existing (`test_redis_bus.py`) + **5 new** (this session) — infra-gated, CI supplies Redis |
| 2 | `SKYLIZE_TEST_OPA_URL not set — requires a live OPA server` | Pre-existing — **see the warning below** |
| 1 | `runtime/ LLMAgentRunner ctor drifted; the runtime alt-stack is dead code with no tracked removal plan` | Pre-existing — dead code |
| 1 | `chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap (dead code, no tracked rework plan)` | Pre-existing — dead code |

**No skip reason references an unsourced label.** The two dead-code skips are worth calling out as
positive examples: both say *"no tracked removal plan"* / *"no tracked rework plan"* explicitly, rather
than citing an invented milestone to justify themselves. That is the correct pattern.

## Warning: the 2 OPA-gated skips are not merely inert — they are broken

`tests/decision_engine/test_opa_client_integration.py:59` and `:76` do:

```python
allow, deny_reasons = await client.evaluate(...)
```

but `OPAClient.evaluate` returns an `OPAResult` with **four** fields (`opa_client.py:82`,
`models.py:41-47`). The unpack raises `ValueError: too many values to unpack (expected 2)` — reproduced
directly against the real class.

`SKYLIZE_TEST_OPA_URL` is set in **no** CI configuration, so these two have skipped since they were
written and the breakage has never surfaced. The earlier section of this document records them as a
clean, accounted-for `+2` skip delta; that was accurate as bookkeeping and is the reason the defect
stayed invisible — **a skipped test reports the same as a passing one in a count.**

Not fixed here (this session's brief forbids modifying tests). Consequence if left: whoever stands up the
first OPA server has their only smoke test fail for a reason unrelated to their server. Tracked as
blocker **B6** in `OVERNIGHT_SESSION_2026-07-19.md` and **Q5** in `OWNER_DECISIONS_QUEUE_2026-07-19.md`.

## Second coverage caveat, self-reported

The 5 new Redis-gated tests have **never been executed against a real Redis** — written on a machine with
no Redis listener, no Docker and no `redis-server` binary. They are verified by reading the adapter only.
CI's integration job will be their first real run. The caveat is also written into the module docstring so
it travels with the code rather than only living in a report.

## Other gates at `639c1cf0`

| Gate | Result |
|---|---|
| `ruff check src/ tests/` | clean |
| `mypy src/skylize/decision_engine/ src/skylize/events/` | clean, 22 files |
| `scripts/check_forbidden_imports.py` | exit 0 — no LangChain/CrewAI |
| `scripts/check_all_modules_importable.py` | exit 0 — 196 modules |
| `scripts/find_orphan_modules.py` | exit 0 — no new orphans |
| `lint-imports` | **exit 1 — `4 kept, 1 broken`, pre-existing** (see Q1) |

`lint-imports` was red at `12ac6684` and is red at `639c1cf0` with the identical single violation;
`skylize.decision_engine` appears nowhere in its output. **Method note:** `python -m importlinter.cli
lint-imports` is a silent no-op that exits 0 and prints nothing — it must be run as the console script
`lint-imports` (which is what `.github/workflows/ci.yml:25` does). Any past "verified green" recorded via
the `-m` form verified nothing.
