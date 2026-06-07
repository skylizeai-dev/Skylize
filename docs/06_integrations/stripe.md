# Integration: Stripe

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_treasury` · `director_backend` · `chief_security_officer`
**Related:** [shopify.md](./shopify.md) · [../04_decision_engine/capital_allocation.md](../04_decision_engine/capital_allocation.md) · [../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)

---

## 1. Purpose

Stripe is the **payment & subscription system of record**. Skylize integrates to
read **billing reference IDs and subscription/payment state** — never card data.
It is the source of truth for what a tenant is billed and for settlement signals
feeding capital allocation.

## 2. Architectural role

- Skylize holds **billing reference IDs only; never PAN/card data**
  ([../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)).
  This keeps card data out of scope (PCI burden stays with Stripe).
- Inbound via **signed webhooks** (Stripe signature) at the edge; outbound via the
  Stripe adapter at `IF-INTEGRATION`.

## 3. Authentication & secrets

- Stripe API key + webhook signing secret in the secrets manager; adapter-only
  access; never in code/logs/events/memory.

## 4. Inbound (webhooks → events)

- Edge **verifies the Stripe signature**; invalid → `401` +
  `governance.integration_bad_signature`.
- Verified events (subscription updated, invoice paid, payment failed) map to
  internal events that update the tenant's billing reference state and feed
  settlement reconciliation.

## 5. Outbound (adapter, `IF-INTEGRATION`)

- Read subscription/plan state, create/adjust subscriptions or usage records
  (where authorized) — only after a `decision.approved` + valid governance token.
- No card data ever transits Skylize; tokenized references only.

## 6. Relationship to capital allocation

Settlement signals (paid/failed/refunded) reconcile the budget ledger's `spent`
against `committed` ([../04_decision_engine/capital_allocation.md §4](../04_decision_engine/capital_allocation.md#4-the-budget-ledger));
drift triggers `director_risk` review.

## 7. Tenant isolation & security

- Billing references carry `org_id` (RLS).
- Stripe is the highest-sensitivity integration; `chief_security_officer` reviews
  the adapter; access is least-privilege; all calls audited.

## 8. Failure handling

- Signature fail → drop + audit.
- API error → backoff retry; persistent failure escalates (treasury/risk) and
  alerts.
- Reconciliation drift → flagged, never silently absorbed.

## 9. Events produced

billing/subscription state-update events, settlement events feeding capital
allocation, `audit.action_recorded`, `governance.integration_bad_signature`.

## 10. Ownership & evolution

- **Owner:** `director_treasury` (functional), `director_backend` (adapter),
  `chief_security_officer` (security review).
- **Evolution:** new Stripe events are additive mappings; "reference IDs only,
  never card data" is a permanent, PCI-scope-limiting invariant.
