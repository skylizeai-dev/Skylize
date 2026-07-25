# ADR 0006 — Billing-Grade AI Cost Ledger: A Third, Immutable Money Ledger Sourced at the Provider-Usage Seam

**Status:** Accepted
**Date:** 2026-07-25
**Deciders:** Principal Architect, human owner
**Related:** [capital_allocation.md](../../04_decision_engine/capital_allocation.md) · [ADR-0004](./0004-opa-production-arbiter.md) · `src/skylize/adapters/llm/gateway.py` · `src/skylize/runtime/tool_proxy.py` · `src/skylize/runtime/run_ledger.py` · `src/skylize/dal/cost_ledger.py` · `migrations/versions/0012_ai_cost_ledger.py` · `pyproject.toml` (`[tool.importlinter]`)

---

## Context

Cost attribution on a multi-tenant platform is **billing-grade**: an incorrect number here becomes an incorrect customer invoice. The platform already has two money-adjacent ledgers, and neither is LLM cost expressed in money:

- The **token `run_ledger`** (`runtime/run_ledger.py`) is an *in-flight per-run token ceiling* — RAM (`InMemoryRunLedger`) or Redis (`RedisRunLedger`), never Postgres. It enforces `max_token_budget` fail-closed during a run and is discarded when the run ends. It counts **tokens**, not money, and keeps no history.
- **`budget_ledger`** (migration 0001) is *business spend against a ceiling* — ad spend, vendor commitments — in currency minor units. [capital_allocation.md §2](../../04_decision_engine/capital_allocation.md#2-architectural-role) states explicitly that token budgets (`max_token_budget`, LLM cost) and `budget_ledger` (business spend) are **two independent ceilings**.

This ADR introduces the **missing third thing**: **LLM cost expressed in money**, recorded per real provider call, immutable, and reconcilable against a provider invoice.

### Sources rejected by the owner (do not relitigate)

- **NOT Langfuse.** The gateway's own contract says it "records cost/quality in **Langfuse** keyed by `governance_token_id`" (`adapters/llm/gateway.py:11`). Langfuse is observability-grade: best-effort, sampled, retention-bounded. A sampled trace cannot underwrite an invoice.
- **NOT the event bus.** Bus delivery semantics are under active repair; at-least-once/at-most-once is not currently a guarantee we can bill on.

Cost is therefore written **transactionally to Postgres** (the system of record) at the single point where **real provider usage is observed**.

## The three-ledger distinction (write this into the record so a future session cannot conflate them)

| | token `run_ledger` | `budget_ledger` | **`ai_cost_ledger`** (this ADR) |
|---|---|---|---|
| **Question** | "tokens left in this run?" | "business spend vs ceiling?" | "money owed for LLM usage?" |
| **Unit** | tokens | currency minor units | currency **micro**-units (`cost_micros`) |
| **Store** | RAM / Redis | Postgres | Postgres |
| **Lifetime** | one run, then gone | current period | permanent, immutable |
| **Mutability** | debited in place | UPDATE `committed`/`spent` | **append-only**; corrections via reversal |
| **Scope** | run key (token id) | org + scope + period | org (RLS) + per-call row |
| **Source** | tool-proxy debit | spend proposals | **provider-usage seam** |
| **Module** | `runtime/run_ledger.py` | `dal` + engine | `dal/cost_ledger.py` |

`ai_cost_ledger` is **not** the token run ledger (an in-flight ceiling) and **not** `budget_ledger` (business spend). It is the money value of consumed model tokens.

## The seam: where cost is written

**Decision: the write point is the concrete LLM gateway adapter** (the `LLMGateway` port in `adapters/llm/gateway.py`, whose Anthropic/OpenAI implementations are Sprint-2 / task T-B1B and **do not exist yet**). Reasoning, from code:

1. **It is the single first-hand observation of real provider usage.** Only the concrete adapter sees the provider response, i.e. actual `LLMUsage` (`prompt_tokens`/`completion_tokens`/`total_tokens`, `gateway.py:124-129`), the `provider` and `concrete_model` served (`gateway.py:136-137`), and the real cost (`cost_usd_micros`, `gateway.py:139`). Everything downstream is a copy.

2. **The tool proxy is disqualified on two independent grounds.**
   - *Lossy copy.* `RegistryToolProxy.dispatch` observes usage only via `_actual_tokens(handler_result)`, which extracts **`total_tokens` and nothing else** (`tool_proxy.py:540-544`) from an untyped `dict`. The input/output split, provider, and concrete model — all required for a per-call cost — are discarded there.
   - *Layering.* The import-linter contract **"Pure inner layers hold no database driver"** lists `skylize.runtime` among the modules forbidden from importing `asyncpg` (`pyproject.toml:158-168`). The tool proxy lives in `runtime`. Making it write to Postgres would breach that contract. **This is exactly the STOP-condition the task names — and the resolution is to choose the layer-legal seam, not to work around the contract.** The gateway lives in `skylize.adapters.llm`, which is *not* an inner layer, so an adapter may depend on the `dal` cost-ledger port. **The DB write always executes inside the `dal` layer (`dal/cost_ledger.py`), never in `runtime`.** The "Pure inner layers hold no database driver" contract remains **KEPT** after this change.

3. **Honest gap — neither seam has the full attribution tuple today (minimal plumbing required, deferred to T-B1B).** Billing needs `{org_id, correlation_id, agent_id, run_id, provider, concrete_model, input_tokens, output_tokens}`. The gateway request (`LLMGenerateRequest`, `gateway.py:82-100`) carries only `governance_token_id` and `org_id` — it does **not** carry `agent_id` or `correlation_id` as typed fields. These are real, resolvable values (the governance token row already has both `agent_id` and `correlation_id` columns — migration 0001, `governance_tokens`), so the minimal, non-invented plumbing is: **add `agent_id: str` and `correlation_id: UUID` to `LLMGenerateRequest` / `LLMGenerateWithToolsRequest`**, populated by the tool proxy from the token it already holds. No field is fabricated. This plumbing **and** the call from the adapter into `CostLedgerDAL.record_cost` are **T-B1B and out of scope here** — this ADR ships the contract and proves it in isolation.

## Immutability (enforced by the database, not by convention)

`ai_cost_ledger` is append-only at two layers, mirroring `audit_log` (migrations 0001 + 0003):

1. **Least privilege.** `skylize_app` is granted only `SELECT, INSERT` on `ai_cost_ledger` — no `UPDATE`/`DELETE` (migration 0012). This mirrors the `audit_log` grant in migration 0003.
2. **Trigger.** A `BEFORE UPDATE OR DELETE` row trigger (`ai_cost_ledger_append_only` → `skylize_prevent_ledger_mutation()`) raises, so even a role holding the grant cannot mutate a row. This fires for superusers too (unlike RLS), so history is immutable even to `postgres`.

**Corrections happen via reversing entries, never UPDATE.** A correction is a new row with `entry_type='reversal'`, negated `input_tokens`/`output_tokens`/`cost_micros`, and `reverses_entry_id` pointing at the charge. A `CHECK` constraint enforces the sign discipline (`charge` ≥ 0, `reversal` ≤ 0 with `reverses_entry_id` set).

**Tenant isolation** mirrors `budget_ledger` exactly: `ENABLE` + `FORCE ROW LEVEL SECURITY` with a single `tenant_isolation` `FOR ALL` policy on `current_setting('skylize.org_id', true)`, `USING` and `WITH CHECK` identical (migration 0012, cf. migration 0001).

## Pricing (versioned, effective-dated, snapshotted)

Prices change and must **never retroactively alter history**. `model_pricing` is versioned reference data with `effective_from` / `effective_to` dating and a `version`. Each `ai_cost_ledger` row **snapshots** the `input_price_micros_per_mtok`, `output_price_micros_per_mtok`, and `pricing_version` it used — so a later price row cannot change a recorded cost. `model_pricing` is **platform-level (no RLS)**, like `api_keys` (migration 0004); a nullable `org_id` reserves a per-tenant (BYOK-negotiated) override with **global-price fallback** (resolution prefers the tenant row, then `org_id IS NULL`). **The seed is intentionally empty — no price is fabricated** (project empty-value convention); ops seeds real prices before any cost is recorded, and `record_cost` **fails closed** (`PricingNotFound`) if no active price covers the call.

## Money & rounding (the residue has a defined home)

- **Stored unit: `cost_micros` (BIGINT), millionths of one currency unit** — matching the gateway's existing `cost_usd_micros` contract (`gateway.py:139`). We deliberately do **not** store whole minor units (cents) per row: a `$0.60/Mtok` call of 100 tokens costs 0.006 ¢ and would round to **zero cents per row** — precisely the drift that turns a ledger wrong. This is a considered deviation from the task's suggested `cost_minor` column name, made under "code is ground truth" and the task's own severity framing; cents are **derived**, never stored.
- **Python `Decimal`, never `float`**, everywhere in the money path (guarded by a unit test).
- **Unit prices are per 1e6 tokens (`*_per_mtok`)** so every real quoted price is an exact integer (`$3.00/Mtok` → `3_000_000` µ/Mtok).
- **Rounding rule:** `cost_micros = round_half_up( (in_tok·in_price + out_tok·out_price) / 1_000_000 )`, ROUND_HALF_UP to the nearest whole micro. **Where the residue goes:** the discarded fraction is `< 0.5` micro (`< 5×10⁻⁷` of one currency unit per row) and is absorbed by that nearest-micro rounding — never truncated, never floated. Because a micro is 100× finer than a cent, per-row rounding cannot perturb any cent-level total.
- **Cents are produced ONCE, at aggregation:** `cents = round_half_up( SUM(cost_micros) / 10_000 )`. Reconciliation sums exact integers and rounds a single time, so ledger↔invoice reconciliation is **exact (tolerance zero)**.

## Reconciliation

A ledger total is reconciled to a provider invoice line via `provider`, `provider_invoice_ref`, and `billing_period` (+ `SUM(cost_micros)` grouped by provider/period, indexed). `provider_invoice_ref` is set when a period is settled against the received invoice.

## BYOK shape (default now, so it is not a rewrite later)

The platform has **no API keys wired yet**. The default resolution shape is **per-tenant key with global-key fallback**: `resolve_key(org_id, provider)` → the tenant's own key if present, else the global platform key. A BYOK tenant's rows differ: `byok = true`, and **markup is not applicable** — `cost_micros` is the tenant's own provider cost (recorded for their visibility), and the invoicer must skip any platform markup when `byok` is set. No markup value is stored or fabricated here. `model_pricing.org_id` reserves the symmetric per-tenant price override.

## Idempotency (a retried call must not double-charge)

The natural unique key is **`(org_id, idempotency_key)`**, where `idempotency_key` is the provider's own response/request id for the served call (globally unique per real charge). `record_cost` INSERTs with `ON CONFLICT (org_id, idempotency_key) DO NOTHING` and returns the winning row with `inserted=False` on a retry — so a retried *write* collapses to one row, while two genuine provider charges (two ids) both record. This mirrors the `decision_processed_events` `ON CONFLICT DO NOTHING` pattern (migration 0011).

## Consequences

- **New:** `ai_cost_ledger` + `model_pricing` (migration 0012, head `0011 → 0012`), `dal/cost_ledger.py`, unit + integration tests. `alembic upgrade head` and `alembic downgrade -1` both verified clean against real Postgres.
- **Deferred to T-B1B (NOT in this change):** add `agent_id` + `correlation_id` to `LLMGenerateRequest`; implement the concrete gateway adapter; call `CostLedgerDAL.record_cost` from it. The gateway/tool-proxy are deliberately **not** wired to the ledger here.
- **Unchanged:** `src/skylize/events/**` (owned by another terminal this session) is untouched. The pre-existing import-linter break ("Application logic contains no SQL", via `app/orchestrator/temporal/worker`) is unrelated to this change and neither introduced nor worsened by it.
