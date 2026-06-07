# Integration: n8n

**Status:** Integration adapter spec (source of truth for this surface)
**Owner:** `director_platform` · `director_backend`
**Related:** [mcp_servers.md](./mcp_servers.md) · [../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration) · [../architecture/01_final_stack.md §3](../architecture/01_final_stack.md#3-rejected-overlaps-one-tool-per-job)

---

## 1. Purpose

n8n is the **low-code workflow automation** surface — for connecting long-tail
SaaS tools and bespoke automations without building each as a first-class adapter.
It is treated as an **external execution surface**, not an internal component: we
do not reinvent a workflow engine internally
([../architecture/01_final_stack.md §3](../architecture/01_final_stack.md#3-rejected-overlaps-one-tool-per-job)).

## 2. Architectural role

n8n sits **outside** Skylize's boundaries:
- Skylize **triggers** n8n workflows with a **signed payload** via the n8n adapter
  at `IF-INTEGRATION`.
- n8n **calls back** with **signed** results received at the edge (HMAC), mapped to
  internal events.
- **n8n holds no Skylize credentials and cannot read internal data** — it only
  receives the scoped payload it is sent
  ([../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration)).

```
Skylize adapter ──signed trigger payload──▶ n8n workflow ──signed callback──▶ edge (verify) ──▶ IF-EVENT
```

## 3. Authentication & secrets

- Shared-secret HMAC for both directions, in the secrets manager.
- n8n's own credentials for third-party tools live in **n8n**, not Skylize —
  containing third-party secret sprawl outside our boundary.

## 4. Trust model

- Outbound payloads are **scoped** to exactly what the workflow needs; never bulk
  tenant data.
- Inbound callbacks are **signature-verified**; invalid → `401` +
  `governance.integration_bad_signature`.
- An n8n-driven action that would cause a Skylize side effect still routes through
  the normal event/Decision-Engine path — n8n cannot bypass governance.

## 5. Tenant isolation

Trigger payloads and callbacks carry `org_id`; a workflow run is bound to one
tenant. n8n cannot read across tenants because it cannot read Skylize data at all.

## 6. Failure handling

- Callback signature fail → drop + audit.
- Workflow timeout/no-callback → adapter marks the trigger failed; proposing
  agent's `failure_mode`/`escalation_path` applies.
- n8n outage is isolated: it degrades long-tail automations, not the core
  platform.

## 7. Events produced

internal events mapped from verified callbacks (typed per their purpose),
`audit.action_recorded`, `governance.integration_bad_signature` on bad callbacks.

## 8. Ownership & evolution

- **Owner:** `director_platform` (surface), `director_backend` (adapter).
- **Evolution:** frequently-used n8n automations may be promoted to first-class
  adapters; the "external surface, no Skylize creds, cannot bypass governance"
  invariant is permanent.
