# Capital Allocation

**Status:** Subsystem specification (source of truth for spend control)
**Owner:** `cfo` · `director_capital_allocation` · Principal Architect
**Related:** [decision_engine.md](./decision_engine.md) · [guardrails.md](./guardrails.md) · [scoring_models.md](./scoring_models.md) · [../03_agents/agent_governance.md §9](../03_agents/agent_governance.md#9-human-in-the-loop-trigger-conditions)

---

## 1. Purpose

Capital allocation is how Skylize controls **money**: who may spend, how much,
within what ceilings, and when a human must approve. It is the financial guardrail
applied as **STAGE 4** of every decision that moves budget — ad spend, vendor
commitments, tooling. The principle: *autonomy over allocation, never over the
ceiling.* Agents may move budget within limits; crossing a ceiling always defers
to a human.

## 2. Architectural role

Capital allocation is a policy class in the Decision Engine
([guardrails.md §3](./guardrails.md#3-policy-classes)) backed by a per-tenant
**budget ledger**. The Decision Engine checks a proposal's amount against the
relevant ceiling before approving; the Governance Authority's token budgets
(`max_token_budget`) cover *LLM* cost, while this covers *business* spend — two
independent ceilings.

```
spend proposal (sales.budget_reallocation_proposed, campaign_proposed, vendor commit)
        │
        ▼ STAGE 4
   ceiling check against budget ledger (tenant → department → campaign)
        ├─ within all ceilings & authority OK → approved → adapter executes spend
        ├─ over a ceiling                      → deferred_to_human (SPEND_OVER_CEILING)
        └─ authority insufficient              → deferred_to_human (AUTHORITY_EXCEEDED)
```

## 3. The budget hierarchy

Ceilings cascade and the **tightest applicable ceiling wins**:

| Level | Set by | Example |
|---|---|---|
| **Platform floor** | Skylize policy | hard cap no tenant config can exceed without contract change |
| **Tenant ceiling** | the tenant (human owner) | monthly total ad budget |
| **Department ceiling** | `cfo` / `director_capital_allocation` delegation | marketing's share |
| **Campaign/initiative ceiling** | `director`/`vp` within delegated cap | one campaign's budget |
| **Per-run / per-action ceiling** | contract + token | a single launch's max |

An agent's authority to *allocate within* a cap is delegated down the tree; the
authority to *raise* a cap escalates up it
([agent_governance.md §3](../03_agents/agent_governance.md#3-authority--escalation)).

## 4. The budget ledger

```python
class BudgetLedger(BaseModel):
    org_id: str                       # tenant scoped, always
    scope: str                        # "department:marketing" | "campaign:42"
    ceiling: int                      # currency minor units
    committed: int                    # approved + in-flight
    spent: int                        # settled
    period: str                       # e.g. "2026-05"
    # available = ceiling - committed
```

- The ledger is in Postgres (system of record), tenant-isolated by RLS.
- Every approved spend **reserves** against `committed` before the adapter
  executes, preventing concurrent proposals from jointly overshooting (reservation
  is transactional; per-`partition_key` ordering prevents races on one campaign).
- Settlement events from integration adapters (Meta/TikTok/Stripe) reconcile
  `spent` against `committed`; drift triggers `director_risk` review.

## 5. Authority × spend matrix

Consistent with [agent_governance.md §3](../03_agents/agent_governance.md#3-authority--escalation):

| Level | May spend |
|---|---|
| `worker` | nothing — workers propose, never authorize spend |
| `manager` | within a small pre-set operational threshold |
| `director` | within a delegated department/campaign cap |
| `vp` | reallocate within a function, up to function cap |
| `executive` | top-level budget; above the human-approval ceiling → human |

Any spend above the tenant-configured ceiling — at **any** level, including
executive — triggers `SPEND_OVER_CEILING` and defers to the human owner.

## 6. Reallocation flow

Growth/finance agents propose reallocations (`sales.budget_reallocation_proposed`);
the engine scores them ([scoring_models.md](./scoring_models.md) — Campaign
Allocation Score), checks ceilings, and approves within caps or defers. A
reallocation that *reduces* risk/spend may auto-approve within authority; one that
*increases* exposure beyond a cap always defers. All movements are audited and
reconcilable.

## 7. Failure & safety

- Ledger unavailable or inconsistent → spend decisions **fail closed** (deny /
  defer), never approve on uncertainty.
- A kill switch at any scope freezes all spend in scope immediately
  ([kill_switch_protocol.md](./kill_switch_protocol.md)).
- `director_risk` and `fraud_detection_agent` monitor for anomalous spend
  patterns; a flag can veto an in-flight allocation (safety veto,
  [guardrails.md §5](./guardrails.md#5-evaluation-contract)).

## 8. Ownership & evolution

- **Owner:** `cfo` and `director_capital_allocation` own ceilings and the
  allocation policy; `director_treasury` owns settlement reconciliation;
  Principal Architect owns the ledger contract.
- **Evolution:** MVP uses static, human-set ceilings with transparent rules; at
  Scale, the Campaign Allocation Score may *recommend* reallocations that still
  pass the same ceiling/authority gates. The "no autonomy over the ceiling"
  invariant is permanent.
