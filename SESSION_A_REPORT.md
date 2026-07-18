# Session A Report — durable + runtime-proven governance spine (2026-07-15)

Branch: `feat/durable-governance`, cut from `chore/overnight-2026-07-14` @ 8ff18d17.
(The brief said "from release/console-m1 post-merge HEAD" — that merge never
happened, and every prerequisite of A1/A2 lives only on the overnight branch,
which contains release/console-m1. Logged in DECISIONS_PENDING.)
Nothing pushed; no other worktree touched; no merges performed.

## 1. Tasks done

| Task | Commit | What landed |
|---|---|---|
| A1 | 6136b5ef | `PgCapitalRepository` (reads the EXISTING `budget_ledger` from 0001 — no duplicate budget table) + `PgProcessedEventStore` (new RLS'd `decision_processed_events`), behind the existing ports. Migration 0011: processed-events table with the 0006/0010 policy+grant shape, plus the UNIQUE (org_id, scope, period) natural key `budget_ledger` was missing. Wired in the composition root on the postgres backend only; memory backend keeps in-memory defaults. `ProcessedEventStore` port methods now take `org_id` (keyword-only) — required for RLS; the engine had the tenant at every call site. Engine still never receives the LLM gateway. |
| A2 | 1ef9281d | Temporal worker entrypoint (`python -m skylize.app.orchestrator.temporal.worker`) implementing the documented recipe verbatim: `LLMJudge(container.llm)` + `PgWorkflowRepository(container.db)` into `WorkflowActivities`, served on `SKYLIZE_TEMPORAL_TASK_QUEUE` (default `skylize-workflows`). Fails closed on the memory backend. Signatures verified against installed temporalio 1.30.0 (`Client.connect`, `Worker(client, task_queue=, activities=)`; lazy clients cannot back a Worker, so live serving needs a real server). `Container` now exposes `db`. New settings: `temporal_address` / `temporal_namespace` / `temporal_task_queue`. |
| (found) | 90818fbd | **Real migration bug the proof gate exposed**: 0009's session-scoped `SET search_path TO public` leaked into every later migration of the same alembic run, so 0010/0011 escaped the disposable test schema and collided with public (`DuplicateTableError`). Fresh CI DBs passed silently-wrong. 0009 now restores env.py's path. |
| A3 | 9506c728 | Container closers tear down LIFO (ExitStack semantics): consumers/subscribers stop before db/redis close. Reproduced the failure live (clean `aclose()` on the postgres backend raised redis `ConnectionError`), then verified clean shutdown after the fix. Fixes the governance-subscriber quirk uniformly — trivial, no special cases, so no deferral needed. |

## 2. Real-backend proof gate — ALL FOUR EXECUTED, none env-gated away

Docker Desktop started; `docker compose -f infra/docker-compose.yml up -d postgres redis`
(postgres:16-alpine + redis:7 healthy). `alembic upgrade head` ran the FULL
chain 0001→0011 against that real DB — the first time 0010/0011 have run
outside CI. Then, as the NON-SUPERUSER `skylize_app` role (RLS-subject):

- **(a) Ceiling durability** — `test_ceiling_survives_restart`: seeded via
  `set_ceiling`, closed the pool entirely, fresh pool reads it back. PASSED.
- **(b) Replay dedup across restart** — `test_processed_marker_survives_restart`
  (store level: marker survives, first outcome sticks under redelivery) and
  `test_engine_restart_dedupes_replayed_event` (full path: same event into a
  REBUILT engine over a fresh pool emits zero additional decisions). PASSED.
- **(c) Workflow round-trip** — all 3 `test_workflow_repository.py` tests
  (every-field round-trip, SQL-NULL-vs-JSON-null, RLS isolation). PASSED.
- **(d) Judge fail-closes on injection** —
  `test_injection_payload_fails_closed_through_worker_wiring`: the payload goes
  through `build_activities(container)` — the exact composition the worker
  registers — and is blocked by the content gate before any provider egress. PASSED.
- Bonus runtime proof: `build_container(backend="postgres")` booted against the
  live stack, wires `PgCapitalRepository`/`PgProcessedEventStore` into the
  engine, composes the worker activities, and shuts down cleanly (post-A3).

To reproduce locally:
```
docker compose -f infra/docker-compose.yml up -d postgres redis
$env:SKYLIZE_DB_URL="postgresql://skylize:localdev@localhost:5432/skylize"
$env:SKYLIZE_APP_DB_PASSWORD="appdev"
python -m alembic upgrade head
$env:SKYLIZE_TEST_DB_URL="postgresql://skylize:localdev@localhost:5432/skylize"
$env:SKYLIZE_TEST_APP_DB_URL="postgresql://skylize_app:appdev@localhost:5432/skylize"
$env:SKYLIZE_TEST_REDIS_URL="redis://localhost:6379"
python -m pytest tests/integration -q
```
(The compose stack is still up on this machine.) The one thing NOT runtime-proven:
serving the Temporal task queue against a live Temporal server — none exists in
the compose file and lazy clients can't back a Worker. Run
`temporal server start-dev` then
`SKYLIZE_BACKEND=postgres python -m skylize.app.orchestrator.temporal.worker`.

## 3. Test delta + gates

- Baseline: 949 passed / 15 skipped. Final, with the real backend up:
  **972 passed / 2 skipped / 0 failed**. Without infra env vars: 953 passed /
  21 skipped (the 6 new store tests join the env-gated set; default run stays green).
- Formerly-skipped integration tests that now actually ran here: all 19
  (13 pre-existing incl. the T4 workflow-repo trio + 6 new). Remaining 2 skips
  are the known M5-scoped drift (memory_gateway, llm_agent_runner).
- New tests: +6 integration (durability/replay/RLS), +4 worker composition, and
  1 signature update in test_decision_engine.py.
- `check_forbidden_imports.py`: 0 violations. mypy strict src/: clean (192
  files). ruff src tests: clean. Orphan ratchet + all-modules-importable: clean
  (worker registered as a declared entrypoint). Key-leak grep over
  `8ff18d17..HEAD`: no matches (only compose's committed dev placeholders).

## 4. Is SKYLIZE_DECISION_ENGINE_ORG_IDS now safe to enable on postgres?

**Yes — for single-instance M1, with two caveats.** The two things that made it
unsafe are fixed and runtime-proven: budget ceilings live in `budget_ledger`
and survive restart (proof a); idempotency lives in `decision_processed_events`
and a replayed event is not re-decided even by a rebuilt process (proof b).
RLS binds on both tables as the real app role. Caveats: (1) the evaluator's
conflict-detection window (`DecisionEvaluator._recent`) is still in-process
per instance — fine single-instance, documented as a Scale concern in the code;
(2) ceilings must actually be seeded (`PgCapitalRepository.set_ceiling` or SQL) —
a missing ceiling fails closed to HITL, which is safe but noisy.

## 5. Stubbed / fragile

- `WorkflowActivities` is constructed with `builder=None, minter=None` — honest:
  the two registered activities don't consume them; they land with the workflow
  definitions. The worker registers activities only (no `@workflow.defn` exists
  yet anywhere in src/).
- `set_ceiling` is a helper on the concrete class, not on the read-only port —
  deliberate; nothing on the request path writes budgets yet.
- `get_ceiling` picks the most recent `period` row (capital_dal.py convention);
  BudgetCeiling carries no period, so multi-period orgs read newest-wins.
- Old `skylize.decision_engine/` module (capital_dal etc.) still uses the
  `department:{x}` scope vocabulary vs the wired engine's bare scope strings —
  pre-existing divergence, untouched.

## 6. A3

**Done** (9506c728), not deferred — A1+A2 were green, the bug reproduced live
during the proof gate, and the LIFO fix covers the governance subscriber's
identical quirk trivially rather than non-trivially.

---

## CORRECTION (2026-07-15) — "M5" is an unsourced term

This report refers to `memory_gateway` and `llm_agent_runner` as **"the known M5-scoped drift."** That framing is **unsourced**: no document in this repository defines "M5" or any "launch plan." The term was self-propagated across reports, test skip reasons, and config comments without a defining source.

The **underlying technical fact is unchanged and still accurate**: those modules are genuinely dead/unwired code with no tracked removal plan. Only the claim that they belong to a defined "M5" milestone was fabricated authority.

The live config/test comments carrying this framing were corrected on branch `fix/unsourced-m5-references`. This point-in-time report is left intact with this note appended rather than silently rewritten.
