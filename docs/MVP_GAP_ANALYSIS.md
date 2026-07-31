# MVP GAP ANALYSIS — what stands between this repo and a payable product

**Generated:** 2026-07-30 by a read-only audit. No source file was modified; the only write is this file.
**Describes commit:** `22aeffed8250710629d7395a797ee24f43d5cf31` on branch `feat/durable-governance`.
**Method:** code is ground truth. Docs, ADRs and comments are claims tested against code. Every line carries a `file:line`, or an explicit **ABSENT** / **UNVERIFIED**. Where `docs/REPO_STATE.md` and the code disagree, the code wins and the disagreement is reported.
**Scope:** this is not an inventory (see `docs/REPO_STATE.md` for that). It asks three questions: what can a paying customer do today, what breaks first, and what the two thresholds — demo and pilot — actually require.
**Not in scope:** sequence. No roadmap, no schedule, no recommendation about what to do first.

---

## OPENS

```
SELF-SERVE TO FIRST EXECUTION: no        -- per-customer manual steps: 5
EVER DEPLOYED:                 no
CI EVER RUN ON THIS WORK:      UNVERIFIED  (deploy workflow: never)
MONEY PATH PROVEN IN CI:       yes (ci.yml config) -- never proven to have RUN
GATEWAY CONTAINER IMAGE:       exists (two, divergent)
TABLES WITHOUT TENANT ISOLATION: 6 of 25 hold customer data
AGENTS WITH FULL-PATH TESTS:   3 of 21   -- mocked-only: 0
AGENTS TYPED BY OMISSION:      0 of 21   -- was 11
DEMO MODE: 7 of 21 demoable; 14 raise a typed error naming themselves
DEMO BAR GAPS: 8   --   PILOT BAR GAPS: 22
EXPOSURE ITEMS: 15
```

---

## PART 1 — THE CUSTOMER JOURNEY

### 1. Account creation — **WORKS**

`POST /api/v1/auth/register` (`src/skylize/edge/routes/auth.py:80-101`) takes **no authentication**. Body is `{org_id, email, password, display_name?}` (`auth.py:31-36`). `org_id` is normalised and slug-validated (`auth.py:38-49`).

It writes **one row in `users`** and nothing else (`src/skylize/app/auth/user_service.py:53-65`). It does **not** create a `tenants` row, a `tenant_users` row, a spend ceiling, or credentials.

Role assignment: **first user in the org becomes `owner`; every subsequent user becomes `viewer`** (`user_service.py:49-51`). There is no check that `org_id` exists, and no check that the registrant is entitled to that org. A stranger registering with an existing org's `org_id` is silently admitted to that tenant as a `viewer`, and `viewer` is an accepted role on `GET /api/v1/agents` (`edge/routes/agents.py:121`) and every deliverable read route (`edge/routes/deliverables.py:120,151,221,237`).

### 2. Org provisioning — **ABSENT (manual)**

Nothing in `src/` provisions an org beyond the `users` row. Specifically:

| Required for a working org | Automated? | Evidence |
|---|---|---|
| `tenants` row | no — a separate authenticated call, `POST /api/v1/tenants` (`edge/routes/tenants.py:45-61`), which reads `org_id` from the context, not the body | `tenants.py:48` |
| `org_spend_ceiling` row | **no — there is deliberately no write endpoint** | `edge/routes/spend.py:9-10`: "Setting a ceiling stays an operator action through the audited set_ceiling seam — there is deliberately no write endpoint here." Only caller of `OrgSpendCeilingDAL.set_ceiling` (`dal/org_spend_ceiling.py:93`) anywhere outside tests is **none** |
| membership of `decision_engine_org_ids` | **no — static process config** | `config.py:113`, read once at `bootstrap.py:299` and frozen into the service at `bootstrap.py:384` |
| credentials / API key | partially — `POST /api/v1/api-keys` exists but is unreachable with a registration-issued JWT (see §3) | `edge/routes/api_keys.py:56` |

The spend ceiling is the load-bearing one. On the postgres backend with a real Anthropic key, `SpendCeilingEnforcer.enforce` reads the ceiling and **refuses the call when no row resolves** (`adapters/llm/spend_ceiling.py:203-218`, owner decision D6). The lookup is effective-dated — the greatest `billing_period <= now` (`dal/org_spend_ceiling.py:79-91`) — so this is a **one-time** per-org action, not a monthly one. But until it happens, every LLM call from that org is refused.

**These are manual operator steps.** Setting a ceiling requires running Python against `OrgSpendCeilingDAL.set_ceiling` or writing SQL. Adding an org to the governed set requires editing an environment variable and restarting the process.

### 3. Authentication — **PARTIAL**

Four distinct mechanisms exist. They are not interchangeable, and which routes accept which is the central self-serve blocker.

| Mechanism | Issued by | Expiry | Revocation | Self-service? |
|---|---|---|---|---|
| **`X-Dev-Org` / `X-Dev-User` / `X-Dev-Roles` headers** | nobody — trusted verbatim when `dev_auth` is on (`edge/auth.py:39-50`) | n/a | n/a | n/a — this is not authentication. Any caller asserts any org and any role. **`dev_auth` defaults to `True`** (`config.py:56`) and nothing forces it off when `backend="postgres"` (the only two boot validators are `config.py:186-195` and `config.py:197-205`) |
| **Skylize access JWT** (HS256) | `POST /api/v1/auth/login` (`edge/routes/auth.py:104-118`) | 30 min (`config.py:74`) | none — access tokens are not revocable; only the refresh token is (`user_service.py:95`) | **yes** |
| **Skylize refresh JWT** | login / `POST /api/v1/auth/refresh` (`auth.py:121-133`) | 14 days (`config.py:75`) | rotation revokes the consumed token (`user_service.py:94-95`); no user-facing revoke endpoint | **yes** |
| **API key** `sky.<prefix>.<secret>` | `POST /api/v1/api-keys` (`edge/routes/api_keys.py:53-69`) | optional, 1-365 days; **`None` = never expires** (`api_keys.py:29,59-61`) | `DELETE /api/v1/api-keys/{id}` (`api_keys.py:87-99`), checked at auth (`app/auth/service.py:84`) | **no — see below** |
| **OIDC bearer** | an external IdP | per IdP | per IdP | n/a — `oidc_jwks_url` defaults to `""` (`config.py:57`); with `dev_auth=false` and no JWKS URL, `build_request_context` raises `AuthError` for every caller (`edge/auth.py:53-69`) |

The blocking split: only routes using `require_any_role_or_user` / `get_current_user` accept a Skylize JWT (`edge/deps.py:167-171`, `deps.py:96-123`). Routes using `get_context` / `require_any_role` / `require_role` / `enforce_rate_limit` do **not**.

- **Accept the JWT:** agents (`agents.py:60,121`), deliverables (`deliverables.py:93,120,151,167,187,206,221,237`), hitl (`hitl.py:86,107,157`), audit (`audit.py:48`), spend (`spend.py:58`), `/auth/me` (`auth.py:138`).
- **Reject the JWT:** api-keys (`api_keys.py:56,74,90`), tenants (`tenants.py:48,66,77,92,107,120,136`), credentials (`credentials.py:62,78,89,117`), kill-switch (`kill_switch.py:29,43`), knowledge (`knowledge.py:178,214,236`), workflows (`workflows.py:47`).

### 4. API key issuance — **PARTIAL, with a bootstrap paradox**

`POST /api/v1/api-keys` requires `owner` or `admin` via `require_any_role` (`api_keys.py:56`), which resolves through `get_context` (`deps.py:135-143`) — API key, OIDC, or `X-Dev-*` headers. It does **not** accept the Skylize access JWT a fresh registration produces.

Consequence with `dev_auth=false` and no OIDC (the intended production posture, and what the ECS task definition sets — `infra/terraform/staging/modules/ecs/main.tf:75`): **you need an API key to mint an API key.** There is no path from `/auth/register` to a first API key. With `dev_auth=true` it works, but then the `X-Dev-*` headers are themselves an unauthenticated impersonation of any org and role.

Secondary note: `scopes` become `roles` verbatim (`app/auth/service.py:95`) with no validation, so an `admin` can mint a key carrying `owner`.

### 5. First execution — **PARTIAL**

Path: `POST /api/v1/agents/execute` (`edge/routes/agents.py:57`) → `AgentExecutionService.execute` (`app/agents/execution.py:183`) → contract resolve (`:193`) → input validation (`:199-203`) → decision gate **only if `org_id in governed_org_ids`** (`:218`) → governance mint + pre-egress `validate_tool_call` (`:249-295`) → LLM (`:309`) → output validation (`:331`) → `create_deliverable` (`:354`) → audit (`:366`).

Preconditions that must already be true:

| # | Precondition | Satisfiable via API/console? |
|---|---|---|
| 1 | A credential the route accepts: Skylize JWT with role owner/admin/operator, or an API key with those scopes, or `X-Dev-*` headers | **yes** (JWT) |
| 2 | `SKYLIZE_ANTHROPIC_API_KEY` set, or `SKYLIZE_LLM_DEMO_MODE=true` — otherwise the container refuses to build (`bootstrap.py:346-352`) | **no** — process config |
| 3 | An `org_spend_ceiling` row (postgres + real key only) | **no** — no write endpoint (`spend.py:9-10`) |
| 4 | A `model_pricing` row for the concrete model — else `LLMModelNotPriced` before egress (`adapters/llm/anthropic_adapter.py:327-332`) | seeded by migration `0013_seed_model_pricing.py:79-90`; the configured `llm_model_fast` default `claude-haiku-4-5-20251001` (`config.py:159`) is present at `0013:80` |
| 5 | Membership of `decision_engine_org_ids`, **if the governance gate is meant to run at all** | **no** — static config + restart (`config.py:113`, `bootstrap.py:384`) |
| 6 | `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` on any non-memory backend, else fail-closed at boot (`config.py:45-50`) | **no** — process config |

**Not** preconditions, verified: a `tenants` row (nothing on the execute path reads `tenants`), a kill-switch row (the snapshot is empty-means-active — `app/governance/authority.py:222-226`), or per-org credentials for the single-shot path.

What happens when precondition 3 fails: `SpendCeilingEnforcer` raises `OrgSpendCeilingExceeded` (`spend_ceiling.py:288`). That exception is caught **nowhere in `src/`** — grep finds it only in `spend_ceiling.py` and tests. `edge/routes/agents.py:80-108` catches seven exception types; this is not one of them. The customer's first call returns **HTTP 500**, not a governed refusal. The same is true of `LLMModelNotPriced` (`anthropic_adapter.py:328`).

### 6. VERDICT

> **A new customer CANNOT self-serve to a first execution**, because the org spend-ceiling gate fails closed on a missing row (`adapters/llm/spend_ceiling.py:203-218`) and there is deliberately no endpoint to set one (`edge/routes/spend.py:9-10`); and because with `dev_auth=false` the API-key, tenant-registration, credential and kill-switch surfaces do not accept the JWT that registration produces (`edge/routes/api_keys.py:56` + `edge/deps.py:135-143`), while with `dev_auth=true` (`config.py:56`, the default) there is no authentication at all.

Per-customer manual steps, in order:

1. **Operator sets the org's spend ceiling** — `OrgSpendCeilingDAL.set_ceiling(org_id=..., billing_period=..., ceiling_micros=..., audit=..., correlation_id=...)` (`dal/org_spend_ceiling.py:93-156`) from a Python shell, or direct SQL against `org_spend_ceiling`. One-time per org (effective-dated, `dal/org_spend_ceiling.py:79-91`).
2. **Operator adds the org to `SKYLIZE_DECISION_ENGINE_ORG_IDS` and restarts the process** — otherwise the governance gate never fires for that customer (`app/agents/execution.py:218`). This is the product's differentiator, and it is off by default.
3. **Operator mints the customer's first API key** out-of-band, or leaves `dev_auth=true` — because `POST /api/v1/api-keys` cannot be reached with a registration JWT (`api_keys.py:56`).
4. **Operator registers the `tenants` row** (or the customer does, only if step 3 gave them an API key) — `POST /api/v1/tenants` reads `org_id` from the authenticated context (`tenants.py:48`), so it has the same credential dependency.
5. **Operator provisions console access** — the console has one shared password (`website/src/app/api/console/session/route.ts:4-6`) and one service API key (`website/src/lib/skylize/client.ts:79`, `config.ts:16-17`), so a second customer requires a second console deployment.

---

## PART 2 — HAS THIS EVER RUN ANYWHERE ELSE

### 7. CI

Two workflows, both `runs-on: ubuntu-latest`.

**`.github/workflows/ci.yml`** — `on: push: branches: [main]` and an unfiltered `pull_request:` (`ci.yml:3-6`). Three parallel jobs, no `needs:`:
- `unit` (`:13-35`) — ruff, `lint-imports`, `check_forbidden_imports.py`, `check_all_modules_importable.py`, `find_orphan_modules.py`, `mypy src`, `pytest -q`.
- `website` (`:41-58`) — `npm ci`, typecheck, vitest.
- `integration` (`:66-106`) — services `postgres:16-alpine` (`:69-78`) and `redis:7-alpine` (`:79-84`); `alembic upgrade head` (`:101`); `pytest -q -m integration -rA` (`:106`).

**`.github/workflows/deploy-staging.yml`** — `on: push: branches: [main]` + `workflow_dispatch` (`:3-6`). Chain `lint-and-test` → `integration-test` → `build-and-push` (`docker build ... .`, i.e. the **root** Dockerfile, `:130`) → `deploy` (ECS task-definition render + deploy + `/health` smoke test, `:136-193`).

**Has CI run on this work?** The branch **is** pushed: `git rev-list --count feat/durable-governance --not --remotes` = 0, `git branch -vv` shows it tracking `origin/feat/durable-governance` with no ahead marker. It is **50 commits ahead of `origin/main`** (`git rev-list --left-right --count origin/main...feat/durable-governance` = `0  50`). Both workflow files are present on the branch.

- `deploy-staging.yml`: **never ran for this work.** Its only automatic trigger is `push` to `main`, and none of the 50 commits has reached `main`. (`workflow_dispatch` leaves no working-tree trace — UNVERIFIED, but see §10/§13.)
- `ci.yml`: **UNVERIFIED.** Its `pull_request:` trigger is unfiltered, so a PR from this branch would fire it, and the branch is pushed so a PR is possible. Nothing in the working tree records workflow runs — no run ids, no badges, no artifacts. Determining it requires network access (`gh run list --branch feat/durable-governance`).

**Disagreement with `docs/REPO_STATE.md` (R4).** REPO_STATE:46 states the branch is "46 commits ahead of that upstream — all 46 unpushed" and REPO_STATE:21,47 report "62 commits exist only on this machine". At `22aeffed` the code/git disagrees: **0 unpushed on this branch**, and `git log --all --not --remotes` = **2**. The branch was pushed after REPO_STATE was written. REPO_STATE's own note that the figure drifts is correct; the magnitude is not.

### 8. Are the money-path / RLS tests reachable in CI?

**In `ci.yml`: yes, by configuration.**
- Postgres provisioned (`ci.yml:69-78`), Redis provisioned (`:79-84`).
- `SKYLIZE_TEST_DB_URL` (`:87`), `SKYLIZE_TEST_APP_DB_URL` pointing at the **`skylize_app` role** (`:90`), `SKYLIZE_APP_DB_PASSWORD` (`:91`, `:104`), `SKYLIZE_TEST_REDIS_URL` (`:92`).
- `skylize_app` is created by the migration itself — `migrations/versions/0003_app_role_rls_subject.py:54-66`, `NOSUPERUSER NOBYPASSRLS` — and `ci.yml:100-104` runs `alembic upgrade head` with the matching password, so the role exists with the right privileges.
- The RLS / append-only / ceiling tests carry `pytest.mark.integration` and run under `pytest -q -m integration` (`ci.yml:106`): `tests/integration/test_postgres_isolation.py:28`, `test_cost_ledger_pg.py:27`, `test_org_spend_ceiling_pg.py:49`, `test_jsonb_readback_pg.py:40`, `test_spend_position_endpoint.py:44`.

**In `deploy-staging.yml`: no.** `SKYLIZE_TEST_APP_DB_URL` (`:80`) is the same **superuser** DSN as `SKYLIZE_TEST_DB_URL` (`:79`). The gate `requires_app_role` only checks non-emptiness (`tests/integration/conftest.py:30-33`), so the tests would run and pass while connected as a role that bypasses RLS unconditionally — the exact failure mode migration 0003 exists to prevent (`0003_app_role_rls_subject.py:7-12`). Its migration step also omits `SKYLIZE_APP_DB_PASSWORD` (`:95-98`).

**OPA: ABSENT from both.** `SKYLIZE_TEST_OPA_URL` is set in no CI config (repo-wide grep: zero hits outside tests and docs) and no OPA service is provisioned. `tests/decision_engine/test_opa_client_integration.py` has never executed in CI. Corroborated in-tree at `docs/testing/test_suite_health_2026-07-19.md:123`.

**Statement of record:** the money-path and tenancy guarantees are proven by a CI job that is *correctly configured* to prove them, but there is no evidence in the working tree that that job has ever executed against this branch's 50 commits. Until a run is confirmed, those guarantees rest on the one Windows machine described in `docs/REPO_STATE.md:31-34`.

### 9. Deployment artifacts

| Artifact | Covers | Notes |
|---|---|---|
| `Dockerfile` (root) | **the gateway** | Multi-stage. `CMD ["sh","-c","alembic upgrade head && uvicorn skylize.edge.gateway:app --host 0.0.0.0 --port 8000 --workers 1"]` (`:57`). **Builder stage copies only `pyproject.toml` before `pip install --prefix=/install .`** (`:14-16`) while `pyproject.toml:103-104` sets `[tool.setuptools.packages.find] where = ["src"]`. Built by `deploy-staging.yml:130` and `deploy.ps1:134`. Also: `PATH="/install/bin:$PATH"` (`:25`) points at a directory that does not exist in the runtime stage — the packages land in `/usr/local` (`:33`) |
| `infra/Dockerfile` | **the gateway** | Single stage. Copies `pyproject.toml` **and** `src` before install, with the explicit comment "setuptools needs src/ to build the wheel" (`infra/Dockerfile:10-12`). `CMD` same shape (`:26`). **This is the image `docker compose` builds** — and it is *not* the image CI builds |
| `infra/opa/Dockerfile` | dependency only (OPA) | `FROM openpolicyagent/opa:1.18.2`, copies `policy/` (`:12-14`) |
| `infra/docker-compose.yml` | gateway **and** dependencies | `postgres` (`:6-20`), `redis` (`:22-32`), `opa` (`:42-52`), `migrate` (`:56-66`), `gateway` (`:68-88`, builds `infra/Dockerfile`), `decision-engine-worker` behind `profiles: ["opa-engine"]` (`:113-145`). No qdrant, n8n or temporal service |
| `infra/terraform/staging/` | gateway (ECS) + RDS, ElastiCache, ECR, ALB, IAM, Secrets Manager, VPC | Task definition `infra/terraform/staging/modules/ecs/main.tf:60-115`, container `api`, port 8000, no `command` override so the image CMD runs |
| `deploy.ps1` | gateway | terraform init/plan/apply (`:99-113`), ECR login + `docker build -f Dockerfile .` + push (`:123-139`), migrations via `aws ecs run-task` with `command: ["alembic","upgrade","head"]` (`:186-193`), service force-new-deployment (`:214-218`), `/health` smoke test (`:227-242`) |
| `infra/opa/railway.json` | dependency only (OPA) | |
| `website/railway.json` | the Next.js console, not the gateway | |
| Fly / Render / Procfile / nixpacks / k8s / Helm | **ABSENT** | |

### 10. Gateway container image — **exists (two, divergent); never built or run**

Two definitions exist and they are not the same image. CI and `deploy.ps1` build the **root** `Dockerfile`; compose builds **`infra/Dockerfile`**. The root one omits `src/` from the builder stage that the sibling explicitly documents as required.

Evidence it has never been built or run:
- No `.tfstate`, no `.terraform/`, no image digest, tag file or registry reference anywhere in the tree.
- The only CI step that builds it (`deploy-staging.yml:130`) sits behind `needs: integration-test` in a workflow that only triggers on `push` to `main` — which these 50 commits have never reached.
- Recorded local runs start dependencies only: `SESSION_A_REPORT.md:20` — `docker compose -f infra/docker-compose.yml up -d postgres redis`. The `gateway` service is not named.
- `docs/08_operations/opa_staging_bring_up.md:3` — "**Status: DOCUMENTATION OF MANUAL STEPS. Nothing here has been executed.**"; `:137` — "*ECS path:* **does not exist yet.**"
- `OVERNIGHT_SESSION_2026-07-21.md:4` — "No deploy, no environment created, no secret operation, no flag flipped."

**Decisive: the ECS task definition as written cannot boot.** `infra/terraform/staging/modules/ecs/main.tf:73-100` supplies `SKYLIZE_BACKEND=postgres`, `SKYLIZE_DEV_AUTH=false`, `PYTHONPATH`, and five secrets (`SKYLIZE_DB_URL`, `SKYLIZE_DB_APP_URL`, `SKYLIZE_REDIS_URL`, `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET`, `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM`). It supplies neither:
- `SKYLIZE_JWT_SECRET` — with `dev_auth=false` this raises `ValueError` inside `Settings()` construction (`config.py:197-205`), and `get_settings()` runs at module import (`edge/gateway.py:90` → `create_app()` → `:56`), so `uvicorn skylize.edge.gateway:app` crashes before serving; nor
- `SKYLIZE_ANTHROPIC_API_KEY` — absent with `llm_demo_mode` false raises `LLMConfigurationError` in the lifespan (`bootstrap.py:346-352`).

Either alone means the ALB health check on `/health` (`ecs/main.tf:102-108`) could never have passed.

### 11. Migrations on deploy — **covered, three ways**

1. **Baked into the image CMD** — `Dockerfile:57` and `infra/Dockerfile:26` both run `alembic upgrade head &&` before uvicorn. On ECS this is what executes, because the task definition has no `command` override (`ecs/main.tf:60-115`): every task on every rolling deploy runs migrations before serving. No init container, no separate migration container.
2. **Compose one-shot** — `infra/docker-compose.yml:56-66` service `migrate`; `gateway` waits on `service_completed_successfully` (`:86`).
3. **Scripted one-off ECS task** — `deploy.ps1:186-193`, `aws ecs run-task` with a `command` override, skippable via `-SkipMigrations` (`:20`).

The `deploy` job of `deploy-staging.yml` (`:136-193`) runs no migration step of its own.

**The `fileConfig` fix is present.** `migrations/env.py:21-27` calls `fileConfig(config.config_file_name, disable_existing_loggers=False)` under `if config.config_file_name is not None:`. `alembic.ini:8-40` defines logger sections, so the branch is taken on every invocation — CLI and programmatic alike — but the explicit `disable_existing_loggers=False` means application loggers are not silenced. On the deploy path the risk is doubly moot: alembic runs as a separate process that exits before uvicorn starts. The programmatic callers that would have been affected are `tests/integration/conftest.py:44-46,60,67-71,93-97`; `src/` contains **no** programmatic alembic invocation (only the comment at `bootstrap.py:177`), and `scripts/` contains none.

### 12. Secrets — **AWS Secrets Manager shells exist; population is manual; `.env` otherwise**

- **AWS Secrets Manager** is the only real integration. `infra/terraform/staging/modules/secrets/main.tf:4-45` creates seven empty secret shells (`DATABASE_URL`, `DATABASE_APP_URL`, `REDIS_URL`, `HMAC_SECRET`, `LANGFUSE_SECRET_KEY`, `GOVERNANCE_SIGNING_KEY_PEM`, `DB_PASSWORD`); `DB_PASSWORD` is seeded with a literal placeholder and `ignore_changes` (`:47-53`). ARNs feed the ECS task `secrets` block (`ecs/main.tf:79-100`). A `secretsmanager` VPC endpoint exists (`vpc/main.tf:107-114`). **Population is a printed reminder in `deploy.ps1:143-165`, not an automated step.**
- **GitHub Actions secrets:** exactly one — `AWS_GITHUB_ACTIONS_ROLE_ARN` for OIDC (`deploy-staging.yml:117,148`). No application secret is injected in CI.
- **Vault / Doppler / SOPS / sealed-secrets / SSM Parameter Store / Railway or Render env sync: ABSENT.** Vault appears only as aspiration in docs (`docs/02_architecture/tech_stack.md:57`, `docs/architecture/05_security_architecture.md:121-122`). The in-app `CredentialVault` (`app/credentials/vault.py`) is a per-org Fernet store for customer provider credentials, not a secret manager.
- Config loading: pydantic-settings with `env_prefix="SKYLIZE_"`, `env_file=".env"` (relative to CWD, `/app` in both images), process env winning (`config.py:18-26`), cached in a module singleton (`config.py:208-215`).
- **`.env.example` is materially incomplete for a deployment.** It omits `SKYLIZE_DB_URL`, `SKYLIZE_DB_APP_URL`, `SKYLIZE_REDIS_URL`, `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM`, `SKYLIZE_APP_DB_PASSWORD`, `SKYLIZE_CORS_ORIGINS`, `SKYLIZE_DECISION_ENGINE_ORG_IDS`, `SKYLIZE_OIDC_*`, `SKYLIZE_LANGFUSE_*` — all declared fields in `config.py` and several load-bearing.

### 13. VERDICT

> **This application HAS NEVER run outside a local developer machine**, on the evidence of: no terraform state or image artefact anywhere in the tree; the only image-building CI job gated behind a `main` push these 50 commits have never made (`deploy-staging.yml:5,107,130`); the ECS task definition omitting two variables each of which fails the process closed at boot (`ecs/main.tf:73-100` vs `config.py:197-205` and `bootstrap.py:346-352`); and in-tree operational documents stating it directly (`docs/08_operations/opa_staging_bring_up.md:3,137`, `OVERNIGHT_SESSION_2026-07-21.md:4`).

---

## PART 3 — WHAT BREAKS WITH A SECOND CUSTOMER

### 14. Tenancy: RLS per table

25 tables. **18 have `ENABLE` + `FORCE` ROW LEVEL SECURITY with a `tenant_isolation` policy. 7 have none. No table has ENABLE without FORCE.** The GUC is `skylize.org_id`, not `app.current_org` (`0001_initial_schema.py:362`).

Eleven of the 18 are covered by one `DO` loop in `0001_initial_schema.py:347-367` (array `:351-355`, `ENABLE` `:357`, `FORCE` `:358`, policy `:359-364`): `governance_tokens`, `agent_live_state`, `kill_switch_state`, `budget_ledger`, `decisions`, `hitl_queue`, `memory_records`, `kg_nodes`, `kg_edges`, `audit_log`, `tenant_integrations`. Their policy is replaced by `0002_rehydrate_rls_carveout.py:51-60`, which adds a read-only escape hatch: the `USING` clause also passes when `current_setting('skylize.rehydrate') = 'on'` (`0002:57`), a GUC any app-role session can set (`dal/connection.py:101`). `WITH CHECK` is unchanged (`0002:59`).

The other seven with full RLS: `deliverables` (`0006_deliverables.py:74-81`), `org_credentials` (`0007_org_credentials.py:59-68`), `decision_outbox` (`0009_add_outbox_table.py:84-96`), `workflow_run_steps` (`0010_workflow_run_steps.py:88-95`), `decision_processed_events` (`0011_decision_engine_stores.py:63-70`), `ai_cost_ledger` (`0012_ai_cost_ledger.py:178-185`), `org_spend_ceiling` (`0014_org_spend_ceiling.py:99-106`).

**Customer data WITHOUT tenant isolation — 6 tables:**

| Table | Created | Holds | Why unisolated |
|---|---|---|---|
| `users` | `0008_users.py:33` | email, bcrypt `password_hash` (`:37`), display name, roles | no RLS; comment at `0008:65` — "auth layer needs cross-tenant email lookup". Has `org_id` (`:35`) |
| `user_refresh_tokens` | `0008_users.py:52` | refresh-token revocation records | **no `org_id` column at all** (`:53-58`) |
| `api_keys` | `0004_api_keys.py:38` | `key_hash`, name, scopes, `created_by`, `last_used_at` | RLS deliberately omitted (`0004:61-63`). Has `org_id` (`:40`) |
| `tenants` | `0001:40` | org display name, OIDC issuer, status | not in the RLS array (`0001:351-355`) |
| `tenant_users` | `0001:52` | user↔org membership + role (`:55-56`) | not in the RLS array |
| `model_pricing` | `0012_ai_cost_ledger.py:65` | nullable `org_id` (`:67`) exists to hold per-tenant negotiated prices | no RLS (`0012:176`); app role has full DML (`0012:216`) |

(`agent_contracts`, `0001:66`, also has no RLS but holds no customer data — platform registry, no org column.)

Isolation on all six is application-level `WHERE org_id = $1` only. Several accessors carry **no org filter at all**: `dal/users.py:37-40` (`get_by_id`), `:51-54` (`update_last_login`), `:69-72` (`get_refresh_token`), `:83-87` (`revoke_refresh_token`), `dal/repositories.py:409-412` (`touch_last_used`).

**A structural caveat that outranks the table list.** There is one connection pool (`dal/connection.py:55-57`) built from `settings.runtime_db_url`, which is `db_app_url or db_url` (`config.py:41-43`) with `db_app_url` defaulting to `""` (`config.py:37`) and `db_url` defaulting to the superuser `skylize` (`config.py:36`). A superuser bypasses RLS unconditionally regardless of FORCE (`0001:12-16`, `0003:7-12`). Compose sets the app DSN correctly (`infra/docker-compose.yml:77`); `.env.example` documents no DB variable at all. **Any run that forgets `SKYLIZE_DB_APP_URL` silently disables every RLS policy in the schema.**

Scope is set **per transaction**, not per connection: `set_config('skylize.org_id', $1, true)` inside `async with conn.transaction()` (`dal/connection.py:78-79`), so a pooled connection carries no stale binding and no `RESET` is needed. `admin_session` (`connection.py:83-87`) acquires from the same pool and sets nothing — every RLS comparison there is NULL, so rows are hidden rather than exposed. Live-path repositories using `admin_session`: `PgUserRepository` all methods (`dal/users.py:17,30,37,44,51,59,69,83`), `PgTenantRepository` all methods (`dal/repositories.py:277,289,304,311,322,336,351`), `PgApiKeyRepository` all methods (`dal/repositories.py:375,389,394,401,409` — `get_by_prefix` at `:389` is on **every** API-key request via `edge/deps.py:87`).

### 15. The gate switch: `decision_engine_org_ids`

`decision_engine_org_ids: list[str] = []` (`config.py:113`) is read once at startup — `decision_engine.subscribe(org_id)` per entry (`bootstrap.py:299-300`) and frozen into the execution service as `frozenset(...)` (`bootstrap.py:384`). `AgentExecutionService` consults that frozenset per request (`app/agents/execution.py:218`).

**Onboarding a governed customer requires editing an environment variable and restarting the process.** On ECS that is a task-definition revision plus a rolling deploy. Operational consequences: governance is off by default for every new customer (`app/agents/execution.py:177` — "When `governed_org_ids` is empty (the default) ... the gate never runs"); the on/off boundary for the product's core feature is a redeploy; and there is no per-tenant record of whether governance is on — the switch lives only in process config, not in the database, so it is invisible to the audit trail and to `GET /api/v1/tenants/me`.

### 16. Spend ceilings at N customers

- The ceiling is `(org_id, billing_period)` (`0014_org_spend_ceiling.py:76-77`) but the **read is effective-dated** — greatest `billing_period <= requested` (`dal/org_spend_ceiling.py:79-91`). A ceiling set once stays in force through every later month. **The recurring burden is therefore zero; the burden is one-time, per org, at onboarding.** Spend accounting still resets per calendar month (`CostLedgerDAL.org_period_total_micros`, called at `spend_ceiling.py:195`).
- **Ways to set a ceiling: exactly two — the audited DAL (`dal/org_spend_ceiling.py:93-156`) or direct SQL.** No route writes it, by explicit design (`edge/routes/spend.py:9-10`). The DAL has no CLI wrapper; `scripts/` contains nothing for it. The only non-test callers anywhere are **none**.
- The gate is a **soft** cap by its own docstring: the ceiling read is not atomic with the ledger write, so overshoot is bounded at roughly `concurrency - 1` maximal in-flight calls (`adapters/llm/spend_ceiling.py:9-19`). With no per-org concurrency cap (see §18), "concurrency" is unbounded.
- Refusal surfaces as HTTP 500, not a typed error (see §5).

### 17. Shared, non-tenant-scoped state on the request path

| Site | Holds | Assessment |
|---|---|---|
| `app/governance/authority.py:91` — `ConvergenceTracker._rings` | action hashes keyed `(correlation_id, agent_id)` | **no `org_id` in the key.** Cross-tenant interference requires a shared `correlation_id`; it is server-minted (`schemas/base.py:85`, `default_factory=uuid4`) and no route accepts it from a header or body. **Latent, not currently exploitable** |
| `tools/proxy.py:63` — `ToolCallCounter._counts` | per-`(correlation_id, agent_id, tool_id)` call counts enforcing `max_calls_per_run`; grows for process life | no `org_id` (conceded at `tools/proxy.py:53-59`). Same precondition; impact would be availability, not data |
| `runtime/run_ledger.py:163` — `_redis_key` = `runledger:{correlation_id}:{agent_id}` | per-run token budget in **shared Redis across all tenants** | no `org_id` in the Redis key. In-memory twin at `run_ledger.py:46,91` likewise |
| `memory/qdrant_adapter.py:35` — `_COLLECTION = "platform_knowledge"` | **one Qdrant collection for all tenants**; every read/write targets it (`:83,99,112,122,138,157,169`) | isolation is application-level only — `memory/knowledge_ingestion.py:217-219` forces `filters={"org_id": org_id}`. There is no store-level enforcement equivalent to RLS; one missing filter at a future call site is a cross-tenant read |
| `dal/connection.py:56` — `max_size=10` | global pool cap | not per-org: one tenant can starve all others |
| `bootstrap.py:238-240` | one platform-wide Fernet key for `org_credentials` | not per-tenant |

Verified **not** leaks (org-keyed or immutable): `edge/rate_limit.py:16` (keyed by `org_id`), `app/governance/snapshot.py:20-21` (keyed `(agent_id, org_id)` / org set), `app/decision_engine/evaluator.py:95` (keyed `(org_id, partition_key)`), `runtime/exec_fingerprint.py:92,104-106` (org inside the key and inside the hash), `contracts/registry.py:132` `MVP_REGISTRY` (no `register_contract` caller in `src/`), `tools/registry.py:19`, `config.py:208-215`. Only two `@lru_cache` sites exist in `src/` — `memory/compression/budget.py:27` (tiktoken encoder) and `services/obsidian_writer/settings.py:21` (config) — neither caches tenant data.

### 18. Rate limiting and abuse — **PARTIAL, and absent where it matters**

`RateLimiter` (`edge/rate_limit.py:13`) is **per-org** (`allow(org_id)`, `:18,21`), a fixed 60-second window (`:20`), and **in-process memory** (`:16`) — its own docstring concedes "MVP-grade and in-process; at Scale this moves behind Redis" (`:4-5`). With N uvicorn workers or N replicas the effective limit is N × the configured value. The `_window` dict has no eviction, so with `dev_auth=true` a caller sending distinct `X-Dev-Org` values grows it without bound.

Two instances are built at lifespan: the general limiter, default 120/min (`edge/gateway.py:43`, `config.py:83`), and a tighter one for credential resolution, default 10/min (`gateway.py:44-46`, `config.py:85`).

**Enforcement covers 5 handlers:** `knowledge.py:178,214,236`, `workflows.py:47`, and inline at `credentials.py:98`.

**`POST /api/v1/agents/execute` has no rate limit** (`edge/routes/agents.py:57-61` — dependencies are `require_any_role_or_user` and `get_container` only). Nor do `/auth` register/login/refresh, `/api/v1/tenants`, `/api/v1/api-keys`, `/api/v1/deliverables`, `/api/v1/hitl`, `/api/v1/audit`, `/api/v1/spend`, `/api/v1/kill-switch`, `/api/v1/agent-prompts`. The `/auth` module docstring says so directly (`edge/routes/auth.py:9-10`).

**Per-org quota or concurrency cap beyond the spend ceiling: ABSENT.** No semaphore, no in-flight cap, no per-org connection cap in `src/`. `runtime/run_ledger.py` enforces tokens per `(correlation_id, agent_id)` — per run, not per org.

### 19. Observability — what an operator could actually see

**Present:**
- `audit_log` — append-only, DB-trigger enforced, one row per governed action (`app/audit/service.py:5-8`). Readable via `GET /api/v1/audit` (owner/admin, `edge/routes/audit.py:48`). Records `action_type`, `result`, `source_agent_id`, `authority_level`, `governance_token_id`, `correlation_id`, `causation_id`, `result_reason`.
- `deliverables` — `content_markdown` plus `metadata_json` carrying the raw `input`, `user_id` and `llm_provider` (`app/agents/execution.py:349-351`). This is the only place the customer's actual request text is retrievable.
- `ai_cost_ledger` — per-call cost in micro-USD, append-only (`dal/cost_ledger.py`), surfaced org-wide by `GET /api/v1/spend/position` (`edge/routes/spend.py:56-95`).
- `hitl_queue`, `decisions`, `governance_tokens`, `kill_switch_state`.
- Structured logs — `log.info("agent_llm_response", extra={agent_id, provider, tokens})` (`execution.py:236-239,312-315`).

**Absent:**
- **OpenTelemetry: ABSENT.** Zero `opentelemetry` imports anywhere in `src/`. `AnthropicAdapter` takes a `tracer` parameter (`anthropic_adapter.py:196`) and starts spans when it is set (`:577-581`), but `bootstrap.py:339-343` constructs the adapter without one. `pyproject.toml:13` lists OTel as a dependency to be "added in the sprints that first import them" — that has not happened.
- **Langfuse: unreachable.** `_record_langfuse` exists (`anthropic_adapter.py:535-560`) and returns immediately when `self._langfuse is None` (`:539`). `bootstrap.py:339-343` passes no client, `Langfuse(` is constructed nowhere in `src/`, and `langfuse` is **not a dependency** in `pyproject.toml`. The `langfuse_public_key` / `langfuse_secret_key` settings (`config.py:147-148`) reach nothing in the API process.
- **Inputs and outputs in the audit trail are SHA-256 hashes only** — deliberate, for PII (`app/audit/service.py:10-11,28-33`).

**A support question that could NOT be answered today:** "why did this specific call cost what it did / return what it did?" The raw prompt sent to the provider, the raw response, the per-call token split and the model actually used are not persisted anywhere queryable — only the parsed markdown, an aggregate cost row, and hashes. There is no trace id linking a customer-reported request to its LLM call other than `correlation_id`, which is not returned to the caller on the 201 path (`edge/routes/agents.py:111-116` returns `deliverable_id`, `status`, `agent_id`, `title`). There is also no way to answer "is this customer's governance on?" from data — see §15.

---

## PART 4 — THE TWO BARS

### 20. THE DEMO BAR

**Demonstrable end to end today, no narration needed** (memory backend, demo mode, no keys, no infrastructure):

- The full governed lifecycle: onboard → agent produces → decision-bearing event → decision engine → HITL defer → human verdict resumes → audit trail. This is an executing test, not a claim: `tests/integration/test_demo_lifecycle.py:29-45`.
- The HTTP happy path: register → execute → read deliverable back → list deliverables, via `scripts/e2e_deliverable.py:1-31`.
- Synchronous governance on `/agents/execute`: 201 approve / 202 defer with a `hitl_id` / 403 reject (`edge/routes/agents.py:84-100`), each with a terminal decision event and an audit record emitted before the response (`app/agents/execution.py:416,488-591`).
- The kill switch, engaged and observed to deny (`edge/routes/kill_switch.py:29,43` → `app/governance/authority.py:222-226`).
- Signed P-384 governance tokens with a real pre-egress validation pipeline (`app/agents/execution.py:267-295`).
- The console: login, agent list, execute, deliverable read, HITL approve/reject, kill switch (`website/src/app/api/console/*`).

**Requires narration or visibly fails — 8 gaps:**

1. **Every demo string is literally prefixed `[DEMO]`** (`adapters/llm/demo_adapter.py:42-95`). Output content is templated, not generated. This is by design and correct, but it is on screen.
2. **Only 7 of 21 agents have canned demo responses** (`demo_adapter.py`: hook_generator, ad_copy, caption_writer, script_writer, cta_optimizer, seo_keyword, cfo). The other **14 now raise `DemoResponseUnavailable`** naming themselves, and the caller sees **HTTP 500** — the exception is typed but unmapped in `edge/routes/agents.py:96-203`, so it reaches FastAPI's default handler (measured, not inferred). Demoing an agent outside the seven fails visibly, and always did; what changed is *how*.

   **Before** (`_pick_response` sniffed keywords out of the system + user prompt): 13 of the 14 fell to `_FALLBACK_RESPONSE = {"result": ...}`, which satisfies no agent's output schema — e.g. `ToneAdjustedOut` requires `brief_id`, `adjusted_content`, `notes` (`schemas/agents/brand.py:32-35`) — so validation failed at `execution.py` and the API returned **HTTP 502** "the model provider is unavailable or returned an unusable response" (`agents.py:193-201`). That message was false: in demo mode there is no provider. The 14th, **`director_growth`, was served `cfo_agent`'s budget summary on every call** — its own `agent_role` ("Director Growth — proposes campaigns & budget reallocations") matched `if "budget" in combined`. That is not a failure at all; it is another agent's output returned under this agent's name, schema-valid and therefore invisible to every check downstream. The sniff also read **customer content**: `tone_of_voice_agent` asked to soften *"Cut your ad budget in half"* received `cfo_agent`'s payload; the same agent asked about a *"hook"* received `hook_generator_agent`'s. Routing was a function of the caller's text, not of who was acting.

   **Now**: dispatch is an exact lookup on `agent_id`, a required field on every request model reaching the adapter (`gateway.py:165,202`; `structured.py:122`) that every live construction site sources from the resolved contract or the validated `GovernanceToken`. Mis-routed agents: **1 before, 0 after**. A 500 with a typed, agent-naming message in the log is more honest than a 502 blaming a provider that was never called — but it is still an unhandled exception with a generic body, which is why **E21** maps it.
3. **Demo mode and the money path are mutually exclusive.** `DemoLLMAdapter` reports `cost_usd_micros=0` (`demo_adapter.py:170`) and the spend-ceiling enforcer + cost ledger are wired only in the Anthropic branch (`bootstrap.py:318-343`). You can demonstrate governance **or** billing, not both, unless you run against a live key.
4. **The OPA engine — the designated production arbiter — decides nothing.** All 7 Rego files are `default allow := false` with no allow rule, 128 lines total (`policy/skylize/decision/*.rego`). `infra/docker-compose.yml:36,108-109` accurately says "denies everything". The live arbiter is the inline evaluator (`bootstrap.py:294`), which `bootstrap.py:276-282` hard-fails on any other selection.
5. **The one live LangGraph path is hardwired to a single agent and skips the gate.** `POST /api/v1/workflows/creative` always invokes `hook_generator_agent` (`edge/routes/workflows.py:58`) and runs no decision gate — it requires no role at all, only authentication (`workflows.py:47`).
6. **No traces.** See §19: no OTel, no Langfuse. "Show me the trace for that call" has no answer.
7. **The console is single-tenant.** One shared password (`website/src/app/api/console/session/route.ts:4-6`) and one service API key (`website/src/lib/skylize/client.ts:79`).
8. **The governance outcome distribution is static, not intelligent.** For the 21 agents on a valid input, the inline evaluator returns 9 approve / 12 defer / 0 reject, determined by the contract's `human_in_loop_triggers` (`app/decision_engine/evaluator.py:216-259`), not by the content of the request. `reject` is reachable only for a malformed proposal (`evaluator.py:310-326`).

**What a hostile question exposes:**

- *"Show me a second customer."* → the console cannot host one (gap 7); minting their API key needs an API key (§4); governing them needs a redeploy (§15).
- *"Is this running anywhere I could hit?"* → no (§13). The staging task definition cannot boot (§10).
- *"So OPA is your governance engine — show me a policy denying something for a real reason."* → gap 4.
- *"Suspend that tenant in front of me."* → `POST /api/v1/tenants/me/suspend` writes `status='suspended'` (`edge/routes/tenants.py:81-83` → `dal/repositories.py:306`) and **nothing on any enforcement path reads it**; `get_tenant` is read only inside `TenantService` and by `GET /tenants/me` (`app/tenants/service.py:43,68,75,85,94`; `edge/routes/tenants.py:69`). The only working stop control is the kill switch.
- *"What does the audit trail say this agent was asked?"* → a SHA-256 hash (§19).
- *"What stops one customer spending another's budget?"* → the ceiling is a soft cap (`spend_ceiling.py:9-19`) with no concurrency cap and no rate limit on the spending endpoint (§18).

### 21-22. THE PILOT BAR — one real customer, real money, not a laptop

Ordered by when a customer hits it. Sizes: SMALL <½ day, MEDIUM ~1 day, LARGE >1 day.

#### ENGINEERING — Day one (the customer's first hour)

| # | Item | Size | Reasoning |
|---|---|---|---|
| E1 | Map `OrgSpendCeilingExceeded` and `LLMModelNotPriced` to typed HTTP responses instead of 500 (`agents.py:80-108` catches neither; `spend_ceiling.py:288`, `anthropic_adapter.py:328`) | **SMALL** | two `except` clauses on an existing pattern; the exceptions already carry the full decision context |
| E2 | A way to set a spend ceiling without a Python shell — endpoint, CLI, or documented SQL runbook (`edge/routes/spend.py:9-10`; the DAL exists at `dal/org_spend_ceiling.py:93`) | **SMALL** (CLI/runbook) / **MEDIUM** (governed endpoint with RBAC + audit + tests) | the audited write seam is already built; only the caller is missing |
| E3 | Let a registration-issued JWT reach `/api/v1/api-keys`, or provide another first-key path (`api_keys.py:56` uses `require_any_role`, not `require_any_role_or_user`) | **SMALL** | one-line resolver swap; the combined resolver already exists (`deps.py:167-171`) and is used by six routers. Test surface is the cost |
| E4 | Boot interlock: refuse to start with `dev_auth=true` on a non-memory backend (no such validator exists — `config.py:186-205` has only two) | **SMALL** | mirrors the two existing `model_validator` fail-closed checks |
| E5 | Rate-limit `/agents/execute` and `/auth/*` (`agents.py:57-61`, `auth.py:9-10`) | **SMALL** | `enforce_rate_limit` already exists (`deps.py:126-132`); adding it is a dependency change. Making the limiter shared-state is E11 |
| E6 | Fix the ECS task definition: supply `SKYLIZE_JWT_SECRET` and `SKYLIZE_ANTHROPIC_API_KEY` (`ecs/main.tf:73-100` vs `config.py:197-205`, `bootstrap.py:346-352`) | **SMALL** | two secret entries plus two Secrets Manager shells (`secrets/main.tf:4-45`) |
| E7 | Reconcile the two gateway Dockerfiles; confirm the root image actually builds (`Dockerfile:14-16` omits `src/` before `pip install .`, contra `infra/Dockerfile:10-12`) | **UNVERIFIED** — likely SMALL | requires an actual `docker build` to determine whether setuptools errors or silently emits an empty wheel that `PYTHONPATH=/app/src` masks. State would take one build |

#### ENGINEERING — Week one

| # | Item | Size | Reasoning |
|---|---|---|---|
| E8 | Make the governed-org set data, not process config, so onboarding is not a redeploy (`config.py:113` → `bootstrap.py:299,384` → `execution.py:218`) | **MEDIUM** | new per-tenant column or table, a read on the request path (or a cached invalidatable set), plus migration and tests. The `GovernanceBroadcast` invalidation pattern (`app/governance/broadcast.py`) is a precedent to follow |
| E9 | Make tenant `status` enforce something — `suspended` must stop execution (`repositories.py:306` written, never read) | **SMALL** | one check on the execute path plus the kill-switch precedent; the row and the routes already exist |
| E10 | Expire `hitl_queue` rows past `expires_at` (no sweep exists; `dal/hitl.py:133-142` filters only `status='pending'`) | **MEDIUM** | needs a background task in the gateway lifespan (`edge/gateway.py:39-50` has none today) or an external scheduler, plus idempotency |
| E11 | Move the rate limiter to Redis so the limit is per-org, not per-worker (`edge/rate_limit.py:4-5,16`) | **MEDIUM** | the pattern already exists in `services/obsidian_writer/app.py:37-49`; the interface is unchanged |
| E12 | RLS or an equivalent guarantee for `users`, `user_refresh_tokens`, `api_keys`, `tenants`, `tenant_users`, `model_pricing` — or a written, tested justification per table (§14) | **LARGE** | `0008:65` states the auth layer needs cross-tenant email lookup, so this is a design decision (separate auth schema? a `SECURITY DEFINER` lookup function?) before it is a migration. Five unfiltered accessors (`dal/users.py:37,51,69,83`; `repositories.py:409`) change with it |
| E13 | Per-org concurrency cap so the soft spend ceiling has a bounded overshoot (`spend_ceiling.py:9-19`; pool `max_size=10` is global, `connection.py:56`) | **MEDIUM** | a semaphore keyed by org plus a decision about what to do when it is full; interacts with E11 |
| E14 | Observability a support engineer can use: at minimum a returned `correlation_id` on the 201 and a per-call record of model, tokens, and cost joinable to it (§19) | **MEDIUM** | the data exists in `ai_cost_ledger` and the logs; the join key is not surfaced (`agents.py:111-116`) |

#### ENGINEERING — Later

| # | Item | Size | Reasoning |
|---|---|---|---|
| E15 | Real OIDC, or accept that API keys are the only production credential — today `oidc_jwks_url` defaults empty (`config.py:57`) and the production path fetches JWKS **on every request with no cache** (`edge/auth.py:62-63`) | **LARGE** | IdP selection is a business decision; the JWKS cache alone is SMALL |
| E16 | A multi-tenant console: per-user login instead of one shared password, per-org identity instead of one service key (`session/route.ts:4-6`, `client.ts:79`) | **LARGE** | the console's entire auth model changes; the BFF currently has no concept of which org a session belongs to |
| E17 | Wire Langfuse or OTel — the hooks exist and reach nothing (`anthropic_adapter.py:196,535-560`; no `opentelemetry` import in `src/`; `langfuse` not a dependency) | **MEDIUM** | adding the dependency and a construction site is small; deciding what to trace, and the PII posture against `audit/service.py:10-11`, is not |
| E18 | Real Rego + a live OPA server, if OPA is to be the arbiter ADR-0004 designates (`policy/skylize/decision/*.rego`, 128 lines of deny-all) | **LARGE** | blocked on owner approval of `policy_inputs.md` (§26) |
| E19 | Per-tenant Qdrant isolation, or accept application-level filtering as the boundary (`memory/qdrant_adapter.py:35`, one collection for all tenants) | **MEDIUM** | per-org collections or a payload-index guarantee, plus a re-index of existing points |
| E21 | Map `DemoResponseUnavailable` to a typed HTTP response instead of an unhandled 500 (`demo_adapter.py`; `agents.py:96-203` catches it nowhere) | **SMALL** | one `except` clause on the existing pattern, mirroring `LLMModelNotPriced` -> 503 `MODEL_NOT_PRICED` (`agents.py:169-184`): both are "this deployment cannot serve this agent", decided before any spend. The exception already carries `agent_id`. Status choice is an owner call, not a mechanical one, which is why it is an item and not part of the dispatch fix |
| E22 | Decouple demo payload validity from `execution.py`'s `brief_id` echo before any move to `generate_structured` (`execution.py:329-333`) | **SMALL** (add the four `brief_id`s) / **MEDIUM** (decide the echo's contract first) | see §27 |
| E20 | A governed invite flow — the only way to add a SECOND user to an organisation. `POST /api/v1/auth/register` is unauthenticated and now creates NEW orgs only, refusing any org that already has a user (409 `org_not_available`, `app/auth/user_service.py`); the read-then-write that admitted strangers as `viewer` is gone, and with it the last HTTP path to a second account. Intentional and fail-closed: registration mints owners for unknown callers, so it must never be the path that adds a user to someone else's tenant | **MEDIUM** | the write side already exists (`UserRepository.create_user` is unconditional and is what an invite would call); the work is the surface around it — an owner/admin-authorised endpoint, an invitation record with a single-use expiring token, an explicit role choice validated against `VALID_ROLES`, an audit record per acceptance, and the migration for the invitation table. Migration 0017's unique index is deliberately partial on the owner role so this stays possible without a schema change |

#### OPERATIONS — not engineering (item 23)

These are frequently mistaken for missing features. They are provisioning steps and runbooks, and none requires new code.

| # | Item | Size |
|---|---|---|
| O1 | Populate the seven AWS Secrets Manager shells — they are created empty and `deploy.ps1:143-165` only prints a reminder (`secrets/main.tf:4-53`) | **SMALL** |
| O2 | Generate and install the P-384 governance signing key; `scripts/gen_governance_key.py` exists, nothing installs its output (`config.py:45-50` fails closed without it) | **SMALL** |
| O3 | Set `SKYLIZE_DB_APP_URL` to the `skylize_app` role in every non-compose environment — the default silently falls back to the superuser and disables all RLS (`config.py:37,41-43`) | **SMALL** |
| O4 | Per-customer onboarding runbook: ceiling row, governed-org list entry + restart, first API key, tenant row (§6) | **SMALL** |
| O5 | Complete `.env.example` — it omits every DB, Redis, signing-key, CORS and governed-org variable (§12) | **SMALL** |
| O6 | Merge this branch to `main`, or add `feat/*` to the CI push filter, so CI and the deploy pipeline can fire at all (`ci.yml:3-6`, 50 commits ahead of `origin/main`) | **SMALL** |
| O7 | Fix `deploy-staging.yml`'s `SKYLIZE_TEST_APP_DB_URL` to use `skylize_app`, not the superuser (`deploy-staging.yml:79-80`) — today its RLS assertions are vacuous | **SMALL** |
| O8 | Provision an OPA service in CI and set `SKYLIZE_TEST_OPA_URL`, or record that those tests are intentionally never run (§8) | **SMALL** |
| O9 | Decide and document the demo posture: demo mode (fake output, no money path) vs live key (real output, real spend). They cannot be shown together (§20 gap 3) | **SMALL** |

Counts: **22 engineering gaps** (E1-E22), **9 operations items** (O1-O9), **8 demo gaps**.

---

## PART 5 — HONEST ACCOUNTING

### 24. EXPOSURE LIST — claimed but not reachable (15)

| # | The claim | The contradicting code |
|---|---|---|
| 1 | ADR-0004 designates OPA the production governance arbiter (`docs/architecture/adr/0004-opa-production-arbiter.md`) | all 7 Rego files are `default allow := false` with no allow rule, 128 lines total (`policy/skylize/decision/*.rego` — `authority.rego:13`, `brand_legal.rego:13`, `data_access.rego:13`, `decision.rego:14`, `external_action.rego:13`, `security_veto.rego:14`, `spend.rego:11`); `bootstrap.py:276-282` raises on anything but `"inline"` |
| 2 | Langfuse observability — settings, adapter hook and a `_record_langfuse` implementation | `bootstrap.py:339-343` passes no client; `Langfuse(` is constructed nowhere in `src/`; `langfuse` is not in `pyproject.toml` dependencies. `anthropic_adapter.py:539` returns immediately |
| 3 | OpenTelemetry — `pyproject.toml:13` lists it as an observability dependency; `anthropic_adapter.py:196,577-581` has a tracer seam | zero `opentelemetry` imports in `src/`; `bootstrap.py:339-343` passes no tracer |
| 4 | Tenant suspension — `POST /api/v1/tenants/me/suspend` returns `status: "suspended"` (`edge/routes/tenants.py:75-87`) | `tenants.status` is written (`dal/repositories.py:306`) and read only by `TenantService` existence checks and `GET /tenants/me` (`app/tenants/service.py:43,68,75,85,94`; `edge/routes/tenants.py:69`). No enforcement path consults it |
| 5 | `edge/deps.py:28-32` — "The credentials router is NOT mounted yet ... populating `app.state.credential_resolve_limiter` is part of that deferred mount work" | mounted at `edge/gateway.py:79`; state populated at `edge/gateway.py:44-46` |
| 6 | `contracts/registry.py:131` — "the **15** governed creative + growth contracts" | `ALL_MVP_CONTRACTS` has **21** members (runtime count; `contracts/mvp/__init__.py:4` says 21 correctly) |
| 7 | `docs/06_integrations/anthropic.md:33-35` — the proxy enforces the token's `max_token_budget` before dispatch | true of `validate_tool_call` (`contracts/token.py:282-285`); the adapter-level `_check_budget` half is dormant — `anthropic_adapter.py:288-300` early-returns unless `request.max_token_budget` is set, and no live caller sets it (`execution.py:298,664`; `runner.py:111`; `structured.py:125`) |
| 8 | Temporal durable workflow execution — `temporalio` is a hard dependency (`pyproject.toml:66-68`) and a worker exists (`app/orchestrator/temporal/worker.py`) | no `temporal` reference in `bootstrap.py` or `edge/gateway.py`; nothing schedules it |
| 9 | Agent memory — `PgMemoryAdapter` reads and writes `agent_memory_entries` (`dal/memory_adapter.py:64,92`) | no migration creates that table (0001 creates `memory_records` instead, `0001:232`); `PgMemoryAdapter` is constructed nowhere; the INSERT casts `::vector` (`memory_adapter.py:99`) while pgvector is never installed (`infra/postgres/init/00_extensions.sql:3-5`) |
| 10 | `config.py:178` — LLM prices are "configurable so ops can update without redeploy" | demoted to a WARNING-logged fallback reached only with no cost ledger (`anthropic_adapter.py:504-505,703-707`); `model_pricing` is the source of truth (`anthropic_adapter.py:695-702`). The float defaults also disagree with the seeded table — haiku `0.80/4.0` (`config.py:181-182`) vs `1.00/5.00` (`0013_seed_model_pricing.py:80`) |
| 11 | Governance token `max_token_budget` protects per-org spend | it is per-run, keyed `(correlation_id, agent_id)` (`runtime/run_ledger.py:46,163`); the per-org control is the soft ceiling (`spend_ceiling.py:9-19`) |
| 12 | The gateway runs in a container | two divergent Dockerfiles; the one CI builds omits `src/` from the builder stage (`Dockerfile:14-16` vs `infra/Dockerfile:10-12`), and neither has been built or run (§10) |
| 13 | Staging exists on AWS ECS (`infra/terraform/staging/`, `deploy.ps1`, `deploy-staging.yml`) | no terraform state anywhere; the task definition omits two variables that each fail the process closed at boot (`ecs/main.tf:73-100` vs `config.py:197-205`, `bootstrap.py:346-352`); `docs/08_operations/opa_staging_bring_up.md:137` — "*ECS path:* **does not exist yet.**" |
| 14 | 21 agents are available for execution (`GET /api/v1/agents` lists all of them, `edge/routes/agents.py:119-136`) | **3** of 21 have a test proving execution through to a persisted deliverable, and no agent now reaches `create_deliverable` only against a mock (§25). In demo mode, 14 of 21 have no canned payload and raise `DemoResponseUnavailable`; 4 of the remaining 7 validate only because of the `brief_id` echo (§27) |
| 15 | RLS protects tenant data | true for 18 of 25 tables, and only when `SKYLIZE_DB_APP_URL` is set — the default DSN is the table-owning superuser, which bypasses RLS regardless of FORCE (`config.py:36-43`, `0003_app_role_rls_subject.py:7-12`). Six customer-data tables have no RLS at all (§14) |

### 25. Agents with a proven full path to a persisted deliverable — **3 of 21**

**Proven, persisted to Postgres — 3:**
- **`hook_generator_agent`** — `tests/integration/test_deliverable_readback_e2e.py:106-153`: execute → 202 defer → approve → `GET /api/v1/deliverables/{id}` → 200 with metadata intact. Also `tests/integration/test_agent_execute_governed_e2e.py` and `tests/integration/test_jsonb_readback_pg.py`.
- **`seo_keyword_agent`** — `tests/integration/test_seo_deliverable_e2e.py:215-313`: execute → **201 approve** → `GET /api/v1/deliverables/{id}` → 200 with `deliverable_type="seo_report"`, the model's keywords present in the markdown, and attribution metadata intact. A structurally different path from hook_generator's, which is why it is a second *vertical* and not a second instance of the same one: it **approves** rather than defers (`human_in_loop_triggers=[]` at `contracts/mvp/seo.py:36` → `evaluator.py:230-240`, owner decision D6), so there is no HITL hop; and it runs the **tool loop** rather than the single-shot path (`invocable_tools` non-empty at `seo.py:29` → `execution.py:233`), so the provider is called twice and **two** `ai_cost_ledger` rows are written, one per completed call. Its audit chain is linked by a shared `correlation_id`, not `causation_id` — that field is populated only on a HITL replay (`execution.py:377-379`), so on an approve path it is correctly NULL.

- **`cfo_agent`** — `tests/integration/test_cfo_deliverable_e2e.py`: execute → **202 deferred** → `POST /api/v1/hitl/{id}/approve` → tool loop → `GET /api/v1/deliverables/{id}` → 200. A third structurally distinct path, and the one that closes the mocked-only category. It **defers** where seo approves and it defers for a *different reason* than hook_generator: its triggers are `[SPEND_OVER_CEILING, LOW_CONFIDENCE_IRREVERSIBLE]` (`contracts/mvp/finance.py:183-186`), neither of which is `FIRST_EXTERNAL_LAUNCH`, so the evaluator takes its final branch — the unmatched-trigger fail-closed defer (`evaluator.py:247-260`) — and the queue row's `trigger_reason` records *both* triggers, `"spend_over_ceiling, low_confidence_irreversible"`. Because it executes on a HITL **replay**, its audit chain is linked by **`causation_id`** (`execution.py:377-379`), the mirror image of seo's correlation-only chain; the replay mints a fresh `run_id`, so the test asserts `causation_id == the original correlation` *and* `correlation_id != it`. It runs the tool loop (`invocable_tools=["utility.current_datetime"]`, `finance.py:176`), so **two** `ai_cost_ledger` rows are written, one per completed provider call (`idempotency_key = message.id`, `anthropic_adapter.py:699`). Uniquely, it is the only e2e covering the **post-validation recompute** (`execution.py:342-344`): the fake provider returns a schema-*valid* response whose arithmetic is deliberately wrong (`total: 7.77` plus a fabricated concentration flag) and the persisted deliverable is asserted to carry the Python-computed `$100,000.00` and the real `paid_media` flag instead — while the model's *narrative* fields survive untouched, so the test cannot pass by discarding the response. Proven to bind: with the recompute disabled the deliverable persists `$7.77` and the fabricated flag.

**Reach `create_deliverable` but against a mocked `DeliverableService` (nothing persists) — 0.** This category is now empty; `cfo_agent` was its last member (`tests/unit/test_finance_agent_execution.py:57-143`, `AsyncMock` at `:62`, which still runs as a unit test).

**The remaining 18 are invocable but unproven.** They are registered, listed by `GET /api/v1/agents`, and validated for contract *shape* by `tests/contract/test_agent_contracts.py` — registry membership, schema-path resolution, canonical authority levels, escalation paths ending at `human_owner`, and (new) an explicit `_AGENT_DELIVERABLE_TYPE` entry. No test executes them. Named: `ad_copy_agent`, `agency_deliverable_drafter`, `agency_requirements_analyst`, `art_director`, `brand_guardian_agent`, `caption_writer_agent`, `ceo`, `cmo`, `copy_director`, `creative_operations_manager`, `cta_optimizer_agent`, `director_growth`, `fraud_detection_agent`, `lead_qualifier_agent`, `script_writer_agent`, `sdr_outreach_agent`, `tone_of_voice_agent`, `vp_creative`. (Several appear in decision-evaluator and governance tests — `tests/unit/test_decision_evaluator.py`, `tests/unit/test_multiturn_execution.py` — which exercise the gate or the token, not the deliverable path.)

**Deliverable typing, for all 21.** `_AGENT_DELIVERABLE_TYPE` (`execution.py`) covered 10 of 21; the other 11 fell through `.get(agent_id, "other")` and were **typed by omission** — the type is part of the audit record, and it was being set by an absence. All 21 now have an explicit entry, each `"other"` carrying the reason it is `"other"`. Disposition (b) of the two considered: (a) — a distinct type per agent — cannot be honoured without inventing, because the vocabulary is fixed by migration `0006:42-47` (ten named types plus `other`) and for those 11 agents nothing in the contract determines which of the ten a run produces. Three contract-gate tests hold the line, including one that proves a *newly registered* agent without an entry is caught. **Agents typed by omission: 0.**

### 27. Demo payload validity is coupled to `execution.py`'s `brief_id` echo — E22

**REPORTED, NOT FIXED.** Four of the seven canned demo payloads do not satisfy their own output schema. They validate today only because `execution.py:329-333` copies input-provided fields the model is not expected to invent — in practice `brief_id` — from the validated input onto the raw output dict *before* `model_validate`:

```python
if isinstance(raw, dict):
    input_data_json = validated_input.model_dump(mode="json")
    for field in output_cls.model_fields:
        if field not in raw and field in input_data_json:
            raw[field] = input_data_json[field]
```

Measured against the real schemas — validating each `_DEMO_RESPONSES` entry directly, then again after applying the echo:

| agent | payload alone | after the echo | field the echo supplies |
|---|---|---|---|
| `hook_generator_agent` | valid | valid | — |
| `seo_keyword_agent` | valid | valid | — |
| `cfo_agent` | valid | valid | — |
| `ad_copy_agent` | **invalid** | valid | `brief_id` |
| `caption_writer_agent` | **invalid** | valid | `brief_id` |
| `script_writer_agent` | **invalid** | valid | `brief_id` |
| `cta_optimizer_agent` | **invalid** | valid | `brief_id` |

**7 of 7 valid today; 3 of 7 valid without the echo.**

Why that is a live risk rather than trivia: `adapters/llm/structured.py:generate_structured` is the intended provider-native structured-output path (its module docstring frames it as replacing "generate free text → JSON.loads → validate"). It calls `gateway.generate` and then `_parse(schema, response.text)` **directly** (`structured.py:348-353`) — there is no echo on that route, and none of the fallback/retry machinery adds one. So a migration of `execution.py` onto `generate_structured` silently drops demo mode from 7 demoable agents to 3, and the four losses surface as schema-validation failures attributed to the provider, not to the migration.

This is not an argument that the echo is wrong. It is deterministic pass-through of a correlation id the model has no business inventing, and it is the same principle as the `cfo_agent` recompute two lines below it. The defect is that the echo is an *undocumented precondition* of four payloads, recorded nowhere, in a file whose stated contract is "each payload satisfies its agent's output schema".

**Fix, when it is taken (E22):** either add the literal `brief_id` to the four payloads (SMALL — but `brief_id` is a UUID that must match the *request's*, so a fixed literal only works because nothing asserts the correlation; that is a second coupling, not a removal of the first), or decide the echo's contract explicitly and give `generate_structured` the same pre-validation hook (MEDIUM — it is a shared-behaviour decision across two egress paths, plus tests on both). Sizing assumes no change to any output schema, which is out of scope. **Not fixed here, by instruction.**

### 26. OWNER DECISIONS the code is waiting on

| # | Decision | Blocks | Class |
|---|---|---|---|
| 1 | **Approve `docs/04_decision_engine/policy_inputs.md`** — "Status: DRAFT — AWAITING OWNER APPROVAL" (`:3`); "Faz 2 (Rego) is BLOCKED until each section reaches `[APPROVED]`" (`:20`). The file is still untracked (`git status --porcelain`) | Real Rego authoring, therefore E18, therefore OPA enablement | **Feature** — the inline evaluator is correct and live today (`bootstrap.py:294`) |
| 2 | **OPA production enablement** (`SKYLIZE_DECISION_ENGINE=opa`) — gated by ADR-0004 §Decision 4; `bootstrap.py:276-282` fails closed | Nothing operationally today; the flag stays `"inline"` and one engine emits terminal events either way | **Feature** |
| 3 | **n8n admin governed rewrite** (ADR-0003 §3) — hard gate before `SKYLIZE_ENABLE_N8N_ADMIN=true` may be set; route returns 501 unless the env var is exactly `"true"` (`website/src/app/api/console/workflows/route.ts:36,84-89`) | The n8n admin BFF surface | **Feature** |
| 4 | **`fix/c3-investor-status` sign-off** — commit is a `draft(docs)` marked "NEEDS HUMAN SIGN-OFF before external use" | External use of that document | **Feature** (documentation) |
| 5 | **Whether `dev_auth=true` is acceptable in any deployed environment** — no code decision has been made; `config.py:56` defaults it on and no validator forbids it on the postgres backend | Every authentication guarantee in a deployed environment (§3, E4) | **Correctness** |
| 6 | **Whether `users` / `api_keys` / `tenants` / `tenant_users` may remain outside RLS** — `0008_users.py:65` records the rationale ("auth layer needs cross-tenant email lookup") but not an approval | E12, and the honest answer to "is customer data tenant-isolated?" | **Correctness** |
| 7 | **Whether the spend ceiling being a soft cap is acceptable for real money** — `adapters/llm/spend_ceiling.py:9-19` states it plainly and calls a hard cap "out of scope here and noted as a future item" | Whether E13 is required before a paying customer, or after | **Correctness** |

Decisions 1-4 block features. Decisions 5-7 block correctness claims that a diligence conversation or a first invoice would test.

**`docs/REPO_STATE.md:198` lists a fifth outstanding decision — "Root `CLAUDE.md` — ABSENT". That is resolved:** `CLAUDE.md` exists at the repo root and is tracked as of commit `22aeffed` ("docs: add root CLAUDE.md orientation").

---

## DISAGREEMENTS WITH `docs/REPO_STATE.md` (R4 — code wins)

1. **Branch push state.** REPO_STATE:46-47 reports the branch 46 commits ahead of its upstream with all 46 unpushed, and 62 commits existing only on this machine. At `22aeffed`: `git rev-list --count feat/durable-governance --not --remotes` = **0**, `git log --all --not --remotes` = **2**. The branch was pushed after that audit.
2. **Root `CLAUDE.md`.** REPO_STATE:198,208 records it ABSENT and lists creating it as an owner decision. It exists and is tracked (`CLAUDE.md`, commit `22aeffed`).
3. **Router mount line numbers.** REPO_STATE:61 cites `gateway.py:73-84` for 13 routers and `:85` for spend; the current file mounts them at `gateway.py:74-86`. Immaterial, but the citations have drifted by one line.

Everything else in REPO_STATE that this audit touched — the two engines, the three ledgers, the Rego placeholders, the governance outcome distribution, the fail-closed environment variables — held on re-verification.

---

## FINAL `git status --porcelain` (raw)

```
?? docs/04_decision_engine/policy_inputs.md
?? docs/MVP_GAP_ANALYSIS.md
```
