# Integration: TikTok Ads

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_performance_marketing` · `director_growth` · `director_backend`
**Related:** [meta_ads.md](./meta_ads.md) · [../04_decision_engine/capital_allocation.md](../04_decision_engine/capital_allocation.md) · [../04_decision_engine/decision_engine.md](../04_decision_engine/decision_engine.md)

---

## 1. Purpose

TikTok Ads is a **spend-bearing ad platform** integrated on the same pattern as
[meta_ads.md](./meta_ads.md): read performance, perform scoped campaign ops only
after a governed decision. The structural sameness is deliberate — both are
spend-bearing adapters behind the same Decision Engine gate.

## 2. Architectural role

- TikTok owns ad account performance & spend; Skylize reads metrics, performs
  **scoped writes** when authorized.
- Inbound performance via adapter pulls; outbound campaign ops via the TikTok
  adapter at `IF-INTEGRATION`. **Every spend passes the Decision Engine.**

## 3. Authentication & secrets

- Per-tenant TikTok Marketing API credentials/access tokens in the secrets
  manager; adapter-only; never in code/logs/events/memory.

## 4. Inbound (performance → events)

- Adapter pulls spend/impressions/CTR/CVR/ROAS per ad account, tenant-scoped, and
  emits `sales.performance_ingested` (feeds episodic memory + Campaign Allocation
  Score).

## 5. Outbound (campaign ops, `IF-INTEGRATION`)

- Create/launch/pause/adjust campaigns/budgets/creatives — **only** after
  `decision.approved` + valid governance token, within the capital ceiling.
- First launch on a new ad account → HITL (`FIRST_EXTERNAL_LAUNCH`); over-ceiling
  → HITL (`SPEND_OVER_CEILING`).
- Budget reserved against the ledger before launch.

## 6. Tenant isolation

Ad-account bindings + metrics carry `org_id`; agents act only on their tenant's
accounts; cross-tenant action impossible and audited.

## 7. Failure handling

- API error/rate-limit → backoff retry (`retry_then_escalate`); persistent failure
  escalates + alerts.
- Boundary refusal (budget/scope/policy) → proposing agent's `escalation_path`.
- Spend drift reconciled against the ledger; anomalies → `director_risk`/fraud.

## 8. Events produced

`sales.performance_ingested`, `audit.action_recorded`, `governance.*` on refusals.

## 9. Ownership & evolution

- **Owner:** `director_performance_marketing` & `director_growth` (functional),
  `director_backend` (adapter).
- **Evolution:** new endpoints additive; the governed-spend invariant is shared
  with Meta and permanent. Multiple ad platforms behind the same gate is what lets
  Growth reallocate across channels under one capital policy.
