# REPO_STATE — read-only audit mirror

**Generated:** 2026-07-29 by a read-only audit (no source files modified; the only write is this file).
**Describes commit:** `834153c9cc3c3da3415b0b22714e79d87440aacd` on branch `feat/durable-governance` (figures re-verified and some resolved through 2026-07-29; see the dated notes inline).
**See also:** `/CLAUDE.md` at the repo root — the short, durable orientation (architecture constraints, the two engines, the three ledgers, environment, testing) that points here for changing state.
**Method:** code is ground truth; docs/comments/ADRs are claims tested against code. Every line carries a `file:line` citation, or an explicit **ABSENT** / **UNVERIFIED**. This is a mirror, not a plan — no recommendations.

**Provenance note:** Part 2 items 8–13 were verified by subagents in a fan-out workflow; the other parts were verified directly in the main loop after 6 of 9 workflow agents aborted on a usage cap. Items marked UNVERIFIED are those a single session could not confirm cheaply; each states what confirmation would take.

---

## OPENS SUMMARY

```
SUITE: 1255 / 2 / 0            (passed / skipped / failed, services up)
OPEN DEFECTS: 4          (was 5; #5 resolved 2026-07-29, see below)
STALE CLAIMS: 14
NOT WIRED SUBSYSTEMS: 7
OWNER DECISIONS OUTSTANDING: 5
UNMERGED BRANCHES: 12
COMMITS LOCAL ONLY: 62    (at 834153c9; see Branch topology for the drift note)
STRANDED BRANCHES: 10
MYPY UNCHECKED SUBTREES: 4 — live: 0   (was 5/live-1; app.decision_engine.* now checked)
```

---

## MEASURED STATE

### Test suite (services up: Postgres + Redis + OPA, all four SKYLIZE_TEST_* set session-scoped)
- **1255 passed, 2 skipped, 0 failed** (`pytest -q -rs`, 121s). Both skips are **dead-code class**, not service-conditional:
  - `tests/unit/test_llm_agent_runner.py:61` — "runtime/ LLMAgentRunner ctor drifted; the runtime alt-stack is dead code with no tracked removal plan (LLMStepRunner is the live runner)".
  - `tests/unit/test_memory_gateway.py:79` — "chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap (dead code, no tracked rework plan)".
- Postgres-backed tests **ran** (did not skip): `test_postgres_isolation.py` 6 passed, `test_jsonb_readback_pg.py` / `test_org_spend_ceiling_pg.py` / `test_spend_position_endpoint.py` / `test_workflow_repository.py` all executed.

### Static gates (raw)
- **mypy:** `Success: no issues found in 205 source files` — **but** 5 subtrees are excluded via `ignore_errors` (see OPEN DEFECTS / mypy). "Clean" is qualified.
- **lint-imports:** `Analyzed 260 files, 1188 dependencies. ... Contracts: 5 kept, 0 broken.` (exit 0)
- **check_forbidden_imports.py:** `OK: no direct LangChain/CrewAI imports in scanned sources.` (exit 0)

### Alembic
- **Head: `0015`** (`migrations/versions/0015_hitl_request_json.py`). Unbroken chain `<base> → 0001 → … → 0015`, files `0001_initial_schema.py` … `0015_hitl_request_json.py` (15 files, one per revision).

### Branch topology / remote (A4, raw)
- `git rev-parse main` = `603936a010b8c5ad08d6b893e5d07ba141951198`; `git rev-parse origin/main` = same. **main is fully pushed** — `git log origin/main..main` and `git log main..origin/main` both empty.
- Current branch `feat/durable-governance` = `834153c9`, tracks `origin/feat/durable-governance` (`603936a0`) but is **46 commits ahead of that upstream** — all 46 unpushed.
- **62 commits exist only on this machine** (`git log --oneline --all --not --remotes | wc -l` = 62 at `834153c9`): reachable from local refs, on no remote. **Verified 2026-07-29 (Part 1):** this does **not** contradict "main fully pushed". The fast-forward hypothesis was tested — `git merge-base --is-ancestor feat/durable-governance origin/main` returns non-zero, so `feat/durable-governance`'s commits are genuinely absent from `origin/main`; the 62 live on unpushed feature branches, not on `main`. The count drifts +1 per new local commit (63 after the audit commit `65c2451a`, and higher after each Part-1/2/3 commit) until branches are pushed.
- Remote: `origin https://github.com/skylizeai-dev/Skylize.git`. Only 6 local branches have upstreams (`feat/durable-governance`, `feat/grammar-gateway`, `feat/tool-dedup-convergence`, `release/console-m1`, plus `main`→origin/main and `fix/dal-ports-workflow-repo`→origin/main); **~44 local branches have never been pushed**.
- 46 git worktrees registered (`git worktree list`); many `wt-*` worktrees sit exactly at `603936a0` (= main tip).

### Git status --porcelain (raw)
```
?? docs/04_decision_engine/policy_inputs.md
```
(After this audit, `docs/REPO_STATE.md` is added as a second entry.) `.hypothesis/` does not appear: it is ignored via its own nested `.hypothesis/.gitignore:9` (`*`), not via the root `.gitignore` (which has **no** hypothesis entry).

---

## WIRED (reachable from an HTTP request today)

Composition root `src/skylize/edge/gateway.py:73-84` mounts 13 routers. Auth dependency in parentheses; org_id always from `RequestContext`, never a query/body field.

- `GET /health` (gateway.py:69) → static dict.
- **tenants** (`edge/routes/tenants.py`) → `TenantService` → `PgTenantRepository` (admin_session) → Postgres `tenants`/`tenant_users`.
- **api_keys** (`edge/routes/api_keys.py`) → `ApiKeyService` → `PgApiKeyRepository` → Postgres `api_keys`.
- **auth** (`edge/routes/auth.py`) → `UserAuthService` (HS256 JWT) → `PgUserRepository` → Postgres `users`/`user_refresh_tokens`.
- **agents** `POST /api/v1/agents/execute` (`edge/routes/agents.py`) → `AgentExecutionService.execute` (`app/agents/execution.py:218`) → **synchronous decision gate** (`DecisionEvaluator.evaluate`, execution.py:415) for governed orgs → LLM egress (`AnthropicAdapter`) + `PgDeliverableRepository` + `CostLedgerDAL.record_cost` + `PgHitlQueueRepository` (defer). External effect: **Anthropic HTTP**, `deliverables`/`ai_cost_ledger`/`hitl_queue`/`audit_log` writes, Redis events.
- **agent_prompts** `GET /api/v1/agent-prompts/{id}` (`edge/routes/agent_prompts.py`) — n8n inbound, static-key auth (`X-Skylize-API-Key` == `settings.n8n_api_key`, agent_prompts.py:23-26).
- **credentials** (`edge/routes/credentials.py`) → `CredentialVault` → `PgCredentialRepository` (Fernet at-rest) → Postgres `org_credentials`.
- **deliverables** (`edge/routes/deliverables.py`) → `DeliverableService` → `PgDeliverableRepository` → Postgres `deliverables` (JSONB read-back via the pool codec, `dal/connection.py:26-56`).
- **hitl** (`edge/routes/hitl.py`) → `HitlQueueService` → `PgHitlQueueRepository` (exactly-once claim UPDATE) → Postgres `hitl_queue`/`decisions`; approve replays through `AgentExecutionService`.
- **workflows** `POST /api/v1/workflows/creative` (`edge/routes/workflows.py:44-64`) → `Orchestrator.invoke` (`app/orchestrator/orchestrator.py:62`) → **LangGraph** `build_creative_graph(...).ainvoke` (orchestrator.py:54,111; `langgraph.graph.StateGraph` imported at `workflows/creative_workflow.py:20-21`) → `LLMStepRunner` → Anthropic HTTP. **This is the one live LangGraph path.** It runs `authority.assert_active` + token mint but **not** the HITL decision gate (that is the `/agents/execute` path).
- **kill_switch** (`edge/routes/kill_switch.py`) → `GovernanceAuthority` → `PgGovernanceRepository` → `kill_switch_state`.
- **knowledge** (`edge/routes/knowledge.py`) → `KnowledgeIngestionService` → `QdrantAdapter` + OpenAI embeddings; `/ingest` HMAC webhook (n8n), `/upload`/`/search` org-scoped.
- **audit** `GET /api/v1/audit` (`edge/routes/audit.py`) → `AuditService.recent` → `PgAuditRepository` → `audit_log` (owner/admin).
- **spend** `GET /api/v1/spend/position` (`edge/routes/spend.py`, mounted gateway.py:85) → `CostLedgerDAL.org_period_total_micros` + `OrgSpendCeilingDAL.read_ceiling_micros` (owner/admin); returns 503 if `container.cost_ledger`/`spend_ceiling_dal` is None (memory backend).
- Middleware: CORS installed only if `settings.cors_origins` non-empty (gateway.py:56-67); rate limiting via `RateLimiter` deps.
- **Inline Decision Engine** (`app/decision_engine`, `DecisionEngine`) is wired at bootstrap.py:294 and runs as an in-process bus consumer for `decision_engine_org_ids`; it is the sole terminal-`decision.*` emitter while `SKYLIZE_DECISION_ENGINE="inline"`.

---

## NOT WIRED (exists in code, unreachable from a live request)

1. **OPA decision engine package** (`src/skylize/decision_engine/`) — runs only as its own worker (`python -m skylize.decision_engine.worker`), never inside the API process. `bootstrap.py:276-280` **raises RuntimeError** for any `SKYLIZE_DECISION_ENGINE` != `"inline"`; the worker refuses anything but `"opa"`. Consumer/pipeline/opa_client/publisher/outbox_poller/resume are implemented; docker-compose gates the worker behind `profiles: ["opa-engine"]`. *Needs:* real Rego (see below) + live OPA + production-readiness certification to flip the flag.
2. **Temporal worker + LLMJudge** (`src/skylize/app/orchestrator/temporal/`) — `worker.py` exists but **no `temporal` reference in bootstrap.py or gateway.py**; nothing schedules it. Import-linter exemption (below) documents it as "unwired/paused". *Needs:* a live entrypoint + the concrete judge activity.
3. **PgMemoryAdapter + MemoryGateway + `agent_memory_entries`** — `PgMemoryAdapter` (`dal/memory_adapter.py:35`) is constructed nowhere; `MemoryGateway` only in unit tests; the table it reads has **no migration** (see OPEN DEFECTS item 9). *Needs:* DDL + wiring + pgvector.
4. **runtime alt-stack: `runtime/tool_proxy.py` + `LLMAgentRunner`** — bootstrap wires `tools/proxy.py::ToolProxy` (bootstrap.py:59), not the `runtime/` one; `LLMAgentRunner` is never constructed in `src/` (only re-exported). Dead. *Needs:* a decision to revive or delete.
5. **mem0 adapter** (`memory/adapters/mem0_adapter.py`) — not imported in bootstrap. *Needs:* wiring + `mem0_api_key`.
6. **obsidian_writer** (`services/obsidian_writer/`) — no live import. Dead.
7. **n8n admin BFF** (`website/src/app/api/console/workflows/route.ts`) — gated **default-OFF** behind `SKYLIZE_ENABLE_N8N_ADMIN` (route.ts:36); returns HTTP 501 unless the env var is exactly `"true"` (route.ts:84-89). *Needs:* the governed rewrite in ADR-0003 §3 before any production enablement.

- **UNVERIFIED:** `generate_sync` / `generate_structured` (`adapters/llm/structured.py`) live-caller status was not traced this session; confirming would take a `structured`-symbol caller sweep excluding tests.

---

## OPEN DEFECTS

1. **`agent_memory_entries` table has no DDL; `PgMemoryAdapter` is dead code** (item 9, STILL OPEN). Read/written at `dal/memory_adapter.py:64,92`; `grep agent_memory` over `migrations/` = 0 matches (migration 0001 creates `memory_records` instead, `0001_initial_schema.py:232`). No construction site for `PgMemoryAdapter` anywhere. *Consequence:* if ever invoked it raises `UndefinedTableError` (and a second latent failure — the INSERT casts `::vector` at memory_adapter.py:99 while `infra/postgres/init/00_extensions.sql` never installs pgvector). Currently unreachable, so latent.
2. **`APIConnectionError` collapses retry-safe failures with ambiguous ones** (item 10, STILL OPEN). `anthropic_adapter.py:435` catches `APIConnectionError` → raises field-less `LLMProviderUnavailable` (`gateway.py:76-84`) with no retry (adapter is sole retry authority, `max_retries=0` at anthropic_adapter.py:273). The SDK preserves `exc.__cause__` (`httpx.ConnectError` = connection-refused/DNS, request never sent, retry-safe; vs `ReadError`/`RemoteProtocolError` = mid-flight reset, may be billed) but the adapter inspects neither. *Consequence:* provably-safe retries are refused as terminal. The in-code comment `anthropic_adapter.py:436-441` ("this seam cannot distinguish...") is overstated (see STALE CLAIMS).
3. **No sweep moves time-expired `hitl_queue` rows to `status='expired'`** (item 12, STILL OPEN). Both writers set `expires_at = now + 48h` (`decision_engine/hitl_writer.py:34,121`; `app/agents/execution.py:75,482` → `dal/hitl.py:119`). Verdicts on an expired row are refused with **HTTP 410** (`dal/hitl.py:173` predicate → `app/hitl/service.py:283-284` → `edge/routes/hitl.py:132-133,177-178`). But no cron/poller writes `'expired'` (grep over `src/` finds only migration 0015's one-time backfill keyed on `request_json IS NULL`, not on `expires_at`); no background task in the gateway lifespan (`edge/gateway.py:39-50`). `list_pending` filters only `status='pending'` with no `expires_at` predicate (`dal/hitl.py:133-142`). *Consequence:* expired rows linger in the pending list/count forever until someone attempts a verdict and gets the 410. (`'expired'` and `'modified'` are valid CHECK values, `0001_initial_schema.py:212-214`; `'modified'` is never written anywhere in `src/`.)
4. **`_check_budget` (adapter-level token guard) is dormant on every live path** (item 11). `anthropic_adapter.py:288-300`: early-returns unless `request.max_token_budget` is set; both request models default it `None` (`gateway.py:153-154,184-185`); no production construction site populates it (all live callers — execution.py:298,664; runner.py:111; structured.py:125 — pass neither field; only unit tests set them). *Consequence:* this defense-in-depth layer never fires; the live budget control is the separate `validate_tool_call` BUDGET stage (`contracts/token.py:282-285`), which does bite.
5. **~~Live code under mypy `ignore_errors` — `skylize.app.decision_engine.*` is type-unchecked~~ — RESOLVED 2026-07-29** (was addendum A1). The subtree was removed from `ignore_errors` (`pyproject.toml`); mypy reports **0 errors** for it (it was already type-clean — the exclusion masked nothing), so it is now checked by CI with the rest of the product, and the stale comment describing it as "NOT wired" was rewritten. The remaining 4 excluded subtrees are all genuinely off the live request path: **paused = 2** (`decision_engine.*` OPA worker — API fails closed on non-inline at `bootstrap.py:276`; `app.orchestrator.temporal.*` — not referenced in bootstrap/gateway); **dead = 2** (`services.obsidian_writer.*` — no live import; `memory.adapters.*` — mem0 adapter, not in bootstrap).

**Risk-class opens (unmerged work at risk):** 10 STRANDED branches and **62 commits that exist only on this machine** (see Branch Inventory). 8 of the 10 stranded branches have no remote at all.

---

## STALE CLAIMS (doc/comment vs contradicting code)

ADR staleness below is largely **expected** — ADRs are point-in-time records, and several items are follow-up the ADR itself scheduled, which has since landed. Reported per R2 regardless.

1. `src/skylize/contracts/registry.py:131` — comment "the **15** governed creative + growth contracts". Code: `ALL_MVP_CONTRACTS` has **21** members (runtime count; sibling docstring `contracts/mvp/__init__.py:4` correctly says "21").
2. `docs/architecture/adr/0004-...:24` — "No `SKYLIZE_DECISION_ENGINE` flag exists on this branch." Now exists: `config.py:107` `decision_engine: Literal["inline","opa"] = "inline"`; interlock `bootstrap.py:276-280`.
3. `docs/architecture/adr/0004-...:27` — "No `policy_inputs.md` exists in the repository." Now exists (untracked, DRAFT): `docs/04_decision_engine/policy_inputs.md:3`.
4. `docs/architecture/adr/0004-...:26` — consumer/`constants.py` "still target placeholder `SUBSCRIBED_STREAMS`". Removed: `decision_engine/constants.py:92` ("the `SUBSCRIBED_STREAMS` alias ... is GONE, as ADR-0005...").
5. `docs/architecture/adr/0005-...:95-98` — the flag-flip blocker list presents transport rebuild, `hitl_id` reconciliation, and HITL resume path as open. **Landed** on this branch: department table (`constants.py:31-52`), caller-minted single `hitl_id` (`publisher.py:245,462-464`; `hitl_writer.py:105-106`), and OPA resume (`decision_engine/resume.py:72` `HITLResumeHandler`, `worker.py:30`). Only "live OPA + real Rego" remains.
6. `docs/architecture/adr/0005-...:61` — `SalesCampaignProposed`/`SalesBudgetReallocationProposed` have "zero construction sites anywhere in `src/` or `tests/`". Now 4 **test** sites (`tests/decision_engine/test_consumer.py:44`, `test_consumer_integration.py:63`, `test_opa_client.py:407`, `tests/integration/test_decision_engine_consumer_redis.py:72`); still zero in `src/`.
7. `docs/architecture/adr/0006-...:43,91` — the concrete gateway adapter "do not exist yet" / calling `CostLedgerDAL.record_cost` "Deferred to T-B1B". **Landed:** `AnthropicAdapter` exists and calls `self._cost_ledger.record_cost` (`anthropic_adapter.py:524`); `LLMGenerateRequest`/`WithToolsRequest` now carry `correlation_id`/`agent_id` (`gateway.py:147-148,178-179`).
8. `docs/architecture/adr/0006-...:66` — "The seed is intentionally empty — no price is fabricated." Migration `0013_seed_model_pricing.py:101-104` seeds published prices.
9. `docs/architecture/adr/0006-...:92` — "pre-existing import-linter break ... via `app/orchestrator/temporal/worker`." Now **0 broken** (exemption `pyproject.toml:175-195`, added 2026-07-27, after the ADR).
10. `pyproject.toml:241-244` — mypy comment "Subsystems that are NOT wired into bootstrap (dead/paused code)". Lists `skylize.app.decision_engine.*` (`:248`), which **is** wired (`bootstrap.py:35,294`). (See OPEN DEFECTS 5.)
11. `src/skylize/adapters/llm/anthropic_adapter.py:436-441` (+ module docstring ~`:20-21`) — "this seam cannot distinguish a request the provider never saw from one it received and will bill." Overstated: the SDK exposes `exc.__cause__`; `httpx.ConnectError` (never-sent) is distinguishable from mid-flight resets.
12. `src/skylize/config.py:178` — comment "configurable so ops can update without redeploy" on the `llm_price_*` floats. They were **demoted** (commit `a2e3b023`) to a WARNING-logged fallback reached only when no cost ledger is wired (`anthropic_adapter.py:504-505,703-707`); `model_pricing` (Postgres) is the source of truth (`anthropic_adapter.py:695-702`).
13. `docs/06_integrations/anthropic.md:33-35` — "The proxy enforces ... that the call fits the token's `max_token_budget` **before** dispatch." True of the `validate_tool_call` pipeline, but the adapter-level `_check_budget` half is dormant (OPEN DEFECTS 4).
14. `src/skylize/config.py:181-184` float defaults vs `migrations/versions/0013_seed_model_pricing.py:38-43` (doc claim: seeded table is authoritative) — haiku default `0.80/4.0` and opus `15.0/75.0` do not match the migration's stated published prices; on the memory-backend fallback path this mischarges (haiku under, opus over). Sonnet `3.0/15.0` matches.

**Checked and still TRUE (load-bearing):** ADR-0001 P-384 scheme (`contracts/token.py:38` `GOVERNANCE_CURVE=Curve.P384`, `app/governance/keys.py:70-75` `_assert_p384`, `ecc_service.py:410`; no Ed25519 in code). ADR-0002 CrewAI absent (0 imports in `src/`, not a dependency, CI-enforced) and LangGraph is the sole orchestration framework actually used (`workflows/creative_workflow.py:20-21`, live via `/workflows/creative`). ADR-0003 n8n gate default-OFF + 501 (route.ts:36,84-89; `.env.example:73`, `website/.env.local.example:37` empty). ADR-0006 money rules (Decimal + ROUND_HALF_UP, per-Mtok integer prices, `cost_micros` BIGINT, `ON CONFLICT (org_id, idempotency_key) DO NOTHING` at `dal/cost_ledger.py:245`, DB-enforced append-only). The 7 Rego files are still fail-closed placeholders — 0 `allow := true`, 128 lines total (`policy/skylize/decision/*.rego`); docker-compose's "denies everything" comment (`infra/docker-compose.yml:36,108-109`) is **accurate**.

---

## GOVERNANCE OUTCOME DISTRIBUTION (item 24)

`ALL_MVP_CONTRACTS` = **21** agents (runtime count; not 15). On `POST /api/v1/agents/execute` for a **governed** org (`org_id in _governed_org_ids`, `execution.py:218`), the sync gate calls the inline `DecisionEvaluator`'s `agent.execute` rule (`app/decision_engine/evaluator.py:190-259`):
- `FIRST_EXTERNAL_LAUNCH` present → **defer** (evaluator.py:216-229).
- no triggers → **approve** (evaluator.py:230-240).
- any other trigger(s) → **defer** (owner decision 2026-07-28, fail-closed; evaluator.py:247-259).
- **reject** is not a per-agent static outcome — reachable only for an invalid proposal at `policy_check` (unknown action_kind / invalid spend / `metadata.brand_safety=="blocked"`, evaluator.py:310-326), i.e. input-content-dependent.

**Distribution over the 21 (valid input, governed org): 9 approve / 12 defer / 0 reject.**
- **Approve (9, no triggers):** ad_copy_agent, agency_requirements_analyst, caption_writer_agent, creative_operations_manager, cta_optimizer_agent, lead_qualifier_agent, script_writer_agent, seo_keyword_agent, tone_of_voice_agent.
- **Defer (12):** director_growth, hook_generator_agent, sdr_outreach_agent, vp_creative (FIRST_EXTERNAL_LAUNCH); agency_deliverable_drafter, art_director, brand_guardian_agent, copy_director (BRAND_LEGAL_SENSITIVE); ceo, cmo (spend_over_ceiling + brand); cfo_agent (spend_over_ceiling + low_confidence_irreversible); fraud_detection_agent (security_severity_high + low_confidence_irreversible).
- **Caveat:** for a **non-governed** org the gate is dormant (`execution.py:218`) and every agent executes. The `/workflows/creative` LangGraph path does **not** apply this gate at all.

---

## ENVIRONMENT VARIABLES (item 25)

Prefix `SKYLIZE_`; source `src/skylize/config.py`. Behavior when absent:

**Fails closed at startup:**
- `SKYLIZE_ANTHROPIC_API_KEY` — absent AND `llm_demo_mode` false → `bootstrap` raises `LLMConfigurationError` (config.py:140-145; the raise is in bootstrap's LLM branch). With `llm_demo_mode=true` → `DemoLLMAdapter` (WARNING per call).
- `SKYLIZE_JWT_SECRET` — absent AND `dev_auth` false → `ValueError` at boot (`config.py:198-205`).
- `SKYLIZE_CORS_ORIGINS` containing `"*"` → `ValueError` at boot (`config.py:186-195`).
- `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` — on `backend != "memory"` when empty → fails closed (`config.py:45-50`); on memory backend → **ephemeral P-384 key generated with a WARNING** (`app/governance/keys.py:63`).
- `SKYLIZE_DECISION_ENGINE` — any value but `"inline"` in the API process → `RuntimeError` (`bootstrap.py:276-280`).

**Defaults, fully functional:** `SKYLIZE_BACKEND` (`memory`), `SKYLIZE_DB_URL`/`DB_APP_URL`/`REDIS_URL`, `TOKEN_TTL_MINUTES` (5), `DEV_AUTH` (true), `REQUEST_CONTEXT_TTL_SECONDS` (300), rate limits (120 / 10), `DLQ_AFTER_RETRIES` (5), `TEMPORAL_*`, `LLM_MODEL_*`, `LLM_RETRY_*`, `LLM_TIMEOUT_SECONDS` (120), `LLM_PRICE_*` floats (fallback only), `JWT_*_TTL`, `QDRANT_URL`.

**Silently degrades (feature off / fallback):** `SKYLIZE_DB_APP_URL` empty → falls back to `db_url` (`config.py:40-43`; "acceptable only for local/dev"). `CREDENTIAL_ENCRYPTION_KEY` empty → ephemeral dev key (memory backend). `CORS_ORIGINS` empty → middleware not installed. `DECISION_ENGINE_ORG_IDS` empty → engine idle. `N8N_API_KEY`/`KNOWLEDGE_WEBHOOK_SECRET` empty → that check disabled. `SEARCH_API_KEY` empty → `NullWebSearchPort` (empty results). `MEM0_API_KEY`/`OPENAI_API_KEY`/`LANGFUSE_*`/`QDRANT_API_KEY` empty → that integration off. `ANTHROPIC_BASE_URL` None → SDK default endpoint.

**Decision-engine worker** reads a separate `DecisionEngineSettings` (`decision_engine/config.py`); it reads `SKYLIZE_DATABASE_URL` (not the gateway's `SKYLIZE_DB_URL` pair) and requires `SKYLIZE_LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` (no defaults). **UNVERIFIED:** a field-by-field enumeration of `decision_engine/config.py` was not completed this session; confirming would take a full read of that file.

`.env.example:73` and `website/.env.local.example:37` document `SKYLIZE_ENABLE_N8N_ADMIN=` (empty = off). No `src/` code reads any `SKYLIZE_TEST_*` var (those are test-harness only).

---

## BRANCH INVENTORY (addendum A2)

12 branches with commits not in `main` (`git branch --no-merged main`). SUPERSEDED determined by `git cherry main <branch>` (patch-equivalence), not by name.

| Branch | Ahead of main | Tip (date) | Remote? | Class |
|---|---|---|---|---|
| feat/durable-governance | 46 (all unpushed) | 2026-07-28 | yes (46 ahead of it) | **STRANDED** (active HEAD) |
| release/console-m1 | 6 (cherry: 3 new) | 2026-07-15 | yes (43 ahead of it) | **STRANDED** |
| fix/knowledge-tenant-identity | 2 | 2026-07-03 | no | **STRANDED** |
| audit/decision-consumer-gap | 1 | 2026-07-25 | no | **STRANDED** |
| chore/import-linter-orphan-check | 1 | 2026-07-13 | no | **STRANDED** |
| feat/capital-budget-reservation | 1 | 2026-07-25 | no | **STRANDED** |
| feat/grammar-gateway | 1 | 2026-06-28 | yes (pushed) | **STRANDED** (backed up) |
| fix/c3-investor-status | 1 | 2026-07-12 | no | **STRANDED** ("NEEDS HUMAN SIGN-OFF") |
| fix/outbox-canonical-envelope | 1 | 2026-07-25 | no | **STRANDED** |
| worktree-bus-audit-gov | 1 | 2026-07-25 | no | **STRANDED** |
| feat/tool-dedup-convergence | cherry: 1 superseded (−) | 2026-06-28 | yes | **SUPERSEDED** |
| feat/workflow-repository-postgres | cherry: 1 superseded (−) | 2026-07-13 | no | **SUPERSEDED** |

STRANDED = 10, SUPERSEDED = 2 (unchanged after the Part-1 re-verification 2026-07-29; the audit figures were correct). Refinement via `git rev-list --count <branch> --not --remotes` (commits on the branch reachable from **no** remote ref) and `git cherry main <branch>`:
- **9 of the 10 stranded carry commits on no remote** (at-risk of machine loss): `feat/durable-governance` (47), `fix/knowledge-tenant-identity` (2), and 1 each on `release/console-m1` (5 of its 6 ahead are off-remote), `audit/decision-consumer-gap`, `chore/import-linter-orphan-check`, `feat/capital-budget-reservation`, `fix/c3-investor-status`, `fix/outbox-canonical-envelope`, `worktree-bus-audit-gov`.
- **`feat/grammar-gateway` is stranded-vs-main but fully pushed** (`--not --remotes` = 0, on `origin/feat/grammar-gateway`) — not at risk of loss.
- **Both SUPERSEDED branches** show `git cherry` `-` (patch already on `main`): `feat/tool-dedup-convergence` (also pushed, 0 off-remote) and `feat/workflow-repository-postgres` (1 off-remote commit object, but its patch equivalent is on `main`, so the work is safe).
- The authoritative de-duplicated at-risk total is `git log --all --not --remotes` (62 at `834153c9`), not the per-branch sum (which double-counts commits shared through the `release/console-m1` merges). Finer ABANDONED-vs-STRANDED classification beyond the `git cherry` signal remains **UNVERIFIED** (would take per-branch content review).

---

## OWNER DECISIONS OUTSTANDING

1. **Approve `policy_inputs.md`** (`docs/04_decision_engine/policy_inputs.md:3` — "Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)"; :20 "Nothing here is `[APPROVED]` ... Faz 2 (Rego) is BLOCKED until each section reaches `[APPROVED]`"). Blocks: real Rego authoring; the file is untracked. (ADR-0004 §4 condition 2, ADR-0005 blocker 5.)
2. **OPA production enablement** (`SKYLIZE_DECISION_ENGINE=opa`). Blocked on real Rego + live OPA + wire-parity/production-readiness certification (ADR-0004 §Decision 4); `bootstrap.py:276-280` fails closed to `inline`. Transport, hitl_id, and resume-path blockers have landed; the 7 Rego files remain fail-closed placeholders.
3. **Root `CLAUDE.md`** — **ABSENT** at repo root (`Test-Path CLAUDE.md` false; `git ls-files CLAUDE.md` empty). A `website/CLAUDE.md` exists for the console subdir. Per the audit brief, no root CLAUDE.md was created; whether to create one is an owner decision.
4. **n8n admin governed rewrite** (ADR-0003 §3) — required before `SKYLIZE_ENABLE_N8N_ADMIN=true` may ever be set in production (hard gate, ADR-0003 §Decision 2).
5. **`fix/c3-investor-status` sign-off** — branch commit is a `draft(docs)` "NEEDS HUMAN SIGN-OFF before external use"; stranded pending owner review.

---

## STATUS DOCUMENTS PRESENT (Part 5 recon)

- Root: `DECISIONS_PENDING.md` (overnight 2026-07-14, with RESOLVED addenda), `OVERNIGHT_REPORT.md`, `OVERNIGHT_SESSION_2026-07-19.md`, `OVERNIGHT_SESSION_2026-07-21.md`, `OWNER_DECISIONS_QUEUE_2026-07-19.md`, `OWNER_DECISIONS_QUEUE_2026-07-21.md`, `SESSION_A_REPORT.md`, `SESSION_B_REPORT.md`, `WORKTREE_AUDIT.md`.
- `docs/`: `docs/audits/console_state_audit.md`, `docs/testing/test_suite_health_2026-07-19.md`, `docs/testing/test_suite_health_2026-07-21.md`, `docs/architecture/decisions/import_linter_exemptions.md`. `audits/pending_resolve_hardening.patch`.
- **`docs/REPO_STATE.md`: did not exist before this audit** (this is the first). **Root `CLAUDE.md`: ABSENT.** `docs/mvp/key_ready_gate.md`: **ABSENT** — no `docs/mvp/` dir; no `key_ready`/`ready_gate` file tracked or on disk (item 20). A gate document drafted in a prior session was evidently never committed.

---

## ADR NUMBERING SCHEMES (item 21)

- **In-repo:** `docs/architecture/adr/` holds 6 four-digit ADRs, `0001`–`0006` (governance-signature, crewai-removal, n8n-admin, opa-arbiter, dept-vocabulary, ai-cost-ledger). Highest = **0006**. Separate un-numbered decision doc: `docs/architecture/decisions/import_linter_exemptions.md`.
- **External Word-doc register:** referenced only inside ADR-0004 (`0004-...:6,20,68`) as the owner's "ADR register (Word doc)" using a three-digit scheme ("ADR-003"). **Not present** in the repo, its history, or any branch/worktree — content **UNVERIFIED** (external file). ADR-0004 itself flags the two schemes as unreconciled. Not reconciled here, per the brief.

---

## CONVENTIONS THAT BIT US (environment + process facts for the next session)

- **PowerShell 5.1 is the primary shell.** No `\` line-continuation; avoid em-dashes/curly quotes in git args; `2>$null` (not `2>/dev/null`) in PS, but the Bash tool uses POSIX. Set the four `SKYLIZE_TEST_*` vars **session-scoped** per command.
- **A money-path claim is only believed if Postgres-backed tests RUN, not skip.** The integration suite skips silently without `SKYLIZE_TEST_DB_URL`/`APP_DB_URL`; confirm execution (e.g. `test_postgres_isolation` 6 passed) before trusting any ledger/ceiling/JSONB result. Owner/app roles: `skylize` (owner, migrations) vs `skylize_app` (non-superuser, RLS-subject) — RLS tests must run as the app role or they prove nothing.
- **ripgrep respects `.gitignore` during traversal.** `.next/` is ignored (`.gitignore:26`), so a plain `rg` from the repo root never descends into `website/.next/` or the stale nested `website/website/.next/` (item 15: exists on disk, untracked, ignored). Naming an ignored path explicitly on the command line overrides this (`rg --files website/.next` lists files); a repo-wide sweep needs `--no-ignore` to see ignored trees. The prior-session claim "ripgrep silently skips route.ts" is **false as stated** — all 13 real `website/src/app/api/**/route.ts` files are listed by plain `rg`. The mechanism that could produce a "skipped file" observation is the gitignore-traversal rule above; whether the original observation was a `.next/`-nested path is **UNVERIFIED** (the nested cache dir holds no route file this session could find).
- **Hypothesis stores counterexamples per-directory** in `.hypothesis/` (self-ignored via its own `.hypothesis/.gitignore`, not the root `.gitignore`). A property test passing in one worktree can mean the counterexample was never replayed there — reason about the code, not the green tick (item 8: `point_id` is injective up to SHA-1 by construction, `memory/identity.py:46-69`, so the pass is sound here).
- **Two ADR numbering schemes exist and are not synchronised** (see above): in-repo four-digit `0001–0006` vs an external Word-doc three-digit register that is not in the repo.
- **mypy "clean" is qualified:** 5 subtrees are under `ignore_errors` (`pyproject.toml:246-252`), one of them live (`app.decision_engine.*`). Read the exclusion list before trusting a green mypy.
- **`git cherry main <branch>`** distinguishes superseded (`-`, patch already on main) from genuinely-new (`+`) commits — the reliable signal for merge-state, since `--no-merged` alone counts rebased/superseded work as unmerged.
- **62 commits live only on this machine**; `main` is fully pushed but `feat/durable-governance` is 46 ahead of its remote, and ~44 local branches have never been pushed.
