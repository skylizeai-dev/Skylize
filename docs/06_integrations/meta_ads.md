# Integration: Meta Ads

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_performance_marketing` · `director_growth` · `director_backend`
**Related:** [tiktok_ads.md](./tiktok_ads.md) · [../04_decision_engine/capital_allocation.md](../04_decision_engine/capital_allocation.md) · [../04_decision_engine/decision_engine.md](../04_decision_engine/decision_engine.md) · [../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)

---

## 1. Purpose

Meta Ads is an **ad platform** Skylize reads performance from and (where
authorized) writes campaign operations to. It is the canonical example of a
**spend-bearing** integration: nothing launches or scales spend here without a
`decision.approved` and, on first launch/over-ceiling, human approval.

## 2. Architectural role

- Meta owns ad account performance & spend; Skylize reads metrics and performs
  **scoped writes** (campaign ops) only when authorized
  ([../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)).
- Inbound performance via scheduled adapter pulls (and webhooks where available);
  outbound campaign ops via the Meta adapter at `IF-INTEGRATION`.
- **Every spend action passes the Decision Engine** (authority → policy → capital
  → HITL) before the adapter executes.

## 3. Authentication & secrets

- Per-tenant Meta app credentials / access tokens + app secret in the secrets
  manager; adapter-only; never in code/logs/events/memory.

## 4. Inbound (performance → events)

- Adapter pulls spend/impressions/CTR/CVR/ROAS per ad account, tenant-scoped, and
  emits `sales.performance_ingested`.
- This data feeds episodic memory and the Campaign Allocation Score
  ([../04_decision_engine/scoring_models.md](../04_decision_engine/scoring_models.md)).

## 5. Outbound (campaign ops, `IF-INTEGRATION`)

- Create/launch/pause/adjust campaigns, budgets, and creatives — **only** after a
  `decision.approved` + valid governance token, and within the capital ceiling.
- **First external launch on a new ad account → HITL** (`FIRST_EXTERNAL_LAUNCH`);
  spend over ceiling → HITL (`SPEND_OVER_CEILING`)
  ([../03_agents/agent_governance.md §9](../03_agents/agent_governance.md#9-human-in-the-loop-trigger-conditions)).
- A budget reservation is taken against the ledger before launch
  ([../04_decision_engine/capital_allocation.md §4](../04_decision_engine/capital_allocation.md#4-the-budget-ledger)).

## 6. Tenant isolation

Ad-account bindings and metrics carry `org_id`; an agent can only act on ad
accounts bound to its tenant. Cross-tenant ad action is impossible and audited.

## 7. Failure handling

- API error/rate-limit → backoff retry per `retry_then_escalate`; persistent
  failure escalates to `director_performance_marketing` and alerts.
- A launch refused at the boundary (budget/scope/policy) invokes the proposing
  agent's `escalation_path`.
- Settlement/spend drift reconciled against the ledger; anomalies → `director_risk`/fraud.

## 8. Events produced

`sales.performance_ingested` (inbound), `audit.action_recorded` per op,
`governance.*` on policy/budget refusals.

## 9. Ownership & evolution

- **Owner:** `director_performance_marketing` & `director_growth` (functional),
  `director_backend` (adapter).
- **Evolution:** new campaign objectives/endpoints are additive; the "every spend
  passes the Decision Engine; first launch and over-ceiling defer to human"
  invariant is permanent.
