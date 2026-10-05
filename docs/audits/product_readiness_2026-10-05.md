# Skylize — Product Readiness vs Three Bars (Technical Due-Diligence Audit)

> **DO NOT MERGE UNTIL OWNER REVIEW.** Read-only audit. The only file written by
> this audit is this document. No `src/` file was modified.

## 1. Header

- **Audited SHA:** `0fc9904689039fe100cc327684d158fa200c029e` — `origin/main` HEAD at audit start (`chore(generators): stop silent vc_procurement rename + honest budget labeling (#21)`, 2026-09-29). Confirmed `git rev-parse origin/main` == audited SHA.
- **Date:** 2026-10-05
- **Auditor worktree:** `C:/Users/HP/Documents/Work/skylize-audit-readiness` on branch `feat/audit-product-readiness` (cut from the audited SHA).
- **Method:** code is ground truth; docs/ADRs/prior sessions are hypotheses. Every matrix row carries `file:line` or a real command/log. Subagents gathered file:line evidence on disjoint scopes; the lead re-verified (spot-checked >10 cited lines) and did all grading.
- **Branch note:** the local `fix/notifications-fk-cascade` is **2 commits behind** `origin/main` and fully contained in it (not ahead). `origin/main` already contains My Day Stage 1 (#27 `646e91c`) as an ancestor; the non-monotonic PR numbers are a trunk-reconciliation artifact, not missing work.

### Gates actually run (real results)

| Gate / command | Result |
|---|---|
| `gh run view 36600901545` — **CI (ci.yml)** on HEAD | **SUCCESS** 4m14s; jobs `unit` ✓, `integration` ✓ (4m3s), `roster` ✓, `console-black` ✓, `website` ✓ |
| `gh run view 36600903315` — **Deploy → Staging** on HEAD | **FAILURE**; `Lint & Test` ✓, **`Integration Tests` ✗**, `Build & Push` / `Deploy to ECS` skipped |
| `gh run list --workflow deploy-staging.yml --limit 40` | **40/40 FAILURE** (every run since 2026-07-12; never once green) |
| `PYTHONPATH=src python -c "...MVP_REGISTRY..."` (audit worktree) | `MODULE_PATH` = audit worktree src; **COUNT=23** contracts; `validate_schemas()` raised nothing |
| `find policy -name '*.rego'` + grep `allow := true` | 7 files, 128 lines, **0 `allow := true`** (all fail-closed placeholders) |

Not run (out of scope / unavailable here): whole-suite `pytest` (known hang — forbidden by brief), live AWS/Railway state, a real-Anthropic call. These are listed under **Unknowns** with the access each needs.

---

## 2. Verdict

- **BAR 1 — CFO Test demo (staging, real backend, no mocks):** **NOT READY.** ~4 blocking items. Critical path **M (1–3 days)**. The governance/HITL/audit/ledger/kill-switch vertical is real and e2e-proven *in CI*; what is missing is a **working deployed staging environment** (automated ECS deploy is 40/40 red) and a single **live real-Anthropic** run with a governed org enrolled.
- **BAR 2 — Design-partner pilot (prod ECS us-east-1, real identity, real tool, isolated data):** **NOT READY.** ~9 blocking items. Critical path **XL (>2 weeks)**. No working deploy anywhere, **no production IaC** (staging-only Terraform), observability absent, isolation unproven for 2 tables, connectors never exercised live, dev_auth default, pilot UX (My Day app) is pure mock.
- **BAR 3 — Paid enterprise (billing live, policy settled, isolation proven, DR):** **NOT READY.** ~8 blocking items + 3 owner decisions. Critical path **XL**. Billing has **no plans/invoices/charging** (usage only), the **policy engine is owner-blocked** (ADR-0004 unresolved; OPA is fail-closed placeholders), isolation has test gaps, no runbooks/backup-restore evidence.

Distance is expressed as **blocking-item count + critical-path effort**, never a percentage. Effort labels are **ESTIMATE** ranges (S=<1d, M=1–3d, L=1–2w, XL=>2w).

---

## 3. Capability matrix

Status vocabulary: SHIPPED-VERIFIED | SHIPPED-UNPROVEN | PARTIAL | MOCK | ABSENT | BLOCKED-OWNER | DOC-DRIFT.

| Capability | Status | Evidence (file:line / log) | Blocks | Effort | Owner? |
|---|---|---|---|---|---|
| Inline decision evaluator (6-stage, deterministic) | SHIPPED-VERIFIED | `app/decision_engine/evaluator.py:109-195`; e2e `tests/integration/test_agent_execute_governed_e2e.py:237`; CI run 36600901545 integration ✓ | — | — | N |
| Sync governance gate on `/agents/execute` | SHIPPED-VERIFIED | `app/agents/execution.py:350-355,589-670`; route `edge/routes/agents.py:58-211` | — | — | N |
| HITL defer→row-first→event ordering (D3) | SHIPPED-VERIFIED | `execution.py:833-901` (row before event); CFO e2e `test_cfo_deliverable_e2e.py:296-330` | — | — | N |
| HITL approve replay (resumption) | SHIPPED-VERIFIED | migration `0027_hitl_resumption_json.py:1`; `execution.py:790-831`; `test_cfo_deliverable_e2e.py:323-391` | — | — | N |
| Governance token mint + clamp `min(agent,principal)` | SHIPPED-VERIFIED | `app/governance/authority.py:290-294,336-355` (`attenuate_level` + `governance.authority_attenuated` audit) | — | — | N |
| Pre-egress token validation (sig/expiry/revocation/scope/budget) | SHIPPED-VERIFIED | `execution.py:419-447` | — | — | N |
| App-level kill switch (agent/dept/tenant/platform) | SHIPPED-VERIFIED | `edge/routes/kill_switch.py:26-55` → `authority.engage_kill_switch`; owner-only | — | — | N |
| ToolProxy spend reserve→settle (actual spend) | SHIPPED-VERIFIED | `tools/proxy.py:111-161`; migrations `0019_principal_authority.py`, `0030_spend_reservation_replay_key.py` | — | — | N |
| Org spend-ceiling pre-egress enforcement | PARTIAL | enforcer `bootstrap.py:840-854`; route 403 `edge/routes/agents.py:135-168`; e2e seeds BIG ceiling so over-ceiling 403 not exercised (`test_agent_execute_governed_e2e.py:139-143`) | 1 | S | N |
| Append-only AI cost ledger (micros) | SHIPPED-VERIFIED | `dal/cost_ledger.py`; migration `0012_ai_cost_ledger.py:178-185` (FORCE RLS); 2-row assert `test_cfo_deliverable_e2e.py:398-411` | — | — | N |
| GCP compute kill switch (stop instance) | SHIPPED-UNPROVEN / dormant | wired only if WIF set `bootstrap.py:915-926`; e2e `tests/integration/test_gcp_killswitch_e2e_pg.py`; off by default | 1(B) | — | Y |
| OPA/Rego production arbiter | BLOCKED-OWNER | 7 fail-closed placeholder regos (0 `allow := true`) `policy/skylize/decision/*.rego`; `bootstrap.py:755-761` RuntimeError on non-inline; `policy_inputs.md:3` DRAFT | 3 | L | Y |
| Real user auth (register/login/refresh/me, HS256, rotation+revoke) | SHIPPED-UNPROVEN | `app/auth/user_service.py:55,179,189-206`; `app/auth/tokens.py:33-56`; routes `edge/routes/auth.py`; mounted `gateway.py:91` | 2,3 | — | N |
| dev_auth header trust (default ON) | SHIPPED-VERIFIED (dev only) | `config.py:92` default `True`; `edge/auth.py:39-50`; refused on non-memory backend `config.py:356-360` | 2 | S | N |
| Production OIDC JWT verification | SHIPPED-UNPROVEN | `edge/auth.py:52-76` (verifies against `oidc_jwks_url`) | 2 | M | Y |
| My Day Stage 1 identity BFF (isolated cookie) | PARTIAL | `website/src/app/api/my-day/{session,me}/route.ts`; AES-256-GCM cookie `website/src/lib/skylize/myday-session.ts:129-234` | 2 | — | N |
| → My Day login CSRF protection | ABSENT | no Origin/Referer/CSRF check `website/src/app/api/my-day/session/route.ts:40-42`; wrapper `myday-handler.ts:144-210` | 2,3 | S | N |
| → My Day server-side logout revocation | ABSENT | DELETE only clears cookie `.../session/route.ts:70-78` (no backend revoke) | 2,3 | S | N |
| My Day Stage 2 app (useMyDayState) | MOCK | `app/Skylize-My Day/src/hooks/useMyDayState.js:7-125` hardcoded literals; no `fetch`/`/api` in `app/Skylize-My Day/src` | 2 | L | Y |
| Cross-tenant RLS (ENABLE+FORCE, ~40 tables) | SHIPPED-VERIFIED | loop `0001_initial_schema.py:351-365`; per-table 0006/0007/0012/0014/0019/0021/0022/0024-0026/0028/0032-0036; boot guard `bootstrap.py:296-341,587` | — | — | N |
| Cross-org DATA-denial PROVEN (per table) | SHIPPED-VERIFIED | audit_log `test_postgres_isolation.py:79-104`; ai_cost_ledger `test_cost_ledger_pg.py:161-183`; notifications/oauth/perm-grants/workflow_runs/github (`test_*_pg.py`); hitl at route `test_hitl_approval_e2e.py:292` | — | — | N |
| Cross-org DATA-denial for deliverables / org_credentials | ABSENT (DB layer) | deliverables: only memory-backend route 404 `tests/unit/test_download_endpoint.py:96`; org_credentials: no isolation test | 2,3 | S | N |
| users table isolation | SHIPPED-VERIFIED (JWT-claim, not RLS) | `0008_users.py:65` "no RLS"; `dal/users.py` all via `admin_session()` | 3 | — | N |
| Connectors: Drive / Asana / Notion tools | SHIPPED-UNPROVEN | registered when OAuth creds set `tools/builtin/__init__.py:57-68`; real httpx `drive_tools.py:218+`, `asana_tools.py:283+`, `notion_tools.py:320+`; never exercised live | 2 | M | Y |
| Connector: HubSpot tools | SHIPPED-UNPROVEN + hazard | `builtin/__init__.py:53-54`; real POST `hubspot_tools.py:101-128`; **no idempotency key under @retry** `:95-104` → double-create | 2 | S | N |
| Connector: Stripe refund | PARTIAL (built, unwired) | tool `stripe_tools.py:131-233` registers only w/ repo+factory `builtin/__init__.py:83-86`; `bootstrap.py:929-932` passes neither; real HTTP `app/stripe/actions.py:238-248` unreachable; schema `0036` | 3 | M | Y |
| Connector: Slack as agent tool | ABSENT | only `SlackApprovalNotifier` (HITL posts) `app/notifications/slack.py:34`, `bootstrap.py:946-951`; no ToolDefinition | 2 | — | N |
| Connector: GitHub as agent tool | ABSENT (foundation only) | `bootstrap.py:428-429` "no tool is registered against it"; key loader `app/github/keys.py`; migration `0026` | 2 | M | Y |
| Agent web search / memory recall tools | MOCK | default Null ports `tools/builtin/__init__.py:48-50`; `bootstrap.py:929-932` passes no port | 2 | M | N |
| OAuth connectors fail-closed when unconfigured | SHIPPED-VERIFIED | resolvers `bootstrap.py:200-293`; ToolProxy deny `tools/proxy.py:587-608` | — | — | N |
| Billing usage (ledger-backed) | SHIPPED-VERIFIED | `edge/routes/billing.py` GET `/api/v1/billing/usage`; console `app/Skylize-Console-Black/src/screens/Billing.jsx:4-14` | — | — | N |
| Billing plans / invoices / seats / charging | ABSENT (by design, named) | `billing.py:64-68` `UNAVAILABLE_SECTIONS=(plan_tier,invoices,seats,agent_slots)`; no write verb | 3 | XL | Y |
| Console-Black ops SPA (live BFF) | PARTIAL | ~20 endpoints via `consoleClient.js`; some panels unwired: kill-switch disengage `useConsoleState.js:860-864`, Command delegation `screens/Command.jsx:151`, Team invite `screens/Team.jsx:26` | 2 | M | N |
| Marketing My Day page routes | PARTIAL (illustrative) | 5 non-navigable captions `website/src/app/(site)/my-day/page.tsx:43-95`; page labeled sample `:28` | — | S | N |
| Autonomous runs (scheduled) | SHIPPED-UNPROVEN / inert | 2 pilot agents only `app/autonomy/pilot.py:128-138,241-248`; no trigger registered `bootstrap.py:998-1008` | 2,3 | L | Y |
| Delegation: act on behalf of SELF | SHIPPED-VERIFIED | cowork turn `edge/routes/cowork.py:104` passes `on_behalf_of_principal=ctx.user_id`; mounted `gateway.py:115` | — | — | N |
| Delegation: act as ANOTHER principal | ABSENT (net-new) | forbidden by design `edge/routes/cowork.py:23-27,47-50` | 3 | L | Y |
| Tenant provisioning | SHIPPED-VERIFIED (operator) | `edge/routes/tenants.py:45-61` needs pre-auth context w/ org_id; not self-serve | 2 | M | Y |
| Credential vault (Fernet at-rest, fail-closed) | SHIPPED-VERIFIED | `bootstrap.py:106-164`; migration `0007` FORCE RLS | — | — | N |
| Automated deploy → staging (ECS us-east-1) | ABSENT (never succeeded) | `deploy-staging.yml`; `gh run list` = 40/40 FAILURE; current fail = integration PG password auth | 1,2 | M | partial |
| Production environment / IaC | ABSENT | `infra/terraform/staging/*` only; **no** `infra/terraform/production` | 2,3 | L | Y |
| Observability (metrics/traces/readyz) | ABSENT | only `GET /health` `gateway.py:85-87`; no OTel/Prometheus in gateway; langfuse not a dep (`pyproject.toml:13` comment only; 0 `import langfuse` in src) | 2,3 | M | N |
| CI fast gate (lint/types/imports/arch/unit) | SHIPPED-VERIFIED | `ci.yml:13-35`; mirrored by `scripts/ci_unit_gate.ps1:60-68`; run 36600901545 ✓ | — | — | N |
| Integration CI (real PG+Redis, no OPA) | SHIPPED-VERIFIED | `ci.yml:122-162` (`-m integration`); no `SKYLIZE_TEST_OPA_URL` → OPA tests self-skip | — | — | N |

---

## 4. CFO Test path trace

Scenario demoed: a budget proposal that governance **defers to a human**, the human **approves**, a deliverable with recomputed arithmetic is produced, every call is **budgeted/ledgered/audited**, and an owner can **kill-switch** the agent.

| # | Step | Real / Mock | Evidence |
|---|---|---|---|
| 1 | `POST /api/v1/agents/execute {agent_id:"cfo_agent"}` | **Real route** | `edge/routes/agents.py:58-95`; auth `require_any_role_or_user("owner","admin","operator")` |
| 2 | Auth | **Mock in tests / dev in demo** | e2e uses `install_dev_header_auth` (X-Dev-* headers), not real user JWT (`test_cfo_deliverable_e2e.py:205`) |
| 3 | `AgentExecutionService.execute` → gate at step 2.5 | **Real** | `execution.py:350-355` (`org_id in governed_org_ids and hitl_approval is None`) |
| 4 | Gate requires org enrolled | **Config-gated** | `config.py:266` `decision_engine_org_ids=[]` default → gate dormant unless org enrolled |
| 5 | `_govern` → `evaluator.evaluate` | **Real** | `execution.py:627-630`; sync vertical `evaluator.py:150-151,198-281` decides on trigger presence |
| 6 | cfo_agent triggers `[SPEND_OVER_CEILING, LOW_CONFIDENCE_IRREVERSIBLE]` → **defer** | **Real** | contract asserted `test_cfo_deliverable_e2e.py:150-158`; defer `evaluator.py:268-281` |
| 7 | `_defer_to_human`: hitl_queue row **first**, then terminal event + audit, raise | **Real** | `execution.py:868` (row), `:884` (emit), `:898` (raise) |
| 8 | Route → **202** `{hitl_id}`; no deliverable/ledger/LLM yet | **Real (asserted)** | `agents.py:110-122`; `test_cfo_deliverable_e2e.py:303-320` (fake.attempts unchanged, no rows) |
| 9 | `POST /api/v1/hitl/{id}/approve` → replay `execute()` (gate skipped) | **Real** | `test_cfo_deliverable_e2e.py:323-332`; `execution.py:272-355` |
| 10 | Mint P-384 token → **LLM call** | **Real code / FAKE provider in test** | fake Anthropic HTTP server `tests/fakes/fake_provider_api`; real `AnthropicAdapter` wired when key set `bootstrap.py:850-854` |
| 11 | Deterministic budget recompute (model arithmetic discarded) | **Real** | `execution.py:491-493`; asserted `test_cfo_deliverable_e2e.py:358-377` |
| 12 | Deliverable persisted + **2 ai_cost_ledger rows** + audit chain by `causation_id` | **Real (asserted)** | `test_cfo_deliverable_e2e.py:398-427` |
| 13 | Kill switch (owner) refuses subsequent mint → 403 | **Real** | `kill_switch.py:26-38`; `execution.py:419-447` → `GovernanceDenied` → `agents.py:123-132` |

**Where it breaks for a BAR-1 demo:** the logic is complete and CI-proven, but (a) **two mocks sit in the proven path** — a fake Anthropic HTTP server and dev-header auth; a real-Anthropic run has never been verified; and (b) there is **no working staging deployment** to run it on (ECS deploy 40/40 red). The demo also needs runtime config: the demo org in `SKYLIZE_DECISION_ENGINE_ORG_IDS`, a real `SKYLIZE_ANTHROPIC_API_KEY`, and a seeded spend ceiling.

---

## 5. Critical path per bar

**Owner-decision items are listed first** — they block more than code does.

### Owner decisions (cross-bar)
1. **Policy engine destiny (ADR-0004).** Approve `docs/04_decision_engine/policy_inputs.md` (DRAFT, `:3`) to unblock real Rego, OR formally adopt the inline evaluator as the arbiter and retitle the ADR. Until then OPA is fail-closed placeholders. *Blocks BAR 3.*
2. **Backend host conflict.** Decide Railway vs AWS ECS for the backend API. The frontend BFF targets a "Railway backend origin" (`website/src/lib/skylize/config.ts:14`) while the only automated deploy pipeline targets ECS (`deploy-staging.yml`). Conflict documented at `docs/MVP_READINESS_AUDIT_2026-09-06.md:375,544`. *Blocks BAR 1 & 2.*
3. **Pilot surface.** The My Day SPA (`app/Skylize-My Day`) is 100% mock; Console-Black is the live surface. Decide what a design partner actually touches. *Blocks BAR 2.*
4. **External registrations / sign-offs:** Google/Asana/Notion platform OAuth apps; GitHub App at GitHub.com; Stripe Connect sign-off (`stripe_tools.py:41-45` "NOT GRANTED TO ANY STATELESS AGENT"); enroll governed org(s). *Blocks BAR 2 & 3.*
5. **My Day Stage-1 security sign-off** before external users (CSRF + logout revocation). *Blocks BAR 2.*

### BAR 1 — engineering (ordered)
1. **Fix the staging deploy gate** — the `deploy-staging.yml` integration job fails on a Postgres password mismatch (admin/app both `testpass`) while `ci.yml` (`localdev`/`appdev`) passes; a SQLAlchemy activity-signal fixture's DSN doesn't honor the deploy job's password. [ESTIMATE S] → unblocks the whole pipeline.
2. Resolve owner decision #2 and stand up **one** working staging backend. [ESTIMATE M]
3. Enroll the demo org, set a real Anthropic key, seed a spend ceiling. [ESTIMATE S]
4. Run the CFO scenario once against real Anthropic on staging; capture the trace. [ESTIMATE S] → converts BAR-1 governance from CI-proven to staging-verified.

### BAR 2 — engineering (depends on BAR 1 deploy working)
5. Production IaC (`infra/terraform/production`) + secrets + migrations on the prod target. [ESTIMATE L]
6. Turn off dev_auth; wire OIDC; verify real end-user login end to end. [ESTIMATE M]
7. Register one real connector (Drive) and exercise a live governed tool call. [ESTIMATE M]
8. Close isolation test gaps (deliverables, org_credentials DB-layer cross-org denial). [ESTIMATE S]
9. Minimum observability (structured request logs + `/readyz` + error reporting) + an incident runbook. [ESTIMATE M]

### BAR 3 — engineering
10. Real billing: plan tiers, invoices, metered charging, Stripe wiring. [ESTIMATE XL]
11. Settle the policy engine per owner decision #1 (real Rego + live OPA, or inline-formalized). [ESTIMATE L]
12. Security questionnaire posture: pen test, backup + **tested** restore, data-retention for `resumption_json` cleartext (`execution.py:773-789`), SOC2-style controls. [ESTIMATE XL]

---

## 6. Hypotheses refuted (this section matters most)

| # | Hypothesis (from the brief) | Verdict | Evidence |
|---|---|---|---|
| H5 | "**33** registered contracts" | **REFUTED** → **23** | `PYTHONPATH=src python -c "...len(MVP_REGISTRY.all())"` = 23 from the audit worktree's own src; `contracts/mvp/__init__.py:4,28-42` |
| H5 | "**127-agent gap**", "160 target" | **REFUTED (no source)** | no literal "127"/"160" roster figure in repo; real computable gap = 23 live vs **151** doc roster (154 manifest rows) `docs/03_agents/00_organization_chart.md:177-200`; 8 code-only agents |
| H5 | "**32 unresolved schema paths**" | **REFUTED for live** | `validate_schemas()` raised nothing for all 23; `contracts/registry.py:104-108`; no source for "32" |
| H5 | "orphan tools bi.query, crm.read, crm.write" | **PARTLY CONFIRMED** | crm.write `contracts/mvp/sdr.py:23`, crm.read `:45` are real orphan grants (fail closed, no ToolDefinition); **bi.query is doc-only**, not in any live contract |
| H2 | "Slack, Drive, Asana, Notion, **GitHub shipped**" | **PARTLY REFUTED** | GitHub = **no tool** (`bootstrap.py:428-429`); Slack = **no tool** (only HITL notifier `slack.py:34`); Drive/Asana/Notion/HubSpot = real registered tools `tools/builtin/__init__.py:53-68` |
| H2 | "Notion rate-limit bucket not built" | **PARTLY REFUTED** | reactive 429/529 + Retry-After backoff IS built `notion_tools.py:119,332-371`; only a **proactive** token bucket is absent |
| H3 | "**User Auth epic not started** (dev_auth=true)" | **REFUTED** | full HS256 user auth with refresh rotation + server-side revocation is shipped & mounted `app/auth/user_service.py:189-206`, `edge/routes/auth.py`, `gateway.py:91`; dev_auth is merely the *default* |
| H3 | "My Day Stage 2 (useMyDayState.js) **not started**" | **PARTLY REFUTED** | `app/Skylize-My Day/src/hooks/useMyDayState.js` exists but is **100% mock** (hardcoded literals, no network) |
| H3 | "no server-side session revocation / refresh-rotation race" | **MIS-ATTRIBUTED** | TRUE for the **My Day BFF** (`session/route.ts:70-78`); the **backend** auth service *does* rotate + revoke `user_service.py:189-206` |
| H4 | "marketing my-day page advertises **9 routes**" | **PARTLY REFUTED** | **5** route-like captions, **non-navigable** (text, not links), page labeled illustrative `website/src/app/(site)/my-day/page.tsx:28,43-95` |
| H4 | "Console wired to **28 live data needs**" | **DOC-DRIFT** | "28" has no repo source; real ≈ 20 endpoints / 23 client fns `app/Skylize-Console-Black/src/lib/consoleClient.js`; some panels explicitly unwired |
| H9 | "two-org test proves per-user me only, **isolation only inferred**" | **REFUTED** | genuine cross-org DATA-denial tests exist & run in CI for audit_log/ai_cost_ledger/notifications/oauth/perm-grants/workflow_runs/github + hitl at route level; ~40 tables FORCE RLS |
| H9 | "users read via admin session (JWT-claim, not RLS)" | **CONFIRMED** | `0008_users.py:65` "no RLS"; `dal/users.py` all via `admin_session()` |
| H1 | "ToolProxy settles actual spend (**migration 0028**)" | **DETAIL WRONG** | settlement real (`proxy.py:111-161`) but migration is `0019`/`0030`; `0028` = `org_autonomy_mode` |
| H7 | "**Railway** staging; ECS us-east-1 production target" | **PARTLY WRONG** | automated pipeline = **ECS us-east-1 STAGING** (`deploy-staging.yml:13-16`); **no production IaC**; Railway config exists for frontend/OPA only; backend-host conflict unresolved |
| H8 | "main green (integration **~397 passed**)" | **PARTLY MISLEADING** | `ci.yml` green on HEAD, but **Deploy→Staging 40/40 red**; "397" has no source |
| H6 | "Docs/ADR claim OPA, ADR-0004 unresolved" | **CONFIRMED (nuance)** | still owner-unresolved; but ADR-0004 was **self-corrected 2026-08-28** to state inline is the only engine that has ever gated a decision |

---

## 7. DOC-DRIFT list (docs/ADRs/memory vs code)

1. **ADR-0004 stale line refs:** the 2026-08-28 correction cites `bootstrap.py:472-478` and `config.py:122`; the live guard is `bootstrap.py:755-761` and `config.py:260`. Substance holds; line numbers drifted.
2. **`docs/audits/console_state_audit.md`** is stale (Turkish, calls the console "%100 mock veri", `:16-20`) and does not describe the currently-wired Console-Black SPA.
3. **`docs/REPO_STATE.md`** describes commit `834153c9` (July) and self-declares staleness; many of its `file:line` refs have drifted by the Sep-29 HEAD (e.g. it says "21 agents" in one place; runtime is 23).
4. **Architecture conflict (recorded, not resolved — per STOP_ON_ARCHITECTURE_CONFLICT):** frontend BFF expects the backend on **Railway** (`website/src/lib/skylize/config.ts:14`) while the only automated deploy ships to **AWS ECS** (`deploy-staging.yml`). Noted in `docs/MVP_READINESS_AUDIT_2026-09-06.md:375,544`.
5. **ADR-0004 title vs reality** ("OPA *is* the production arbiter") — the running arbiter is the inline evaluator; the ADR's own correction section says so, but the title still overstates.
6. **CI gate parity is hand-kept:** `scripts/ci_unit_gate.ps1:44-45` self-documents that nothing enforces agreement with `ci.yml`, and it does not cover website/integration/roster jobs.

---

## 8. Due-diligence red flags (findable in ~30 minutes)

1. **The staging deploy pipeline has never succeeded — 40/40 red since 2026-07-12** (`gh run list --workflow deploy-staging.yml`). No image has reached ECS; there is no verified staging environment. No production IaC exists at all.
2. **`dev_auth=true` is the default** (`config.py:92`); the real-identity path (OIDC) has never been run against a live deploy. The newest identity surface (My Day BFF) **lacks login CSRF and server-side logout revocation** (`session/route.ts:40-42,70-78`), and the hardening branch for it **does not exist**.
3. **"OPA is the production governance arbiter" is aspirational** — 7 fail-closed placeholder regos, inline-only in every environment, `policy_inputs.md` still DRAFT awaiting owner approval.
4. **Mock/again-aspirational frontends:** the My Day app is 100% hardcoded fixtures; the marketing My Day page is illustrative; Console-Black has unwired panels (disengage, delegation, team invite).
5. **Roster inflation:** a 151-agent org chart vs **23** runnable contracts; two live contracts (`sdr_outreach_agent`, `lead_qualifier_agent`) grant **unimplemented** `crm.write`/`crm.read` tools (fail closed, but visible).
6. **Agents can't actually search the web or recall memory** — those tools default to **Null ports** (`tools/builtin/__init__.py:48-50`).
7. **No observability** beyond `GET /health`; Langfuse is referenced but is **not even a dependency** and the API process never emits a trace.
8. **Billing cannot bill** — usage only; plans/invoices/seats/charging are explicitly unavailable; Stripe is schema-only and the refund tool is unwired.
9. **Isolation has holes:** `deliverables` and `org_credentials` carry FORCE-RLS DDL but **no cross-org denial test at the DB layer** (deliverables' only isolation test is a memory-backend route 404).

**For balance (genuine strengths a diligence engineer should credit):** the governance core is unusually disciplined and *proven in CI* — P-384 signed tokens with `min(agent,principal)` clamping, a deterministic 6-stage evaluator, durable-row-before-event HITL ordering, pre-egress token validation, append-only micro-denominated cost ledger, `FORCE ROW LEVEL SECURITY` on ~40 tenant tables with a boot-time role guard that refuses to start as a SUPERUSER/BYPASSRLS role, real cross-org isolation tests, a content-injection gate on all LLM/knowledge egress, and architecture gates in CI (import-linter, forbidden-imports AST scan, orphan-module ratchet). The CFO vertical is end-to-end proven (`test_cfo_deliverable_e2e.py`).

---

## 9. Highest-ROI next 5 moves (bars unblocked ÷ effort)

1. **Fix the `deploy-staging.yml` integration password mismatch** [S] — one config/fixture fix flips a 40/40-red pipeline green and unblocks any staging demo. *Unblocks BAR 1 & opens BAR 2.* Highest ratio: smallest fix, largest gate.
2. **Owner decision + stand up one working staging backend (Railway vs ECS)** [M] — removes the single biggest ambiguity (where does the backend run?) and gives the CFO demo a home. *BAR 1 & 2.*
3. **Enroll a governed demo org + real Anthropic key + seeded ceiling, run the CFO scenario live once** [S] — converts the governance vertical from CI-proven to staging-verified with real provider egress; this is the actual BAR-1 deliverable. *BAR 1.*
4. **Close the two isolation test gaps (deliverables, org_credentials)** [S] — turns "isolation proven for most tables" into "proven", directly answering the security-questionnaire question. *BAR 2 & 3, cheaply.*
5. **Owner decision on the policy engine (approve `policy_inputs.md` → Rego, or formalize inline) and retitle ADR-0004** [owner S, Rego L] — settles the single largest standing architecture question and the one an investor will press hardest. *BAR 3.*

---

## 10. Unknowns (not guessed — with the exact check each needs)

1. **Is a Railway backend actually live and serving the console BFF?** Code points at a "Railway backend origin" but the ECS pipeline is dead. *Verify:* Railway dashboard / `railway status` + `curl https://<backend>/health`.
2. **Is `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY` (and the Anthropic key, JWT/OIDC secrets) set on the live env?** Code fails closed if unset on a real backend, so a running backend implies it is set — but the value isn't repo-verifiable. *Verify:* Railway/ECS env vars (`railway variables` / `aws ecs describe-task-definition`).
3. **Has a real Anthropic call ever been made from a live Skylize process?** All tests use a fake provider HTTP server. *Verify:* a staging run + provider dashboard / cost-ledger rows with a real `message.id`.
4. **Is the ECS `skylize-staging` service actually running anything** (from a prior manual `deploy.ps1`)? The automated pipeline never deployed. *Verify:* `aws ecs describe-services --cluster skylize-staging` + the ALB `/health`.
5. **Is the GitHub App actually registered at GitHub.com?** Only the key-loader exists in repo. *Verify:* GitHub org App settings.
6. **Exact current full-suite pass/skip counts** — not run here (whole-suite `pytest` hangs per the brief; CI `ci.yml` is green on HEAD but prints no names). *Verify:* `gh run view 36600901545 --log` for the `-rA` integration summary, or a gated per-file run.
7. **Backups / restore / runbooks / incident tooling** — no artifacts found in repo. *Verify:* owner confirmation of RDS automated backups + a tested restore; existence of an on-call runbook.
8. **The "32 unresolved schema paths" source** — not found among live contracts (0 unresolved). If it refers to the 151-agent doc roster, those schemas aren't loaded at runtime. *Verify:* the specific audit doc or generator the figure came from.

---

*Prepared read-only at `0fc9904`. All grading is the lead auditor's; subagent inputs were re-verified against code before inclusion. This document lives under `docs/audits/`, outside the `gen_manifest.js` walker root (`docs/03_agents`), so it is not picked up as a phantom agent.*
