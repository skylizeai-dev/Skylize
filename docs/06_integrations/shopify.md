# Integration: Shopify

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_store_operations` · `director_backend`
**Related:** [stripe.md](./stripe.md) · [meta_ads.md](./meta_ads.md) · [../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own) · [../05_memory/knowledge_graph.md §3](../05_memory/knowledge_graph.md#3-core-entities--relationships)

---

## 1. Purpose

Shopify is the tenant's **store system of record**: catalog, orders, customers.
Skylize integrates to *read a scoped mirror* (for context and scoring) and to
*write scoped actions* (where authorized) — never to become the system of record
for store data.

## 2. Architectural role

- **Skylize never owns store data.** Shopify remains authoritative; Skylize holds
  a **scoped, expiring mirror + reference IDs**
  ([../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)).
- Inbound changes arrive via **signed webhooks** at the edge; outbound calls go
  through the Shopify adapter at `IF-INTEGRATION`.

## 3. Authentication & secrets

- OAuth access token / API credentials per tenant in the secrets manager; read
  only by the adapter. Webhook HMAC secret likewise.

## 4. Inbound (webhooks → events)

```
Shopify ──HMAC-signed webhook──▶ edge (verify signature) ──▶ typed internal event ──▶ IF-EVENT
```

- The edge **verifies the Shopify HMAC**; an invalid signature is dropped with
  `401` + `governance.integration_bad_signature`
  ([../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration)).
- Verified payloads map to `sales.*` events (e.g. order created → enriches the
  per-tenant Customer/Order/Product graph mirror).

## 5. Outbound (adapter, `IF-INTEGRATION`)

- Scoped reads: catalog/orders/customers for context and scoring, tenant-scoped.
- Scoped writes (where authorized and policy-approved): e.g. publishing an
  approved asset, updating a product description — only after a
  `decision.approved` and a valid governance token.
- Adapter holds credentials, normalizes responses, emits result + `audit.*`
  events.

## 6. Tenant isolation

Every mirrored record carries `org_id` (RLS); one tenant's Shopify data never
appears in another's context ([../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth)).

## 7. Failure handling

- Webhook signature fail → drop + audit.
- API error/rate-limit → adapter retries with backoff per `retry_then_escalate`;
  persistent failure escalates and is monitored (DLQ/alert).
- Drift between mirror and Shopify is reconciled on a schedule; Shopify wins.

## 8. Events produced

`sales.*` (mirror updates from webhooks), `audit.action_recorded`, and
`governance.integration_bad_signature` on bad inbound signatures.

## 9. Ownership & evolution

- **Owner:** `director_store_operations` (functional), `director_backend`
  (adapter).
- **Evolution:** new Shopify resources/webhooks are additive mappings; the
  "Shopify is system-of-record, Skylize mirrors" invariant is permanent.
