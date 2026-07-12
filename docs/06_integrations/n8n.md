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

n8n sits **outside** Skylize's boundaries. The implemented direction is
**n8n → Skylize**: n8n workflow nodes call two edge endpoints as steps inside
their own workflows.

- **n8n holds no Skylize credentials and cannot read internal data** — it only
  receives the scoped payload it is sent
  ([../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration)).

### 2.1 Inbound endpoints (n8n → Skylize)

**`GET /api/v1/agent-prompts/{agent_id}`**
([agent_prompts.py](../../src/skylize/edge/routes/agent_prompts.py)) — called by an
n8n LLM node before every agent LLM call, to fetch that agent's current system
prompt and metadata (authority level, model tier). Auth: static API key in the
`X-Skylize-API-Key` header, checked against `SKYLIZE_N8N_API_KEY`. Fails closed
(`503`) if the key is unconfigured; `401` on a missing/wrong key; `404` if
`agent_id` isn't registered.

**`POST /api/v1/knowledge/ingest`**
([knowledge.py](../../src/skylize/edge/routes/knowledge.py)) — called by an n8n
HTTP Request node to push a document (`doc_id`, `content`, `source_path`) into
platform knowledge ingestion. Auth: HMAC-SHA256 body signature in the
`X-Hub-Signature-256` header (`sha256=<hex>`), verified against
`SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET` with a constant-time comparison. Fails closed
(`503`) if the secret is unconfigured; `401` on a bad/missing signature; `202`
with `{"status": "accepted", "doc_id": ...}` on success.

### 2.2 Outbound direction (Skylize → n8n) — aspirational, not yet implemented

The rest of this document (§3–§7) also describes a **Skylize → n8n trigger**
adapter and a **signed n8n → edge callback** path (`IF-INTEGRATION`, "Skylize
adapter" in the diagram below). As of this revision, no such outbound adapter
exists in `src/` — grepping the codebase for `n8n` only turns up the two
inbound routes above and their config keys
([config.py](../../src/skylize/config.py)). Treat the trigger/callback design
below as the target design for that future adapter, not a description of
current behavior.

```
[target design, not yet built]
Skylize adapter ──signed trigger payload──▶ n8n workflow ──signed callback──▶ edge (verify) ──▶ IF-EVENT
```

## 3. Authentication & secrets

> §3–§7 describe the target design, mixing the two *implemented* inbound
> endpoints (§2.1) with the *unimplemented* outbound trigger/callback (§2.2).
> As implemented today, the two directions use different mechanisms: the
> agent-prompts endpoint is a static API key, not HMAC; only knowledge/ingest
> uses HMAC. There is no outbound trigger adapter, so there is no "both
> directions" yet.

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
