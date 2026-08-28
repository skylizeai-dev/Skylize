# REPO_STATE_AUDIT_2026-08-12

Read-only audit. No source files modified except this report.

## HEAD state

- Branch: `feat/durable-governance`. HEAD: `f8ca461e2e480a724fea2b380213255e296b5f07`.
- `origin/feat/durable-governance` == `f8ca461e...` — **0 ahead / 0 behind**, fully pushed
  (`git rev-list --left-right --count origin/feat/durable-governance...HEAD` = `0 0`).
  **CONTRADICTS PRIOR CLAIM**: `docs/REPO_STATE.md:50` states "38 commits ahead / 0 behind
  ... all unpushed" at a prior commit. That is stale; the branch is now synced with its remote.
- vs `main` (`603936a0`): `git rev-list --left-right --count main...HEAD` = `0 123` — 123 ahead, 0 behind.
- vs `release/console-m1` (`128ac0f3`): `6 181` — 181 ahead, 6 behind.
- `git log --oneline --all --not --remotes` = 2 commits, both stash/index artifacts
  (`0b8bd0e` "stash stale egg-info regen", `4eb8a61` "index on feat/durable-governance"),
  not real unpushed work. REPO_STATE's "40 commits local-only" figure is stale/resolved.
- Working tree: 2 untracked files, both docs, nothing staged or modified:
  - `docs/04_decision_engine/policy_inputs.md` (413 lines) — DRAFT, matches REPO_STATE's prior note.
  - `docs/10_investor_materials/thiel_fellowship_technical_brief.md` (716 lines) — not previously noted in REPO_STATE.

## Test suite result

Ran `scripts/ci_unit_gate.ps1` (all 7 CI-parity gates) with **no** `SKYLIZE_TEST_*` env vars set
(confirmed unset before the run). Result: **1426 passed, 148 skipped, 0 failed, 109.49s.**
All 7 static gates PASS (ruff, lint-imports, forbidden-imports, all-modules-importable,
orphan-modules 13 known/allowlisted, mypy strict 221 files clean, pytest).

**SKIPPED: Postgres/Redis/OPA-backed tests did not run** — `SKYLIZE_TEST_DB_URL`,
`SKYLIZE_TEST_APP_DB_URL`, `SKYLIZE_TEST_OPA_URL`, `SKYLIZE_TEST_REDIS_URL` are all unset in
this session, so any test requiring live backends skips silently per CLAUDE.md's own warning.
148 skipped here vs. REPO_STATE's 2 skipped at `4c6f4511` (services up) — the gap (146 tests)
is the live-backend integration suite not running, not a regression. Do not treat 1426/148/0
as a money/tenancy/RLS-path result; it is a static+unit-only result.

## Merged since last known state (REPO_STATE baseline `4c6f4511`/`bf2703e`, 38 commits)

Full range `bf2703e..f8ca461` = 38 commits, landing (file:line evidence):
- **Principal kernel** (`aaa0a1f`, `75b7b58`, `229e6c3`, `891b68b`): `PostgresJournalRepository`
  added; `GET/POST /me/brief` wired read-only (`edge/routes/brief.py`).
- **on_behalf_of token** (`19734c6`, `31fd9a9`, `c59f42c`, `0aa4621`): principal claim binding +
  authority-freshness gate at token verification.
- **cowork surface** (`06d861a`, `49fad9b`, `fcabbc4`, `81c17c9`, `de02985`): `cowork_agent`
  AgentContract added (`src/skylize/contracts/mvp/cowork.py`, confirmed in
  `ALL_MVP_CONTRACTS` via `contracts/mvp/__init__.py:37`); chat endpoint now routes through
  `AgentExecutionService.execute()`; work journal gets two live non-transactional writers:
  `edge/routes/cowork.py:204` and `app/hitl/service.py:376` (confirmed by reading both files
  and `git show 6785312`).
- **Principal DAL**: `688b65a` `PgPrincipalRepository` implementing `PrincipalRepository`
  port; `a3e6c11` seeds owner principal + full-manifest grant.
- **Stale-comment fix** (`00e36b3`, `6785312`): `bootstrap.py:194-197` corrected — verified by
  reading current file content; comment now accurately cites both live writers with line
  numbers, matching actual code.
- **Test hardening** (`f8ca461`, current HEAD): adds
  `tests/integration/test_brief_endpoint.py::test_brief_is_scoped_to_principal_within_same_org`
  — pins that `principal_id` is sourced only from `ctx.user_id`, never path/query/body.
  This is an **integration** test (`tests/integration/`) requiring Postgres — it has not run
  this session (no `SKYLIZE_TEST_DB_URL`); its correctness is UNVERIFIED by this audit, only
  its presence in the diff is confirmed.

**ALL_MVP_CONTRACTS is now 22**, not 21 (confirmed by direct Python import:
`len(ALL_MVP_CONTRACTS) == 22`), because `ALL_COWORK_CONTRACTS` (`cowork_agent`) was added.
This further stales `contracts/registry.py:131`'s "15 governed" comment (already flagged
stale in REPO_STATE at 21; now the gap to reality is 22).

## Open blockers (current status)

- **`policy_inputs.md`**: untracked, `docs/04_decision_engine/policy_inputs.md:3` —
  `Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)`. No section reads `[APPROVED]`
  (grep for `[APPROVED]` finds none). Unchanged from REPO_STATE.
- **ADR-0004 (OPA production arbiter)**: unresolved. `bootstrap.py:367-373` still raises
  `RuntimeError` for any `SKYLIZE_DECISION_ENGINE != "inline"`. Comment at
  `bootstrap.py:352-366` unchanged in substance from REPO_STATE's citation.
- **ADR-0005 blockers**: department table, `hitl_id` reconciliation, and OPA resume all cited
  as landed in REPO_STATE (STALE CLAIMS #5); not re-verified line-by-line this session —
  UNVERIFIED (would need re-reading `decision_engine/constants.py`, `publisher.py`,
  `decision_engine/resume.py`).
- **`integration_inputs.md`**: `find` over the repo (excluding `.git`, `node_modules`) returns
  **zero matches** for any file named `*integration_input*`. ABSENT, confirmed.
- **API key env vars (existence only)**: `.env` file itself is **absent** from the working
  directory (`.env` NOT PRESENT). `.env.example` (tracked template, not real config) declares
  keys `SKYLIZE_ANTHROPIC_API_KEY`, `SKYLIZE_OPENAI_API_KEY`, `SKYLIZE_JWT_SECRET`,
  `SKYLIZE_N8N_API_KEY`, `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET` as empty placeholders — this says
  nothing about whether real secrets exist in the process environment or a deploy target;
  UNVERIFIED beyond the template file.
- **`evt:{tenant}:decision` consumer wiring**: only one hit repo-wide, a docstring reference
  at `src/skylize/decision_engine/outbox_poller.py:119` describing the stream-key format.
  No other construction/subscription site found in `src/`. Consistent with REPO_STATE's "OPA
  package not wired into API process" (NOT WIRED #1) — the consumer exists only in the
  separate worker process, not confirmed live in this session (worker not started).
- **`SKYLIZE_AGENT_PROMPTS_HMAC_SECRET`**: **zero occurrences** anywhere in `src/` (grep
  returned no matches, including in `edge/routes/agent_prompts.py`). REPO_STATE's own text
  (`WIRED` section) describes `agent_prompts.py` auth as `X-Skylize-API-Key ==
  settings.n8n_api_key` (static key), not HMAC. If an HMAC-secret-based auth scheme was ever
  planned for this route, it is **UNVERIFIED / not present in code** — flag as a possible
  stale expectation carried in the audit brief itself, not a repo fact.

## Owner-decision-required items outstanding

(List only, per brief — no framing.)
1. Approve `docs/04_decision_engine/policy_inputs.md` (per-section `[APPROVED]` banner).
2. OPA production enablement (`SKYLIZE_DECISION_ENGINE=opa`).
3. Root `CLAUDE.md` — **now present** (this session read it; it exists at repo root). This
   contradicts REPO_STATE OWNER DECISIONS #3 ("ABSENT at repo root"). **CONTRADICTS PRIOR
   CLAIM** — resolved since REPO_STATE was written.
4. n8n admin governed rewrite (ADR-0003 §3) before `SKYLIZE_ENABLE_N8N_ADMIN=true` in production.
5. `fix/c3-investor-status` sign-off (not re-verified this session; branch not checked out here).

Marker sweep (`TODO|FIXME|OWNER-DECISION-REQUIRED|STOP_ON_ARCHITECTURE_CONFLICT` across
`src/`, `tests/`): **one hit**, `tests/decision_engine/test_opa_client.py:399`, a comment
referencing `policy_inputs §0.2 marks [OWNER-DECISION-REQUIRED]` — points back to item 1 above,
not a separate marker.

## Contradictions found vs. prior claims (REPO_STATE.md)

1. **Push state**: REPO_STATE said `feat/durable-governance` was 38 commits ahead of its
   remote, all unpushed. Now 0 ahead / 0 behind — branch has been pushed since.
2. **Root CLAUDE.md**: REPO_STATE said absent. It exists and was read at session start (this
   audit's own instructions were sourced from it).
3. **Local-only commit count**: REPO_STATE said "40 commits exist only on this machine."
   Now only 2, and both are stash/index bookkeeping artifacts, not feature work — the real
   figure for actual unpushed work is effectively 0 on this branch.
4. **`chief_security_officer` / MVP registry**: REPO_STATE (`NOT WIRED` #3, test skip reason)
   says the contract "not in MVP registry." Confirmed still true — `safety.py` defines
   `chief_security_officer` but `contracts/mvp/__init__.py` never imports `ALL_SECURITY_CONTRACTS`
   beyond `fraud_detection_agent` (`security.py:38`: `ALL_SECURITY_CONTRACTS = [fraud_detection_agent]`).
   Not a contradiction — confirms REPO_STATE's claim still holds.

## Dead/unused code flagged

- `src/skylize/decision_engine/__pycache__/outbox_poller.cpython-312.pyc` and
  `...cpython-314.pyc` — stale compiled bytecode artifacts on disk (matched by grep as binary
  hits). Not source; `__pycache__` is gitignored. Flagging only because they surfaced in the
  search — not a code issue, a local build-artifact leftover.
- Skip markers unchanged from REPO_STATE, re-confirmed by reading both files directly this
  session: `tests/unit/test_llm_agent_runner.py:61` (`runtime/` alt-stack dead, no removal
  plan) and `tests/unit/test_memory_gateway.py:79` (`chief_security_officer` not in registry).
- `ALL_MVP_CONTRACTS` is 22 at this HEAD (not 21) — `contracts/registry.py:131`'s "15 governed"
  comment is stale by a wider margin than REPO_STATE recorded.

## Not re-verified this session (out of scope / UNVERIFIED)

- Full ADR-0005 blocker-resolution claims (department table, hitl_id, resume path) — not
  re-read line by line.
- Live-backend (Postgres/Redis/OPA) test results — SKIPPED: no `SKYLIZE_TEST_*` env vars set
  this session, so money/tenancy/RLS paths were not exercised.
- Real (non-template) presence of API key secrets in any deployed environment.
- OPA worker process consumer wiring at runtime (only static grep performed; worker not started).
