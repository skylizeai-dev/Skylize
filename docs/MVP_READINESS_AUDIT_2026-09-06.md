# MVP READINESS AUDIT — 2026-09-06

**Commit:** `a474c2c83ad6425eeb8187562bd3b0a4e5895906` on branch `main` (0 ahead / 26 behind `origin/main`)
**Test baseline (this run, no service env vars):** 1949 passed / 256 skipped / 0 failed — 2205 collected
**Method:** read-only code audit. No source file was modified; the only write is this file. Every claim carries a `file:line`, or an explicit **ABSENT** / **UNVERIFIED**. Prior `docs/MVP_GAP_ANALYSIS.md` (describes `4c6f4511`, 2026-07-31) and `docs/REPO_STATE.md` (describes `834153c9`, 2026-07-29) were NOT used as evidence — both describe commits ~5 weeks older than HEAD.

---

## EXECUTIVE SUMMARY

**Demo-ready: ~70%.** The governance story — the thing this product actually sells — runs end to end today with zero external API keys. I executed it live: register → execute → `202 deferred_to_human` → HITL queue → approve → deliverable, plus a kill switch that produces a real `403 governance denied`. What is missing at the demo bar is *breadth*: 8 of 23 agents have a demo payload, and the console shows no audit log, no spend, and no deliverable history.

**Pilot-ready: ~25%.** Three structural gaps, each independently fatal:

1. **No customer can connect anything.** There is no OAuth authorization-code flow anywhere in the codebase. `OAuthCredentialService` only *refreshes* a grant that already exists; the only caller of `OAuthCredentialRepository.insert` is the test suite.
2. **No customer can sign up.** `users.org_id` is `REFERENCES tenants(org_id)` and nothing in the request path creates the `tenants` row, so registration into a genuinely new org fails on a foreign key. The `principal` row a per-employee run needs is created only by a one-shot backfill migration.
3. **The money ceiling is unreachable.** Not one tool in the registry declares `spend=ToolSpendProfile(...)`, so the entire reserve/commit/release path — and the GCP containment auto-hook that hangs off it — is dormant in every deployment.

**Top 5 blockers, by unblock-value:**

| # | Blocker | Bar |
|---|---|---|
| 1 | No OAuth connect flow — every connector is unreachable by a customer | PILOT |
| 2 | No self-serve tenant provisioning (FK chicken-and-egg) + no principal creation | PILOT |
| 3 | `decision_engine_org_ids` defaults to `[]` — the HITL gate is OFF unless an operator names each org by hand | PILOT |
| 4 | No tool is spend-capable — the ceiling, the ledger, and the containment hook are all dormant | PILOT |
| 5 | 15 of 23 agents cannot be demoed; no per-agent system prompts exist | DEMO |

**Honest gap assessment.** The governance spine is genuinely built and genuinely tested — this is not scaffolding. What is missing is the entire *commercial perimeter* around it: the way a customer gets in, connects their tools, and sees their money. Every one of those three is a greenfield build, not a fix. The spine could carry a pilot; nothing around it can.

---

## SECTION 1 — REPOSITORY VITALS

| Item | Value | Evidence |
|---|---|---|
| Branch / HEAD | `main` @ `a474c2c8` | `git rev-parse HEAD` |
| Position vs origin | 0 ahead, **26 behind** `origin/main` | `git rev-list --left-right --count origin/main...HEAD` → `0  26` |
| Working tree | clean | `git status --porcelain` (empty) |
| Tests collected | 2205 | `pytest --co -q` |
| Tests run | 1949 passed / 256 skipped / **0 failed** | `pytest -q -p no:randomly`, 171.69s |
| Source files | 252 `.py` under `src/` | `find src -name "*.py" \| wc -l` |
| Source lines | 40,723 | `find src -name "*.py" -exec cat {} + \| wc -l` |
| Test files | 194 `test_*.py` | `find tests -name "test_*.py" \| wc -l` |
| Test lines | 49,150 | `find tests -name "*.py" -exec cat {} + \| wc -l` |

**Exit-gate check (prompt: "STOP if new failures vs 2092+ passed"): PASSED — 0 failures.** The 2092+ figure was recorded with service env vars set; this run had none, so 256 integration tests self-skipped rather than running. The comparable figure is *failures*, and it is zero.

### CI gates — all green locally at HEAD

`.github/workflows/ci.yml` defines three jobs: `unit` (ci.yml:13), `website` (ci.yml:41), `integration` (ci.yml:66).

| Gate | Result |
|---|---|
| `ruff check src tests` | All checks passed |
| `lint-imports` | **Contracts: 5 kept, 0 broken** (309 files, 1505 dependencies) |
| `python scripts/check_forbidden_imports.py` | `OK: no direct LangChain/CrewAI imports` |
| `python scripts/check_all_modules_importable.py` | `OK: 252 modules import cleanly` |
| `python scripts/find_orphan_modules.py` | `OK: no new orphan modules (12 known, allowlisted)` |
| `mypy src` | `Success: no issues found in 252 source files` |
| `pytest -q` | 1949 / 256 / 0 |
| `npm run typecheck` (website) | clean |
| `npx vitest run` (website) | **10 files, 47 tests passed** |

Note on the prompt's suggested command: `python -m importlinter` fails — the package has no `__main__`. The correct invocation is the `lint-imports` console script, which is what CI uses (ci.yml:22).

**Not run in this audit:** the `integration` job. Postgres is listening on `localhost:5432` (`pg_isready` → accepting connections), but `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_APP_DB_URL` / `SKYLIZE_TEST_REDIS_URL` are unset in this environment, and provisioning the `skylize_app` role is a database mutation outside a read-only audit's remit. **Per CLAUDE.md's evidence discipline: no claim in this document about RLS enforcement, money, or tenant isolation rests on a test I watched run.** Those claims rest on migration source, cited by line.

### Dead code surfaced by the skip reasons

Two skips are not "service unavailable" — they are self-declared dead code:

- `tests/unit/test_memory_gateway.py:79` — *"chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap (dead code, no tracked rework plan)"*
- `tests/unit/test_llm_agent_runner.py:61` — *"runtime/ LLMAgentRunner ctor drifted; the runtime alt-stack is dead code with no tracked removal plan (LLMStepRunner is the live runner)"*

---

## SECTION 2 — AGENT REGISTRY & CONTRACT COMPLETENESS

`ALL_MVP_CONTRACTS` (`src/skylize/contracts/mvp/__init__.py:29`) resolves to **23 contracts**.

> **DOC/CODE CONFLICT.** The module docstring at `src/skylize/contracts/mvp/__init__.py:4` says *"22 contracts"*. Runtime `len(ALL_MVP_CONTRACTS)` is 23. Per the prompt's conflict rule I report both and do not resolve it. (`docs/04_decision_engine/policy_inputs.md:297` independently says *"24 live contracts"* — a third figure.)

### Field-level findings that apply to ALL 23 contracts

- **`system_prompt` — ABSENT as a contract field.** `AgentContract` (`src/skylize/contracts/base.py:84-155`) declares no such field, and `model_config = ConfigDict(frozen=True, extra="forbid")` (base.py:92) makes adding one per-contract impossible. Every agent's system prompt is synthesized generically at `src/skylize/app/agents/execution.py:1037-1052` — three lines: role, department, and "return only valid JSON". **No agent has bespoke instructions, few-shot examples, or domain guidance.** This is the single largest determinant of output quality and it does not exist.
- **`preferred_model` — ABSENT.** Verified by introspection: `"preferred_model" in AgentContract.model_fields` → `False`. Every call hardcodes `model="fast"` (`execution.py:387`, `execution.py:904`), which resolves to `llm_model_fast` = `claude-haiku-4-5-20251001` (`src/skylize/config.py:312`). **A CEO strategy agent and a caption writer run the same small model.**
- **`max_tool_iterations` is 5 for all 23** — no contract tunes it.
- **All 46 input/output schema dotted paths resolve.** Verified by importing each `contract.input_schema` / `contract.output_schema`: 23/23 OK.

### Master table

Legend — **demo**: canned payload in `demo_adapter.py:59-110`. **int**: an integration test naming the agent. **unit**: a unit test naming the agent. **loop**: non-empty `invocable_tools` (tool-use path) vs single-shot.

| # | agent_id | authority | demo | int | unit | loop | hitl_triggers | memory r/w |
|---|---|---|---|---|---|---|---|---|
| 1 | `ceo` | executive | ✗ | ✗ | ✓ | single | spend, brand_legal, low_conf | 3 / 2 |
| 2 | `cmo` | executive | ✗ | ✗ | ✓ | single | spend, brand_legal | 4 / 2 |
| 3 | `vp_creative` | vp | ✗ | ✓¹ | ✓ | single | first_external, brand_legal | 3 / 2 |
| 4 | `copy_director` | director | ✗ | ✓¹ | ✓ | single | brand_legal | 3 / 1 |
| 5 | `art_director` | director | ✗ | ✗ | ✓ | single | brand_legal | 2 / 1 |
| 6 | `creative_operations_manager` | manager | ✗ | ✗ | **✗** | single | — | 1 / 0 |
| 7 | `hook_generator_agent` | worker | **✓** | **✓** | ✓ | single | first_external | 2 / 0 |
| 8 | `ad_copy_agent` | worker | **✓** | ✗ | ✓ | single | — | 2 / 0 |
| 9 | `caption_writer_agent` | worker | **✓** | ✗ | ✓² | single | — | 2 / 0 |
| 10 | `script_writer_agent` | worker | **✓** | ✗ | ✓² | single | — | 2 / 0 |
| 11 | `cta_optimizer_agent` | worker | **✓** | ✗ | ✓² | single | — | 2 / 0 |
| 12 | `brand_guardian_agent` | worker | ✗ | ✗ | **✗** | single | brand_legal | 1 / 0 |
| 13 | `tone_of_voice_agent` | worker | ✗ | ✗ | ✓² | single | — | 1 / 0 |
| 14 | `director_growth` | director | ✗ | ✓¹ | ✓ | single | spend, first_external | 3 / 1 |
| 15 | `fraud_detection_agent` | worker | ✗ | ✗ | ✓² | single | security_high, low_conf | 2 / 1 |
| 16 | `seo_keyword_agent` | worker | **✓** | **✓** | ✓ | **loop** (search.web, memory.search) | — | 3 / 0 |
| 17 | `cfo_agent` | executive | **✓** | **✓** | ✓ | **loop** (utility.current_datetime) | spend, low_conf | 0 / 0 |
| 18 | `infrastructure_executor` | executive | ✗ | **✓** | ✓ | **loop** (integration.gcp_stop_instance) | first_external | 0 / 0 |
| 19 | `sdr_outreach_agent` | worker | ✗ | ✗ | **✗** | single | first_external | 2 / 1 |
| 20 | `lead_qualifier_agent` | worker | ✗ | ✗ | **✗** | single | — | 2 / 1 |
| 21 | `agency_requirements_analyst` | worker | ✗ | ✗ | **✗** | single | — | 2 / 1 |
| 22 | `agency_deliverable_drafter` | worker | ✗ | ✗ | **✗** | single | brand_legal | 3 / 1 |
| 23 | `cowork_agent` (sandbox) | worker | **✓** | **✓** | ✓ | **loop** (llm.generate, memory.search) | authority_exceeded, low_conf | 1 / 1 |

¹ incidental appearance in a governance/engine test, not a full execute→deliverable→readback proof.
² appears only in `tests/unit/test_contracts.py` (contract-shape assertions), not an execution test.

### Roll-up

- **Demo payload: 8 / 23 (35%).** `demo_adapter.py:59-110`. A 9th key, `brief_summarizer` (demo_adapter.py:104), is not a registered agent — it serves `edge/routes/brief.py:45`.
- **Full execute→deliverable→readback integration proof: 5 / 23 (22%)** — `hook_generator_agent` (`tests/integration/test_deliverable_readback_e2e.py`, `test_agent_execute_governed_e2e.py`), `seo_keyword_agent` (`test_seo_deliverable_e2e.py`), `cfo_agent` (`test_cfo_deliverable_e2e.py`), `infrastructure_executor` (`test_gcp_killswitch_e2e_pg.py`), `cowork_agent` (`test_cowork_chat_endpoint.py`).
- **Zero test coverage of any kind: 6 / 23 (26%)** — `creative_operations_manager`, `brand_guardian_agent`, `sdr_outreach_agent`, `lead_qualifier_agent`, `agency_requirements_analyst`, `agency_deliverable_drafter`.
- **Tool-loop agents: 5 / 23.** The other 18 are single-shot prompt-in/JSON-out.
- **Fully stateless (`memory_read_access == [] and memory_write_access == []`): 2** — `cfo_agent`, `infrastructure_executor`.

**Severity: BLOCKS_DEMO** for the 15 agents with no demo payload. **BACKLOG** for the 6 with no tests — they are registered but nothing routes to them.

---

## SECTION 3 — GOVERNANCE SPINE VERIFICATION

This is the strongest part of the codebase. Every invariant below is enforced in code, not merely documented.

### a. Governance token mint + pre-egress validation — ✅ ENFORCED ON BOTH PATHS

- **Mint:** `src/skylize/app/governance/authority.py:260` (`GovernanceAuthority.mint`). First statement of its body is `await self.assert_active(contract.agent_id, org_id)` (authority.py:294), which raises `GovernanceDenied` for a suspended or kill-switched agent *before* anything is signed.
- **Single-shot pre-egress gate:** `execution.py:362-386`. Runs the full ordered pipeline (`validate_tool_call`) with `requested_token_cost=max_tokens, tokens_used_so_far=0` **before** the `LLMGenerateRequest` is constructed at execution.py:387.
- **Tool-loop pre-egress gate:** `execution.py:877-902`, **inside the per-iteration loop** with `tokens_used_so_far=total_tokens` — a real running ledger, so the BUDGET stage can actually trip mid-run, and a token revoked or agent killed mid-loop is caught on the next turn.
- **Verdict: validation is on EVERY egress path.** Both branches audit the denial (`execution.py:373-386`, `execution.py:888-901`) before raising, and both distinguish `TokenBudgetExceeded` from `GovernanceDenied` by `ValidationStage.BUDGET`.
- **One caveat:** the entire mint+gate block is guarded by `if self._authority is not None` (execution.py:339). `execution.py:337` calls the else branch *"the legacy ungoverned path"*, where `governance_token_id = run_id` — a bare correlation id. The comment says the composition root always wires an authority; `bootstrap.py:849` confirms it does. So the ungoverned branch is unit-test-only in practice, but it is live code reachable by any caller that constructs the service directly.

### b. Kill switch — ✅ ENFORCED, ALL PATHS

- **Engage/disengage:** `src/skylize/edge/routes/kill_switch.py:26` and `:40`, both `Depends(require_role("owner"))`. Scopes: `agent | department | tenant | platform` (kill_switch.py:17).
- **Enforcement:** `GovernanceAuthority.assert_active` (authority.py:253-257) → `GovernanceSnapshot.reason_for`. Called at mint (authority.py:294), which every governed path traverses. Also re-checked per tool call via `live_state` in `validate_tool_call` (proxy.py:186, execution.py:369, execution.py:884).
- **Survives restart:** `GovernanceAuthority.rehydrate` (authority.py:207-215) reloads active kill scopes, revoked tokens, and suspended agents at boot.
- **Propagates across replicas:** `_broadcast_kill` (authority.py:634) over the Redis channel `skylize:governance:invalidation` (`src/skylize/events/redis_governance_broadcast.py:18`); received at `_apply_invalidation` (authority.py:225).
- **LIVE-VERIFIED.** I engaged `agent/hook_generator_agent` and re-executed: `403 {"detail":"governance denied: agent kill switch engaged","code":"governance_denied"}`.

### c. Circuit breaker — ✅ DEFINED AND WIRED (two independent breakers)

- **Scope-violation breaker:** `authority.py:467-483`. `increment_circuit_breaker(agent_id, org_id)` → suspend at `CIRCUIT_BREAKER_THRESHOLD`. **Single call site:** `src/skylize/app/orchestrator/orchestrator.py:175` — i.e. it fires only on the *workflow* path, not on the synchronous `/agents/execute` path.
- **Convergence breaker:** `authority.py:484-533`. Trips when an agent emits the same `action_hash` twice consecutively within a `correlation_id`. **Wired into the live tool path** at `src/skylize/tools/proxy.py:223-238` — checked *after* token validation and the call-count ceiling, *before* dispatch, and a trip denies the call so no side effect runs.
- Idempotent by construction: ring cleared on trip (authority.py:522), and an already-suspended agent is not re-tripped (authority.py:509-511).

### d. HITL approval queue — ✅ COMPLETE, AND THE RESUME RE-MINTS

- **Defer:** `execution.py:575` (`_enqueue_hitl`) → raise `AgentDeferredToHuman` (execution.py:613) → `202` at `edge/routes/agents.py`.
- **Approve:** `src/skylize/app/hitl/service.py:156`; route `src/skylize/edge/routes/hitl.py:103`.
- **Reject:** `hitl/service.py:295`; route `hitl.py:157`.
- **Does resume re-validate the token?** It does something stronger: **it does not reuse the deferred token at all.** `hitl/service.py:187` calls `self._execution.execute(...)` afresh, which mints a *new* token (execution.py:340 / :844) under current governance state. Three re-checks happen on replay:
  1. **K7 input re-validation** — the stored payload is re-checked against the agent's *current* input schema before the mint and before any LLM spend (`execution.py:281-287`).
  2. **Authority freshness** — `on_behalf_of_principal=envelope.on_behalf_of_principal` is passed (hitl/service.py:198-203) so `mint()` recompiles the human's authority from current grants; a principal offboarded since the defer makes the approval refuse.
  3. **Kill/suspend** — `assert_active` runs inside the new mint.
- **The gate is deliberately skipped on replay** (execution.py:302, `hitl_approval is None`) — re-evaluating would defer forever. Documented at execution.py:296-301.
- **LIVE-VERIFIED end to end.** With `SKYLIZE_DECISION_ENGINE_ORG_IDS='["acme"]'`: execute → `202 {"hitl_id":..., "status":"deferred_to_human","reason":"external_publication: ..."}` → `GET /api/v1/hitl` shows the pending row with `trigger_reason: first_external_launch` → `POST /hitl/{id}/approve` → `200` with a real `deliverable_id`.

### e. Spend ceiling (SpendLedger) — ⚠️ CODE COMPLETE, **ZERO TOOLS ENROLLED**

- **Wired into ToolProxy:** yes. Reserve at `proxy.py:319-324` (→ `_reserve_spend`, proxy.py:723), release on handler exception at `proxy.py:339` and `proxy.py:349`, commit after a successful dispatch at `proxy.py:379-384`.
- **Ordering is correct and reasoned:** the hold is placed LAST, after OAuth (proxy.py:262), permission (proxy.py:288), and WIF (proxy.py:311) gates, "to minimise the window in which budget is held for a call that was never going to run" (proxy.py:315-318).
- **Fails closed with no ledger:** `proxy.py:749` — `if self._spend_ledger is None:` denies rather than running unmetered. `bootstrap.py:798-800` constructs it only on a durable pool: *"an in-RAM money ceiling is not a ceiling."*
- **🔴 THE GAP.** `ToolDefinition.spend` (`src/skylize/tools/base.py:211`) defaults to `None`, and **no tool in `src/skylize/tools/builtin/` sets it**. Verified: `grep -rn "spend=" src/skylize/tools/builtin/*.py` returns only prose comments (gcp_tools.py:27, :156, :177), no assignment. The proxy's `if tool.spend is not None:` (proxy.py:318) is therefore **never true in any deployment**. The reserve/commit/release machinery, the `spend_envelope`/`spend_reservation` tables (migration 0019), and every test around them exercise a path production cannot reach.
- **Second proxy at `runtime/tool_proxy.py`:** still spend-unguarded — confirmed. `RegistryToolProxy` (`src/skylize/runtime/tool_proxy.py:374`) has no `SpendLedger` in its constructor (tool_proxy.py:383-390) and none in `dispatch` (tool_proxy.py:399-500); it accounts only LLM *tokens* via `RunLedger.debit` (tool_proxy.py:485). **However it is not reachable from production:** `bootstrap.py:77` imports `from .tools.proxy import ToolProxy` — the guarded one — and grepping `src/` for `RegistryToolProxy` finds only its own definition and the `runtime/__init__.py:36` re-export. Its only consumers are `tests/unit/test_llm_agent_runner.py:22` and `tests/unit/test_tool_proxy_coverage.py:38`, and the first of those is skipped as dead code (§1). **Severity: BACKLOG (dead code), not a live hole.**
- **GCP auto-hook containment: WIRED BUT DORMANT.** The hook is real — `proxy.py:560-635` schedules `propose_containment` on a spend denial, deliberately not awaited (re-entrancy + latency reasoning at proxy.py:563-585), with strong task references to survive GC (proxy.py:145-149) and a loud done-callback (proxy.py:624-634). Late-bound at `bootstrap.py:886` to break the genuine `gcp_containment → agent_execution → tool_proxy → gcp_containment` cycle. **The durable cooldown IS implemented** — `gcp_containment_claims` (migration 0025) with `try_claim` / `record_hitl_id` / `release` at `src/skylize/app/gcp/trigger.py:240, :280, :316`, replacing what migration 0025's docstring (line 9) says was *"an IN-PROCESS Python dict"*. **But because no tool is spend-capable, `_reserve_spend` never denies, so `_propose_containment` never fires.** The entire GCP kill-switch chain is unreachable end to end.

### f. Audit trail — ✅ EVERY PATH EMITS, INPUTS/OUTPUTS HASHED

- **Single-shot success:** `execution.py:461-476` (`agent.executed`, `result="success"`).
- **Single-shot denial:** `execution.py:374-386` (`governance.budget_exceeded` / `governance.tool_call_denied`, `result="escalated"`).
- **Tool-loop denial:** `execution.py:888-901`. **Tool-loop exhaustion:** `execution.py:943-949` (`governance.tool_loop_exceeded`).
- **HITL replay:** the same `execution.py:461` record, with `causation_id=hitl_approval.original_correlation_id` (execution.py:471-473) so defer → approve → execute forms one traceable chain (K8).
- **Every tool call:** `proxy.py:_audit_call` at 8 sites — unknown tool (proxy.py:177), token denial (proxy.py:191), call-limit (proxy.py:210), convergence (proxy.py:232), input validation (proxy.py:245), handler denial (proxy.py:341), handler error (proxy.py:351), success (proxy.py:365).
- **Ordering discipline is deliberate and correct:** on success the audit is written *before* the ledger commit (`proxy.py:359-363`), because "a ledger failure must not be able to erase" the evidence that a real-world action ran.
- **Hashing:** `src/skylize/app/audit/service.py:28-33` — `hashlib.sha256` over the canonical encoding; stored as `inputs_hash` / `outputs_hash` (audit/service.py:59-60, :72-73). Module docstring line 10: *"PII never enters the audit."* **Plaintext inputs are NOT stored in the audit log.**
- ⚠️ Plaintext input *is* stored elsewhere: `execution.py:445` writes `metadata={"input": input_data, ...}` onto the deliverable row. That is a product decision (the deliverable shows what it was asked for), not an audit leak, but it is where a customer's raw prompt actually lives.

### g. Content gate (GuardedLLMGateway) — ✅ WRAPS THE SHARED GATEWAY, COVERS `tool_result`

- **Wired at the composition root:** `bootstrap.py:777` — `llm = GuardedLLMGateway(llm, gate=content_gate)`, after both the Anthropic (bootstrap.py:762) and demo (bootstrap.py:764) branches. Because it replaces the single shared `llm` reference, every downstream holder — `AgentExecutionService`, `LLMStepRunner`, `ToolProxy.dispatch_llm` — is gated with no per-call-site change (bootstrap.py:773-776).
- **Covers `tool_result` blocks:** yes, explicitly. `src/skylize/adapters/llm/content_gate.py:135-139` screens `block.tool_output` with the comment *"Tool output is the classic indirect-injection vector (web/MCP content re-entering the model's context)"*. Also covers `system` and every `text` block (content_gate.py:128-134).
- Scope note: `LLMContentGate` is a signal-matcher (content_gate.py:49-102) with a small signal set — e.g. `"system_prompt_exfiltration"` (content_gate.py:65). It is a tripwire, not a classifier. 144 lines total.

---

## SECTION 4 — TENANT ISOLATION AUDIT

### a–b. Tables with `org_id` + RLS

**26 migrations**, `migrations/versions/0001…0026`. **31 tables carry `tenant_isolation` RLS with `ENABLE` + `FORCE ROW LEVEL SECURITY`:**

| Migration | Tables |
|---|---|
| 0001:347-367 (+ 0002:40-64 rehydrate carve-out) | `governance_tokens`, `agent_live_state`, `kill_switch_state`, `budget_ledger`, `decisions`, `hitl_queue`, `memory_records`, `kg_nodes`, `kg_edges`, `audit_log`, `tenant_integrations` |
| 0006:74-79 | `deliverables` |
| 0007:59-65 | `org_credentials` |
| 0009:84-93 | `decision_outbox` |
| 0010:88-92 | `workflow_run_steps` |
| 0011:63-67 | `decision_processed_events` |
| 0012:178-182 | `ai_cost_ledger` |
| 0014:99-103 | `org_spend_ceiling` |
| 0019:241-249 | `principal`, `principal_grant`, `spend_envelope`, `spend_reservation`, `work_journal`, `journal_cursor` |
| 0021:128-133 | `oauth_credentials` |
| 0022:113-117 | `org_permission_grants` |
| 0024:239-242 | `gcp_wif_connections`, `gcp_wif_targets` |
| 0025:117-120 | `gcp_containment_claims` |
| 0026:245-248 | `github_app_installations`, `github_app_repos` |

Policy shape is uniform: `USING (org_id = current_setting('skylize.org_id', true)) WITH CHECK (same)`. The one deliberate widening is migration 0002's rehydrate carve-out (`OR current_setting('skylize.rehydrate', true) = 'on'`, 0002:56-58) on `USING` only — `WITH CHECK` stays strict, so rehydration can read cross-tenant but never write cross-tenant.

**The role is right.** Migration 0003 creates `skylize_app` as `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE` (0003:60) precisely because, as its own docstring says (0003:7-13), the Sprint-1 claim that FORCE RLS could not be bypassed *"is FALSE if the application connects as a Postgres SUPERUSER"*. Session scoping is `SELECT set_config('skylize.org_id', $1, true)` inside `Database.tenant_session` (`src/skylize/dal/connection.py:79`).

### c. Tables WITHOUT tenant isolation — 6, all documented carve-outs

| Table | Has `org_id`? | Why no RLS | Risk |
|---|---|---|---|
| `tenants` | — (it *is* the org table) | platform table; 0003:43 `_PLATFORM_TABLES` | none |
| `tenant_users` | yes | platform table; 0003:43 | **MEDIUM** — org↔user mapping is readable cross-tenant by the app role |
| `agent_contracts` | yes | platform table; 0003:43 | LOW — contract definitions are not customer data |
| `api_keys` | yes (`REFERENCES tenants`) | 0004:57-59: *"the auth-time lookup is cross-tenant by necessity … isolation for management is enforced by explicit org_id filters"* | **MEDIUM** — one missing `WHERE org_id` in key management leaks cross-tenant. Mitigated by `key_hash` storage and the `idx_api_keys_active` partial index on live keys only (0004:57) |
| `users` | yes (`REFERENCES tenants`) | 0008:65: *"no RLS — auth layer needs cross-tenant email lookup"* | **MEDIUM** — same class. `users_email_unique` is global (0008:33), so email uniqueness is platform-wide, which is itself a weak cross-tenant existence oracle |
| `user_refresh_tokens` | **no `org_id` at all** (0008:32-40) | keyed only by `user_id` | LOW — org is reachable only via the `users` join; nothing org-scoped queries it |
| `model_pricing` | no | platform reference data (0012, 0013, 0018 seeds) | none |

**Assessment:** the carve-outs are deliberate, each documented at its migration, and each is a genuine bootstrap necessity (you cannot RLS-scope the lookup that *determines* the scope). The residual risk is uniform: **three tables now depend on hand-written `WHERE org_id = $1` clauses instead of the database.** That is exactly the class of defect migration 0003 was written to eliminate everywhere else.

### d. Shared non-tenant-scoped in-process state

| Component | Key | `org_id` in key? | Assessment |
|---|---|---|---|
| `ConvergenceTracker._rings` | `(correlation_id, agent_id)` — `authority.py:100` | **NO** | Not exploitable: `correlation_id` is a fresh `uuid4()` per request (`execution.py:301`), so two orgs cannot collide. But the *effect* of a trip is org-scoped correctly — `increment_circuit_breaker(agent_id, org_id)` (authority.py:474) and `reason_for(None, agent_id, org_id)` (authority.py:509). **LOW.** |
| `ToolCallCounter._counts` | `(correlation_id, agent_id, tool_id)` — `proxy.py:94` | **NO** | Same reasoning, same verdict. The docstring (proxy.py:85-91) states the keying is intentional and mirrors `ConvergenceTracker`. Note it also warns the counter *"accumulates for the life of the process"* — an **unbounded-memory leak** in a long-lived process, since nothing evicts finished correlation ids. **LOW security / MEDIUM operational.** |
| `RunLedger` Redis key | `f"runledger:{correlation_id}:{agent_id}"` — `runtime/run_ledger.py:162` | **NO** | Same uuid4 reasoning. Also: this ledger is only reachable through `RegistryToolProxy`, which is dead code (§3e). **LOW.** |
| Qdrant collection | single shared `platform_knowledge` | **Enforced at the query layer** | `src/skylize/memory/org_scope.py:36-43` (`require_org` — raises rather than widening) and `:46-59` (`scoped_filters` — extra filters may only NARROW; restating `org_id` is refused). There is no store-level RLS equivalent, so this is the only barrier. **MEDIUM** — correctness depends on every call site routing through `scoped_filters`. Wired only when both `qdrant_url` and `openai_api_key` are set (`bootstrap.py:578`). |
| Postgres connection pool | `min_size=1, max_size=10` — `dal/connection.py:56` | **No per-org cap** | One tenant can exhaust all 10 connections. **MEDIUM operational (noisy neighbour), not an isolation defect.** |

### e. Redis key namespace

Only three key families exist in `src/`: `skylize:governance:invalidation` (pub/sub channel, `events/redis_governance_broadcast.py:18`), `skylize:revoked:{token_id}` (`runtime/tool_proxy.py:203`), `runledger:{correlation_id}:{agent_id}` (`run_ledger.py:162`). All three are keyed by a UUID or are global-by-design. **No cross-tenant collision surface found.** The rate limiter is in-process, not Redis (`edge/rate_limit.py:16`, per the note at `edge/routes/agents.py:63-65`), which means **the effective rate limit is the configured value × replica count** — an operational, not an isolation, concern.

---

## SECTION 5 — INTEGRATION & CONNECTOR STATUS

**The finding that dominates this section: there is NO OAuth authorization-code flow anywhere in the repository.** `OAuthCredentialService` (`src/skylize/app/credentials/oauth.py:304`) exposes `register_provider`, `ensure_fresh`, `access_token`, `mark_revoked_by_provider`, `_refresh` — **it can only refresh a grant that already exists.** There is no `/oauth/authorize`, no `/oauth/callback`, no state/PKCE handling in `src/skylize/edge/routes/` (verified: `grep -rn "authorize\|callback\|/oauth" src/skylize/edge` returns only unrelated prose). The only caller of `OAuthCredentialRepository.insert` (`src/skylize/dal/oauth_credentials.py:136`) anywhere in `src/` is **none** — it is called exclusively from `tests/`. **A row in `oauth_credentials` can today only be created by a hand-written SQL INSERT.**

| # | Connector | Design doc | Broker/provider config | Connector code that calls the API | Can it do real work today? |
|---|---|---|---|---|---|
| a | **Slack** §2.3 | `[APPROVED]` 2026-08-28 (integration_inputs.md:328) | N/A — platform-level, not OAuth. `bootstrap.py:158` `resolve_slack_notifier_config` | ✅ `src/skylize/app/notifications/slack.py` — real `httpx` POST to `chat.postMessage` | **PARTIALLY YES.** With `SKYLIZE_SLACK_BOT_TOKEN` + `SKYLIZE_SLACK_APPROVAL_CHANNEL_ID` it posts HITL approval requests to **Skylize's own** workspace. **Post-only. No Interactivity/button handling** (slack.py:9-11) and **no tenant Slack integration** — a customer cannot connect their workspace. Not in the tool registry: an agent cannot send a Slack message. |
| b | **Stripe** §2.1 | `[OWNER-DECISION-REQUIRED]` (integration_inputs.md:209) | **ABSENT** | **ABSENT** | **NO.** Zero implementation. Every `stripe` hit in `src/` is a prose comment (`bootstrap.py:884`, `app/principal/models.py:56,74`, `app/credentials/asana_provider.py:50`). No client, no config field, no tool. |
| c | **Google Drive** §2.5 | `[APPROVED]` 2026-08-31 (integration_inputs.md:603) | ✅ `build_google_drive_provider_config` registered at `bootstrap.py:615` | ✅ `src/skylize/tools/builtin/drive_tools.py` — `drive.create_file`, `drive.share_file`, registered at `tools/builtin/__init__.py:53-54` | **NO — blocked by the missing OAuth flow.** The tools exist and are gated (`ToolOAuthProfile` → `_ensure_oauth_credential`, proxy.py:262), but no customer can produce the grant they need. No list/read verbs — create + share only. |
| d | **Asana** §2.6 | `[APPROVED]` 2026-09-03 (integration_inputs.md:726) | ✅ `bootstrap.py:625` | ✅ `asana_tools.py` — `create_task`, `create_project`, `add_project_member` (`tools/builtin/__init__.py:57-59`) | **NO — same blocker.** Create verbs only; no list/read. |
| e | **Notion** §2.7 | `[APPROVED]` 2026-09-04 (integration_inputs.md:970) | ✅ `bootstrap.py:635` | ✅ `notion_tools.py` — `create_page`, `create_database`, `append_blocks` (`tools/builtin/__init__.py:61-63`) | **NO — same blocker.** Create/append only; **no query verb.** |
| f | **GitHub** §2.4 | `[APPROVED]` 2026-09-05 (integration_inputs.md:412) | Platform key custody: `app/github/keys.py:135`, `bootstrap.py:60` | Foundation only: `app/github/tokens.py` (RS256 App JWT → installation token, tokens.py:207), `app/github/probe.py:180` (branch-protection probe), `dal/github_app.py`, migration 0026 | **NO.** `src/skylize/app/github/__init__.py:9-17` states it plainly: *"NO webhook ingress (2.4 Q2.4e) and NO PR-merge verb… **NO TOOL REGISTRATION. Nothing here is reachable by an agent yet.**"* Verified: `grep -rn "github" src/skylize/tools/` → zero hits. Cannot read a repo or open a PR. |
| g | **GCP kill-switch** | §2.2 `[OWNER-DECISION-REQUIRED]` — **HARD BLOCK** (integration_inputs.md:284) | ✅ WIF, not OAuth: `dal/gcp_wif.py`, migration 0024, OIDC issuer routes at `edge/routes/wif_oidc.py:95,107` | ✅ `tools/builtin/gcp_tools.py:152` — `integration.gcp_stop_instance` with `ToolWifProfile` (gcp_tools.py:169); trigger `app/gcp/trigger.py`; durable cooldown migration 0025 | **DORMANT.** The chain is complete and integration-tested (`test_gcp_killswitch_e2e_pg.py`, `test_containment_autohook_pg.py`) but **unreachable**: the auto-hook fires only on a spend denial, and no tool is spend-capable (§3e). Registers only when `wif_repo` AND `gcp_executor_factory` are both wired (`tools/builtin/__init__.py:69-71`), so a non-GCP deployment has no such tool at all. |
| h | **AWS** | — | **ABSENT** | **ABSENT** | **NO.** `grep -rn "\baws\b\|boto3" src/skylize` → zero hits. AWS appears only as the *deployment target* (ECS/ECR/ALB in `.github/workflows/deploy-staging.yml`), never as a governed connector. |

### Design-doc vs code, stated plainly

- **Approved design + real connector code, blocked only by the missing OAuth flow: 3** (Drive, Asana, Notion).
- **Approved design + foundation code, deliberately no tools: 1** (GitHub).
- **Approved design + working code, different mechanism: 1** (Slack, platform-level post-only).
- **Complete code, unreachable at runtime: 1** (GCP).
- **Nothing at all: 2** (Stripe, AWS).

**Severity: BLOCKS_PILOT.** Zero connectors can do real customer work today.

---

## SECTION 6 — API SURFACE COMPLETENESS

**17 route modules**, `src/skylize/edge/routes/`. Auth vocabulary: **JWT** = Skylize HS256 access token; **APIKey** = `X-API-Key` / `Authorization: ApiKey`; **Dev** = `X-Dev-*` headers (only when `dev_auth`, which `config.py:365-372` forbids on a non-memory backend); **OIDC** = production JWKS path (`edge/auth.py:52-76`).

| Method + Path | Auth | Returns | Tests |
|---|---|---|---|
| `POST /api/v1/auth/register` | **none** (rate-limited, `auth.py:89`) | `UserResponse` 201 | unit ✓, integ ✓ (`test_registration_org_claim_pg.py`) |
| `POST /api/v1/auth/login` | none | access+refresh pair | unit ✓ |
| `POST /api/v1/auth/refresh` | refresh token | new pair | unit ✓ |
| `GET /api/v1/auth/me` | JWT (`auth.py:183`) | `UserResponse` | unit ✓ |
| `POST /api/v1/agents/execute` | JWT/APIKey/Dev, roles owner\|admin\|operator (`agents.py:68`); rate-limited (`agents.py:65`) | 201 deliverable, **202 deferred**, 403 rejected/denied | unit ✓✓, integ ✓✓ |
| `GET /api/v1/agents` | ctx-or-user (`agents.py:214`) | agent list + JSON Schemas (`execution.py:987`) | unit ✓ |
| `GET /api/v1/agent-prompts/{agent_id}` | API key (`agent_prompts.py:19`) | `AgentPromptResponse` | `tests/edge/test_agent_prompts.py` ✓ |
| `POST/GET/DELETE /api/v1/api-keys[/{key_id}]` | ctx (`api_keys.py:53,72,87`) | issued key / list / 204 | unit ✓, integ ✓ |
| `GET /api/v1/audit` | ctx (`audit.py:44`) | `AuditListResponse` | unit ✓, **integ ✗** |
| `GET /api/v1/me/brief` · `POST /api/v1/me/brief/seen` | ctx (`brief.py:75,123`) | `BriefResponse` / 204 | unit ✓, integ ✓ (`test_brief_endpoint.py`) |
| `POST /api/v1/cowork` | ctx (`cowork.py:67`) | `CoworkTurnOut` | **unit ✗**, integ ✓ |
| `POST/GET/DELETE /api/v1/credentials[/{id}]`, `GET /credentials/resolve` | ctx (`credentials.py:59,76,85,114`); resolve separately rate-limited (`config.py:238`) | credential summaries | unit ✓, **integ ✗** |
| `POST/GET /api/v1/deliverables`, `GET /{id}`, `PATCH /{id}/approve\|revise\|archive`, `GET /{id}/versions`, `GET /{id}/download` | roles incl. viewer (`deliverables.py:120`) | deliverable list/detail/file | unit ✓, integ ✓ |
| `GET /api/v1/hitl`, `POST /{id}/approve`, `POST /{id}/reject` | ctx (`hitl.py:82,103,157`) | queue / verdict + deliverable | **unit ✗**, integ ✓✓ |
| `POST /api/v1/kill-switch/engage\|disengage` | **role `owner` only** (`kill_switch.py:29,43`) | `{status, scope_type, scope_id}` | **unit ✗**, integ ✓ |
| `POST /knowledge/ingest` (**HMAC**, `knowledge.py:97`), `/upload`, `/interview`, `GET /search` | HMAC / ctx | ingestion + search | unit ✓, integ ✓ |
| `GET /api/v1/spend/position` | ctx (`spend.py:56`) | `SpendPositionResponse` | **unit ✗**, integ ✓ |
| `POST /api/v1/tenants`, `GET /me`, `POST /me/suspend\|reactivate`, `GET /me/users`, `PUT/DELETE /me/users/{id}` | ctx (`tenants.py:46…`) | tenant + user rows | **unit ✗**, integ ✓ |
| `GET /{prefix}/{slug}/.well-known/openid-configuration`, `GET /{slug}/jwks.json` | **public by design** (OIDC discovery, `wif_oidc.py:95,107`) | JSON + cache headers | integ ✓ (`test_wif_oidc_endpoints.py`) |
| `POST /api/v1/workflows/creative` | ctx + rate limit (`workflows.py:45`) | `WorkflowResponse` | **unit ✗**, integ ✓ |

### Flags

**Routes with NO unit-test coverage (integration only): 6** — `cowork`, `hitl`, `kill_switch`, `spend`, `tenants`, `workflows`. Since integration tests self-skip without service env vars, **these six routes are untested in CI's `unit` job and in every local `pytest` run without a database.** The kill switch and HITL approval are the two most safety-critical routes in the product.

**Routes with no integration coverage: 2** — `audit`, `credentials`.

**Documented-but-absent routes.** `docs/11_product/feature_roadmap.md` contains **no route-level specification** at all (`grep` for `GET |POST |/api/v1` returns nothing), so the prompt's "routes in feature_roadmap with no implementation" check cannot be run against it. The genuine gaps, derived from `docs/11_product/mvp_definition.md` §3 instead:

- **No OAuth connect/callback route** — §5 above. The single biggest hole in the surface.
- **No GitHub webhook ingress** — `app/github/__init__.py:14`, needed for uninstall detection per §2.4 Q2.4e.
- **No user-invite route.** `edge/routes/auth.py:104-108` states it outright: *"there is now NO way to add a second user to an organisation over HTTP… a governed invite flow, tracked as PILOT BAR item E20."*
- **`POST /api/v1/workflows/creative` is not a crew.** `workflows.py:57-59` invokes exactly one agent, `hook_generator_agent`. `mvp_definition.md` §3 promises a *"Creative crew: VP-Creative org: copy/art/video/brand/creative-ops teams produce assets."* The multi-agent creative pipeline **does not exist**. (LangGraph *is* real and wired — `app/orchestrator/workflows/creative_workflow.py:20-21` builds a `StateGraph`, compiled at `orchestrator.py:54` — but the graph runs a single agent step.)

---

## SECTION 7 — CONSOLE (FRONTEND) STATUS

**5 pages total.** `find website/src/app -name "page.tsx"`.

| Page | Kind | State |
|---|---|---|
| `(site)/page.tsx` | marketing home | functional |
| `(site)/console-preview/page.tsx` (174 ln) | marketing | **placeholder by design** — its own docstring (line 25): *"the captures themselves are pending"* |
| `(site)/my-day/page.tsx` (209 ln) | marketing | **placeholder by design** — line 28: *"Captures are pending; every frame still carries its sample-data caption"* |
| `console/login/page.tsx` (107 ln) | app | functional |
| `console/page.tsx` (160 ln) | app | functional — the entire operator console is this one page |

**12 BFF routes** under `website/src/app/api/console/`: `session`, `health`, `tenant`, `agents`, `agents/execute`, `deliverables/[id]`, `hitl`, `hitl/[id]/approve`, `hitl/[id]/reject`, `kill-switch`, `workflows`, `workflows/creative`.

| Flow | Works? | Evidence |
|---|---|---|
| **c. Login end-to-end** | ⚠️ **Yes, but it is not real auth.** | `api/console/session/route.ts:4-6`: *"INTERIM gate: one shared password (`SKYLIZE_CONSOLE_PASSWORD`) stands in for the deferred OIDC epic."* Constant-time compare (session/route.ts:31) → httpOnly cookie. **One shared password for the whole console — no users, no roles, no per-org identity.** The backend's real JWT auth (`auth.py:145`) is not what the console uses. |
| **d. Agent execution from console** | ✅ Yes | `AgentRunner` (agent-runner.tsx, 628 ln) fetches `/api/console/agents`, defaults to `hook_generator_agent` (agent-runner.tsx:359), POSTs to `/api/console/agents/execute` (agent-runner.tsx:410), renders the JSON-Schema-driven form. Mounted via `GovernanceDemo` → `console/page.tsx:147`. |
| **e. HITL approve/reject from console** | ✅ Yes | `HitlPanel` (336 ln), mounted at `governance-demo.tsx:36`; on approval the returned deliverable flows to `DeliverableCard` with `provenance="human_approval"` (governance-demo.tsx:38-40). BFF routes exist for both verdicts. |
| **f. Kill switch from console** | ✅ Yes | `KillSwitchPanel` (246 ln), mounted at `console/page.tsx:152`; BFF `api/console/kill-switch/route.ts`. |
| **g. Deliverable list/detail** | ⚠️ **Detail only, no list.** | `DeliverableCard` (71 ln) shows the *one* deliverable just produced (governance-demo.tsx:41-47). There is **no BFF route for `GET /api/v1/deliverables`** — only `deliverables/[id]`. **A customer cannot browse their own past work in the console.** |

### Pages that exist in the spec sitemap but have no implementation

**`Skylize_Design_Specification.md` does not exist in this repository.** `find . -name "Skylize_Design_Specification.md"` → no match. The prompt's §1.2 sitemap reference **cannot be checked** — marked **ABSENT**. The nearest artifacts are `website/src/app/sitemap.ts` (6 entries, all marketing) and `docs/11_product/mvp_definition.md`.

Measuring against `mvp_definition.md` §3 instead, the console has **no page at all** for:

- **Audit log** — `GET /api/v1/audit` exists and is unit-tested; there is no BFF route and no UI. The product's core promise, *"every action explainable"* (`mvp_definition.md` §2), is invisible to the customer.
- **Spend / budget ceilings** — `GET /api/v1/spend/position` exists; no BFF route, no UI. `mvp_definition.md` §3 lists *"sets budget ceilings"* as in-scope.
- **Integrations / connect** — nothing, consistent with §5 (there is no flow to surface).
- **Deliverable history** — see (g).
- **User / role management** — `tenants.py:105-134` exposes the API; no BFF route, no UI.
- **Agent org chart** — `agent-network.data.ts` and `agent-network.types.ts` exist in `components/console/` and are **imported by nothing** (`grep` for `AgentNetwork` outside that directory → zero hits).

**Severity: BLOCKS_DEMO** for audit + spend + deliverable-history (they are the proof-points an investor asks to see). **BLOCKS_PILOT** for the shared-password login.

---

## SECTION 8 — DEPLOYMENT READINESS

**a. Dockerfile — exists, and its history is a warning.** `/Dockerfile`. Multi-stage (builder → runtime), non-root `skylize` uid 1001, `HEALTHCHECK curl -f localhost:8000/health`, `CMD alembic upgrade head && uvicorn skylize.edge.gateway:app`. Its own comments record two shipped-broken states: `COPY src ./src` was missing until 2026-07-31, meaning *"the image CI builds had never built at all"* (Dockerfile:24-29), and `curl` was absent, so *"an image without this layer fails its own health check forever"* (Dockerfile:47-51). **Not built in this audit** — Docker was not exercised.

**b. Docker Compose — exists, 5 services.** `infra/docker-compose.yml`: `postgres`, `redis`, `opa`, `migrate`, `gateway`. **Missing from compose: Qdrant** (`bootstrap.py:578` needs `qdrant_url`) and any frontend service. Note `opa` is present even though `bootstrap.py:685-690` refuses to boot with `SKYLIZE_DECISION_ENGINE != "inline"`.

**c. Railway config — ABSENT.** No `railway.json`, `railway.toml`, `nixpacks.toml`, or `Procfile`. **The deploy target is AWS ECS, not Railway:** `.github/workflows/deploy-staging.yml` pushes to ECR and deploys to `skylize-staging-api`, with Terraform modules at `infra/terraform/staging/modules/{vpc,rds,elasticache,ecs,ecr,alb,iam,secrets}`.

> **ARCHITECTURE CONFLICT — reported, not resolved.** The frontend BFF is written against Railway: `website/src/lib/skylize/config.ts:14` — *"Railway backend origin, no trailing slash"*, and `website/src/app/console/page.tsx:26-28` — *"the Railway URL and service key never appear here."* The backend deploys to AWS ECS behind an ALB (`deploy-staging.yml`, `infra/terraform/staging/`). Two sources disagree on where the API lives.

**d. Env vars with no default that must be set.** From `src/skylize/config.py`:

| Var | Field | Fails closed? |
|---|---|---|
| `SKYLIZE_ANTHROPIC_API_KEY` | `anthropic_api_key: str = ""` (:287) | ✅ `bootstrap.py:766` raises `LLMConfigurationError` unless `SKYLIZE_LLM_DEMO_MODE=true`. **Explicitly refuses a silent demo fallback.** |
| `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY` | `credential_encryption_key: str = ""` (:121) | ✅ boots closed on `backend != "memory"` (config.py:112-119). Losing it loses every stored credential. |
| `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` | `governance_signing_key_pem: str = ""` (:86) | ✅ required on a real backend (config.py:84-85) |
| `SKYLIZE_JWT_SECRET` | `jwt_secret: str = ""` (:109) | ✅ config.py:356-360 — required when `dev_auth` is off |
| `SKYLIZE_APP_DB_PASSWORD` | `app_db_password: str = ""` (:67) | ⚠️ migration 0003:49-50 creates the role **with no password** if unset — the app DSN then cannot authenticate |
| `SKYLIZE_DB_URL` / `SKYLIZE_DB_APP_URL` | `:46` has a **localhost default**; `:47` is `""` | ⚠️ `db_url` defaulting to `postgresql://skylize:localdev@localhost:5432/skylize` is a footgun in a container |
| `SKYLIZE_REDIS_URL` | `redis_url = "redis://localhost:6379"` (:48) | ⚠️ same |
| `SKYLIZE_OPENAI_API_KEY` | `openai_api_key: str = ""` (:299) | ⚠️ **silent** — embeddings/Qdrant simply never wire (`bootstrap.py:578`). No error, no warning. Memory is dead and nothing says so. |
| `SKYLIZE_SEARCH_API_KEY` | `search_api_key: str = ""` (:281) | ⚠️ **silent** — `NullWebSearchPort` (`tools/builtin/__init__.py:44`). `seo_keyword_agent` "searches" nothing. |
| `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET` | `:284` | ✅ 503 rather than unverified ingest (`knowledge.py:141-143`) |
| `SKYLIZE_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` | `:300-301` | 🔴 **read but never used — see (g)** |
| `SKYLIZE_CORS_ORIGINS` | `[]` (:103) | ✅ middleware not installed when empty; comment forbids `"*"` |
| `SKYLIZE_DECISION_ENGINE_ORG_IDS` | `[]` (:266) | 🔴 **the governance gate is OFF by default — see §9e** |

**Notable non-gap:** `dev_auth: bool = True` (config.py:92) looks like a total auth bypass (`edge/auth.py:39-50` trusts `X-Dev-Org` and grants `owner` by default). It is **guarded**: `config.py:365-372` `_forbid_dev_auth_on_a_real_backend` fails boot when `dev_auth and backend != "memory"`. Correctly closed.

**e. Migrations on an empty DB.** Alembic head is `0026`, linear `0001…0026`. CI's `integration` job runs `alembic upgrade head` against a fresh `postgres:16-alpine` before every integration run (ci.yml:100-104, and again in `deploy-staging.yml`), so a clean apply **is** continuously proven — by CI. **Not verified locally in this audit** (would require creating a database; out of scope for a read-only pass).

**f. Has the app ever been deployed?** **Evidence says the deploy pipeline has never completed green.** `.github/workflows/deploy-staging.yml:156-166`, in the maintainer's own words:

> *"This job used to also tag and push `:latest`. It sits behind `needs: integration-test`, which now passes, so `docker push :latest` ran and succeeded while the `deploy` job that follows **still failed** — publishing a container that cannot boot… **RESTORE `:latest` ONLY AFTER a deploy job has completed green end to end**, i.e. the ECS service reached steady state and the /health smoke test returned 200."*

`:latest` is still not restored at HEAD. **Conclusion: no green end-to-end deploy has occurred. There is no running staging environment.**

**g. Observability — 🔴 DORMANT.** `AnthropicAdapter.__init__` accepts `langfuse_client` (`adapters/llm/anthropic_adapter.py:226`) and `_record_langfuse` (anthropic_adapter.py:713) is called on every generation (anthropic_adapter.py:803). **`bootstrap.py:748-752` constructs the adapter with `settings`, `cost_ledger`, `spend_ceiling` — and no `langfuse_client`.** `grep -n "Langfuse" src/skylize/bootstrap.py` → zero hits. The client defaults to `None` and `_record_langfuse` returns immediately (anthropic_adapter.py:717). **`SKYLIZE_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` are read into `Settings` and never used.** The OTel spans in `decision_engine/pipeline.py:143-148` live in the OPA worker package, which `bootstrap.py:685` refuses to run. **There are no traces, from anywhere.**

---

## SECTION 9 — BLOCKING DECISIONS & UNRESOLVED ADRs

**a. `docs/04_decision_engine/policy_inputs.md` — still DRAFT. Zero `[APPROVED]` sections.**
Banner, line 3: *"Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)"*. Line 20: *"Faz 2 (Rego) is BLOCKED until each section reaches `[APPROVED]`."*

| § | Title | Status |
|---|---|---|
| 0.1 | Authority / DoA / HITL matrix | `[OWNER-DECISION-REQUIRED]` (:48) |
| 0.2 | Spend ceilings | `[OWNER-DECISION-REQUIRED]` (:125) |
| 0.3 | External action / first-time detection | `[OWNER-DECISION-REQUIRED]` (:164) |
| 0.4 | Brand / legal classification | `[OWNER-DECISION-REQUIRED]` (:215) |
| 0.5 | Security veto | `[OWNER-DECISION-REQUIRED]` + *"THIS CLASS IS NOT YET IMPLEMENTED IN CODE"* (:252) |
| 0.6 | Data access | `[CODE-VERIFIED]` (:297) — a fact record, not an approval |
| 0.7 | Policy version schema | `[OWNER-DECISION-REQUIRED]` (:350) |

**6 of 7 sections await the owner. Every real policy threshold in this product is still a placeholder.**

**b. ADR-0004 (OPA vs inline) — decided in principle, closed in code.**
`bootstrap.py:685-690` raises on any `SKYLIZE_DECISION_ENGINE` value other than `"inline"`, with the message *"…is not production-ready."* `config.py:260` types the field `Literal["inline", "opa"]` but `"opa"` is unreachable. `infra/docker-compose.yml` still starts an `opa` service that nothing can talk to. **This is fail-closed and correct** — but it means the ADR-0004 *"designated production arbiter"* is not the one running, and cannot be until §a's policies exist as Rego.

**c. `docs/06_integrations/integration_inputs.md` — 5 approved, 5 outstanding.** Banner line 3: *"Status: DRAFT - AWAITING OWNER APPROVAL"*; line 21: *"Connector implementation is BLOCKED until the relevant section reads `[APPROVED]`."*

| § | Provider | Status |
|---|---|---|
| 1.1 | **Agent external actions face no money ceiling** | `[OWNER-DECISION-REQUIRED]` — *"BLOCKING FINDING… must be resolved BEFORE any"* (:108) |
| 2.0 | Provider classification | `[OWNER-DECISION-REQUIRED]` (:181) |
| 2.1 | Stripe | `[OWNER-DECISION-REQUIRED]` (:209) |
| 2.2 | AWS / GCP | `[OWNER-DECISION-REQUIRED]` — **"HARD BLOCK"** (:284) |
| 2.3 | Slack | ✅ `[APPROVED]` 2026-08-28 (:328) |
| 2.4 | GitHub | ✅ `[APPROVED]` 2026-09-05 (:412) |
| 2.5 | Google Drive | ✅ `[APPROVED]` 2026-08-31 (:603) |
| 2.6 | Asana | ✅ `[APPROVED]` 2026-09-03 (:726) |
| 2.7 | Notion | ✅ `[APPROVED]` 2026-09-04 (:970) |
| 3.0 | `org_credentials` OAuth schema gaps | `[OWNER-DECISION-REQUIRED]` — *"recorded, deliberately not implemented"* (:1219) |

§1.1 and §2.2 are the load-bearing ones: §1.1 is the money ceiling that §3e shows is dormant; §2.2 is the hard block over the GCP work that is already built.

**d. Other unsigned documents gating implementation.** Both of the above are the gates. `docs/11_product/mvp_definition.md:3` is marked *"source of truth for MVP scope"* with no approval banner, so it is not a blocker — but it is also the document the code currently diverges from most (§6, §7).

**e. Owner decisions that block forward progress**

| # | Decision | Blocks | Evidence |
|---|---|---|---|
| 1 | **policy_inputs §0.1–0.5, 0.7** | ADR-0004's OPA arbiter; every real HITL threshold; §0.5's security-veto class is *not implemented at all* | policy_inputs.md:48,125,164,215,252,350 |
| 2 | **integration_inputs §1.1** — money ceiling for agent external actions | Assigning `ToolSpendProfile` to any tool; therefore the whole SpendLedger and the GCP auto-hook (§3e) | integration_inputs.md:108 |
| 3 | **integration_inputs §2.2** — AWS/GCP, "HARD BLOCK" | Shipping the GCP kill-switch that is already written and tested | integration_inputs.md:284 |
| 4 | **integration_inputs §2.1** — Stripe | Any billing or revenue connector | integration_inputs.md:209 |
| 5 | **integration_inputs §3.0** — `org_credentials` OAuth schema | The OAuth connect flow (§5) | integration_inputs.md:1219 |
| 6 | **Console identity** — OIDC epic deferred | Replacing the shared console password | `website/src/app/api/console/session/route.ts:4-6` |
| 7 | **Governed-invite flow ("E20")** | A second user in any org | `edge/routes/auth.py:104-108` |

⚠️ **Note on §2.6 and §2.7:** integration_inputs.md:737-741 and :982 record that Asana and Notion connector code was **written before** those sections reached `[APPROVED]`, in acknowledged deviation from the doc's own §4.0 precondition. The deviation is disclosed in the document rather than hidden — recorded here as a process fact, not a defect.

---

## SECTION 10 — THE TWO BARS

### DEMO BAR — what an investor can be shown today, zero external API keys

**Setup:** `SKYLIZE_LLM_DEMO_MODE=true`, memory backend. Every flow below was **executed live during this audit**, not inferred.

#### ✅ Works end to end

| Flow | Live result |
|---|---|
| **Register → execute → deliverable → read back → list** | `register 201` → `execute 201` → `GET /deliverables/{id} 200` with rendered markdown → `GET /deliverables 200, total=2` |
| **The governance story (the money shot)** | With `SKYLIZE_DECISION_ENGINE_ORG_IDS='["acme"]'`: `202 {"status":"deferred_to_human","reason":"external_publication: deliverable destined for external publication defers to human"}` → `GET /hitl` shows the pending row (`trigger_reason: first_external_launch`) → `POST /hitl/{id}/approve 200` returning a real `deliverable_id`. **Defer → human → execute, with the replay re-minting under current authority.** |
| **Kill switch** | `POST /kill-switch/engage 200`, then the same execute → `403 {"detail":"governance denied: agent kill switch engaged","code":"governance_denied"}` |
| **Tool-loop agent** | `cfo_agent` → `201`, title *"Budget Summary — marketing 2026-Q3"*, with `total`/`flags` recomputed deterministically in Python rather than trusted to the model (`execution.py:435-437`) |
| **Audit + HITL list endpoints** | both `200` |
| **Console** | login → agent run → HITL approve → deliverable card, all three panels mounted (`console/page.tsx:147-153`) |

**Demoable agents: 8 of 23 (35%)** — the 8 with a `_DEMO_RESPONSES` entry. Attempting any of the other 15 raises `DemoResponseUnavailable` (`demo_adapter.py:114-133`), which helpfully names the 9 keys that do exist.

#### ✗ Fails, or needs narration

| Flow | Why |
|---|---|
| **`scripts/e2e_deliverable.py` — the repo's own happy-path demo script — IS BROKEN** | Ran it: `[2/4] EXECUTE FAILED: 422`. It posts `{brief_id, product, audience}` (e2e_deliverable.py:56-59) but `HookGeneratorExecuteIn` requires `{brand_name, product_description, target_audience}` (`src/skylize/schemas/agents/creative.py:86-91`). **Schema drift; nothing tests this script.** The flow itself is fine — I re-ran it with a corrected payload and it passed. **Highest-embarrassment, lowest-cost fix in this document.** |
| **15 of 23 agents** | no demo payload |
| **`GET /spend/position`** | `503 {"detail":"spend position requires the postgres backend"}` on the memory backend |
| **Audit log in the console** | no BFF route, no UI (§7) |
| **Spend in the console** | no BFF route, no UI (§7) |
| **Deliverable history in the console** | detail-by-id only (§7g) |
| **Any external action** | no connector is reachable (§5) |
| **Multi-agent creative crew** | `/workflows/creative` runs one agent (§6) |
| **Traces** | Langfuse never constructed (§8g) |

**CFO Test scenario — can any part be demonstrated?** **Partially, and the demonstrable half is the interesting half.** The `cfo_agent` runs today: `201`, tool-loop path, deliverable produced, `total` and concentration `flags` computed deterministically in Python (`execution.py:1054-1067` — the 40% concentration threshold) rather than hallucinated. Its `spend_over_ceiling` HITL trigger is declared and, in a governed org, defers. **What cannot be shown:** the ceiling it would breach. `SpendLedger` is wired into `ToolProxy` but no tool is spend-capable (§3e), and `/spend/position` needs Postgres. **So: "the CFO agent analyses the budget and escalates" demos today. "The CFO agent is stopped by a hard money ceiling" does not.**

---

### PILOT BAR — what a design partner needs that does not exist

| Requirement | State | Evidence |
|---|---|---|
| **Self-serve onboarding, no manual DB steps** | 🔴 **BROKEN** | Two independent breaks. **(1)** `users.org_id` is `REFERENCES tenants(org_id)` (migration 0008:23) and `create_owner_of_new_org` (`dal/users.py:52-63`) does a bare `INSERT INTO users` — **registering into a genuinely new org fails on the foreign key.** `POST /api/v1/tenants` cannot fix it: it requires `Depends(get_context)` (`tenants.py:47`), i.e. auth, which requires a user, which requires the tenant. Chicken-and-egg. The integration test papers over it by pre-inserting the tenant (`test_registration_org_claim_pg.py:70`). **(2)** No runtime code inserts into `principal` — `grep -rn "INSERT INTO principal" src/` → zero hits; `PrincipalRepository` (`app/principal/provider.py`) exposes only loads. The only writer is migration 0020, a **one-shot backfill over owners that already existed**, and its own docstring (0020:26-31) notes *"on a fresh database this is a legitimate no-op."* **A newly registered owner has no principal, so every per-employee / co-work run raises `PrincipalNotFound`.** |
| **≥1 real LLM-powered agent producing real output** | 🟡 **Close** | The plumbing is real: `AnthropicAdapter` with retry/backoff (`config.py:320-323`), cost ledger, spend ceiling. Set `SKYLIZE_ANTHROPIC_API_KEY` and agents call Claude. **But** every agent shares one 3-line generic system prompt (`execution.py:1037-1052`) on `claude-haiku` (`config.py:312`) with no per-agent model choice. Output quality is untested against any bar. |
| **≥1 real external connector doing real work** | 🔴 **NONE** | §5. No OAuth flow ⇒ Drive/Asana/Notion unreachable. GitHub has no tools. Stripe and AWS do not exist. Slack posts to *Skylize's* workspace, not the customer's, and is not agent-callable. |
| **Spend tracking visible to the customer** | 🔴 **NO** | `ai_cost_ledger` (migration 0012) and `org_spend_ceiling` (0014) exist and are wired into the adapter. `GET /spend/position` exists. **No BFF route, no console page** (§7). And the tool-level ceiling is dormant (§3e). |
| **Audit trail queryable by the customer** | 🟡 **API yes, product no** | `GET /api/v1/audit` (`audit.py:44`) works and is unit-tested. **No BFF route, no console page** (§7). The product's central promise is not visible in the product. |
| **Observable traces (OTel/Langfuse)** | 🔴 **NO** | §8g. Config fields read, client never constructed, `_record_langfuse` always returns at line 717. |
| **Uptime / deployment / monitoring** | 🔴 **NO** | §8f — the `deploy` job has never completed green by its own maintainer's account (`deploy-staging.yml:156-166`). No staging environment. No monitoring of any kind found. |
| *(bonus)* **Multi-user orgs** | 🔴 **NO** | `auth.py:104-108`: *"there is now NO way to add a second user to an organisation over HTTP."* A design partner is a team, not a person. |
| *(bonus)* **Real console identity** | 🔴 **NO** | One shared password (`session/route.ts:4-6`). |

---

## PART 11 — GAP REGISTRY

| # | Gap | Severity | Blocks | file:line evidence | Status |
|---|---|---|---|---|---|
| 1 | No OAuth authorization-code flow anywhere — grants can only be hand-INSERTed | **BLOCKS_PILOT** | Drive, Asana, Notion (all `[APPROVED]`, all with working tool code) | `app/credentials/oauth.py:304-340` (refresh only); no route in `edge/routes/`; `dal/oauth_credentials.py:136` called only from `tests/` | OPEN |
| 2 | Self-serve registration fails on `users.org_id → tenants` FK; no route creates a tenant | **BLOCKS_PILOT** | every new customer | migration `0008:23`; `dal/users.py:52-63`; `edge/routes/tenants.py:47` (auth required) | OPEN |
| 3 | No runtime creation of `principal` rows — only the 0020 backfill | **BLOCKS_PILOT** | co-work + every per-employee run for any new org | `migrations/versions/0020_seed_owner_principal.py:26-31`; `app/principal/provider.py` (loads only) | OPEN |
| 4 | `decision_engine_org_ids` defaults `[]` — the synchronous governance gate is OFF | **BLOCKS_PILOT** | HITL defer, decision rejection | `config.py:266`; `execution.py:302`; `bootstrap.py:849` | OPEN |
| 5 | **No tool declares `spend=ToolSpendProfile`** — ceiling, ledger and GCP auto-hook all dormant | **BLOCKS_PILOT** | money ceilings; the entire GCP kill-switch chain | `tools/base.py:211` (default `None`); zero assignments in `tools/builtin/*.py`; dead branch at `tools/proxy.py:318` | OPEN — gated on integration_inputs.md:108 |
| 6 | No per-agent system prompts; one generic 3-line prompt for all 23 | **BLOCKS_PILOT** | output quality everywhere | `contracts/base.py:84-155` (no field); `execution.py:1037-1052` | OPEN |
| 7 | No `preferred_model` — every agent runs `claude-haiku` | BACKLOG | executive-agent quality | `AgentContract.model_fields` (absent); `execution.py:387,904`; `config.py:312` | OPEN |
| 8 | 15 of 23 agents have no demo payload | **BLOCKS_DEMO** | breadth of any investor demo | `adapters/llm/demo_adapter.py:59-110` | OPEN |
| 9 | 6 agents have zero tests of any kind | BACKLOG | trust in the registry | `creative_operations_manager`, `brand_guardian_agent`, `sdr_outreach_agent`, `lead_qualifier_agent`, `agency_requirements_analyst`, `agency_deliverable_drafter` | OPEN |
| 10 | `scripts/e2e_deliverable.py` is broken — schema drift, 422 | **BLOCKS_DEMO** | the repo's own happy-path script | script line 56-59 vs `schemas/agents/creative.py:86-91`; reproduced live | OPEN |
| 11 | Langfuse/OTel never constructed — zero traces | **BLOCKS_PILOT** | debugging a live pilot | `bootstrap.py:748-752`; `anthropic_adapter.py:717`; `config.py:300-301` unused | OPEN |
| 12 | Deploy pipeline has never completed green; no staging env | **BLOCKS_PILOT** | everything | `.github/workflows/deploy-staging.yml:156-166` (maintainer's own note) | OPEN |
| 13 | Console has no audit page | **BLOCKS_DEMO** | the core product promise | no BFF route under `website/src/app/api/console/`; `edge/routes/audit.py:44` exists | OPEN |
| 14 | Console has no spend page | **BLOCKS_DEMO** | "sets budget ceilings" (`mvp_definition.md` §3) | no BFF route; `edge/routes/spend.py:56` exists | OPEN |
| 15 | Console has no deliverable list | **BLOCKS_DEMO** | customer can't see past work | only `api/console/deliverables/[id]/route.ts` | OPEN |
| 16 | Console login is one shared password | **BLOCKS_PILOT** | multi-user, per-org identity | `website/src/app/api/console/session/route.ts:4-6` | OPEN — OIDC epic deferred |
| 17 | No way to add a second user to an org | **BLOCKS_PILOT** | a design partner is a team | `edge/routes/auth.py:104-108` ("item E20") | OPEN |
| 18 | `/workflows/creative` is a single agent, not a crew | **BLOCKS_DEMO** | `mvp_definition.md` §3 "Creative crew" | `edge/routes/workflows.py:57-59` | OPEN |
| 19 | `policy_inputs.md`: 6 of 7 sections `[OWNER-DECISION-REQUIRED]`; §0.5 security veto not implemented | **BLOCKS_PILOT** | ADR-0004 OPA; all real thresholds | `policy_inputs.md:3,48,125,164,215,252,350` | OPEN |
| 20 | GitHub: foundation only, no tools, no webhook | BACKLOG | repo reads, PR creation, uninstall detection | `app/github/__init__.py:9-17`; zero hits for github in `src/skylize/tools/` | OPEN by design |
| 21 | Stripe and AWS: no implementation at all | BACKLOG | billing; AWS containment | zero non-comment hits in `src/` | OPEN — §2.1/§2.2 unsigned |
| 22 | 6 routes have integration-only tests — untested in CI's `unit` job | **BLOCKS_PILOT** | kill switch + HITL are the safety-critical two | `cowork`, `hitl`, `kill_switch`, `spend`, `tenants`, `workflows` | OPEN |
| 23 | Contract count conflict: code 23, `mvp/__init__.py:4` says 22, `policy_inputs.md:297` says 24 | BACKLOG | doc trust | `len(ALL_MVP_CONTRACTS)`=23 vs those two lines | **CONFLICT — reported, unresolved** |
| 24 | Frontend targets Railway; backend deploys to AWS ECS | **BLOCKS_PILOT** | nobody can say where the API lives | `website/src/lib/skylize/config.ts:14` + `console/page.tsx:26-28` vs `deploy-staging.yml` + `infra/terraform/staging/` | **CONFLICT — reported, unresolved** |
| 25 | `ToolCallCounter._counts` never evicted — unbounded growth | BACKLOG | long-running process memory | `tools/proxy.py:89-94` (documented in its own docstring) | OPEN |
| 26 | Connection pool `max_size=10`, no per-org cap | BACKLOG | noisy neighbour | `dal/connection.py:56` | OPEN |
| 27 | `users` / `api_keys` / `tenant_users` have no RLS — isolation is hand-written `WHERE` | BACKLOG (accepted) | the class migration 0003 eliminated elsewhere | `0004:57-59`, `0008:65`, `0003:43` | ACCEPTED, documented |
| 28 | Dead code: `runtime/tool_proxy.RegistryToolProxy`, `runtime/run_ledger`, memory gateway | BACKLOG | audit noise; two skipped tests | `tests/unit/test_llm_agent_runner.py:61`, `tests/unit/test_memory_gateway.py:79` (self-declared) | OPEN |
| 29 | HEAD is 26 commits behind `origin/main` | BACKLOG | this audit may already be stale | `git rev-list --left-right --count origin/main...HEAD` → `0 26` | OPEN |
| 30 | `Skylize_Design_Specification.md` does not exist | BACKLOG | §1.2 sitemap check unrunnable | `find . -name "Skylize_Design_Specification.md"` → no match | **ABSENT** |

---

## PART 12 — RECOMMENDED SEQUENCE

Ordered by unblock-value. No schedule.

**0. Re-baseline first.** HEAD is 26 commits behind `origin/main` (#29). Everything below should be re-checked against `origin/main` before work starts — this audit describes `a474c2c8`.

---

**1. Fix `scripts/e2e_deliverable.py` (#10).** A ten-minute payload correction. It is the repo's own advertised happy path and it exits 1 today. Add it to CI so it cannot drift again. Do this first because it costs nothing and because a broken demo script is what someone runs in front of an investor.

**2. Unblock the demo bar: demo payloads + the three missing console pages (#8, #13, #14, #15).** These are additive, need no owner decision, and convert "70% demoable" into a complete story. Order within: demo payloads for the remaining 15 agents (a dict literal), then the audit page, then spend, then the deliverable list — audit first because "every action explainable" is the pitch and it is currently invisible. Each console page is one BFF route plus one component; the backend endpoints already exist and are tested.

**3. Build the onboarding path (#2, #3).** Nothing in the pilot bar can be attempted while a new customer cannot exist. This is a single coherent piece of work: make registration atomically create `tenants` + `users` + `principal` in one transaction, deriving `principal_id` from `users.user_id::text` exactly as migration 0020 does (0020:16-24 warns that any other derivation silently forks the identity space). Then delete the chicken-and-egg from `POST /api/v1/tenants`.

**4. Build the OAuth connect flow (#1).** The single highest-leverage item in the document: it converts three `[APPROVED]` designs with working, tested connector code from unreachable to shippable in one build. Authorize URL + state/PKCE + callback + `OAuthCredentialRepository.insert` + a console "Integrations" page. Depends on step 3 (a grant needs an org). Owner input needed on integration_inputs §3.0 (#5 in the decision table), but that section is *"recorded, deliberately not implemented"* rather than contested — likely a fast decision.

**5. Turn the governance gate on by default (#4).** Today `decision_engine_org_ids=[]` means HITL never fires unless an operator names each org by hand. Invert it: govern all orgs, with an explicit opt-out list. **This is the difference between a governance product and a product that has governance code.** Small change, large semantics — do it deliberately and with the owner.

**6. Close the two safety-critical test holes (#22).** `kill_switch` and `hitl` have integration-only coverage, so CI's `unit` job — the gate on every PR — never exercises them. Add unit tests. Cheap; these are the two routes whose regression is least acceptable.

**7. Wire Langfuse (#11).** Roughly ten lines in `bootstrap.py` to construct the client from config fields that are already read, and pass it at `bootstrap.py:748`. Do this **before** the first real-LLM pilot traffic, not after: debugging a design partner's bad output with no traces is the failure mode that ends pilots.

**8. Get one green deploy (#12, #24).** Resolve the Railway-vs-ECS conflict first — that is an owner decision, not an engineering one — then drive `deploy-staging.yml` to a green run and restore the `:latest` tag its own comment gates on. Everything after this point needs somewhere to run.

**9. Take the owner decisions that unblock money (#19, and integration_inputs §1.1).** `policy_inputs.md` §0.1/§0.2 and `integration_inputs.md` §1.1 together gate assigning `ToolSpendProfile` to real tools. Until that decision lands, gap #5 cannot be closed by engineering at all — the SpendLedger, the `spend_envelope`/`spend_reservation` tables, and the whole GCP containment chain stay dormant no matter what is written.

**10. Assign spend profiles (#5), unblocking the GCP kill switch.** Immediately after step 9. The code is written and integration-tested; it needs a policy number, not a build. This is the step that makes the CFO Test fully demonstrable.

**11. Per-agent system prompts + `preferred_model` (#6, #7).** Add both to `AgentContract`. Note `model_config = ConfigDict(frozen=True, extra="forbid")` (contracts/base.py:92) means every contract must be touched — mechanical, but not free. Sequenced here because it raises quality on a product that by this point actually works; doing it earlier polishes something nobody can reach.

**12. Multi-user orgs (#17) and real console identity (#16).** A design partner is a team. The governed invite flow (`auth.py:104-108`, "E20") plus the deferred OIDC epic. Last of the pilot-bar items because a single-owner pilot can start without it — but it cannot expand without it.

**Deliberately not sequenced:** GitHub tools (#20), Stripe/AWS (#21), the crew workflow (#18), and the dead-code cleanup (#28). Each is real work, none of it unblocks anything above it, and two of them (#20, #21) are gated on owner sections that are still unsigned.

---

*End of audit. No source file was modified. Two items are reported as unresolved conflicts (#23, #24); two are marked ABSENT (#30, and the `Skylize_Design_Specification.md` sitemap check in §7).*
