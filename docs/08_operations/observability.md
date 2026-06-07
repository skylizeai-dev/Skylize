# Observability

**Status:** Operations specification (source of truth for telemetry)
**Owner:** `director_platform` · `director_devops` · `chief_data_officer`
**Related:** [monitoring.md](./monitoring.md) · [incident_response.md](./incident_response.md) · [../02_architecture/event_driven_architecture.md §12](../02_architecture/event_driven_architecture.md#12-observability) · [../architecture/06_deployment_architecture.md §9](../architecture/06_deployment_architecture.md#9-observability--operations)

---

## 1. Purpose

Observability defines **how we see inside the system**: the traces, metrics,
logs, and LLM telemetry that make every request, decision, and agent action
inspectable after the fact. Where [monitoring.md](./monitoring.md) defines *what
we alert on*, this defines *the instrumentation that produces the data*.

## 2. Architectural role

Skylize is observable **by construction**: the event log is already a complete,
ordered, replayable record of everything that happened, and every hop is
instrumented. The three pillars plus replay give four complementary lenses:

```
OpenTelemetry  — distributed traces + metrics (edge → decision), keyed by correlation_id
Langfuse       — LLM call cost/quality, keyed by governance_token_id
Structured logs— per-hop envelope (minus PII)
Event replay   — authoritative reconstruction from the immutable log
```

## 3. Tracing (OpenTelemetry)

- A single **trace per workflow**, keyed by `correlation_id`, spans edge →
  orchestrator → agents → tool proxy → adapters → decision engine. One user
  request is one trace, even across async event hops
  ([../02_architecture/event_driven_architecture.md §12](../02_architecture/event_driven_architecture.md#12-observability)).
- Spans carry `correlation_id`, `event_id`, `causation_id`, `org_id`,
  `source_agent_id`, `authority_level`, and `governance_token_id` — so a span is
  always attributable to a tenant, agent, and authorizing token.
- `causation_id`/`correlation_id` reconstruct cross-partition causality that
  wall-clock ordering cannot ([../02_architecture/event_driven_architecture.md §8](../02_architecture/event_driven_architecture.md#8-ordering-guarantees)).

## 4. LLM observability (Langfuse)

- Every LLM call through the gateway is recorded in Langfuse **keyed by
  `governance_token_id`**, linking model cost and quality back to the exact agent
  and event that triggered it.
- Enables: per-agent/per-tenant cost attribution, budget burn-rate, quality
  regression detection, and prompt/version tracking. Cost data feeds the finance
  and capital-allocation signals ([../04_decision_engine/capital_allocation.md](../04_decision_engine/capital_allocation.md)).

## 5. Logging

- **Structured** (JSON) logs per hop, containing the event envelope **minus
  payload PII**. PII is referenced by hash, never logged in clear
  ([../architecture/04_memory_architecture.md §7](../architecture/04_memory_architecture.md#7-write-path-commit)).
- Logs are correlated to traces by `correlation_id`/`event_id`.
- Audit is **not** "just logs": `audit.*` events are an immutable, object-locked
  stream with a 7-year floor — the compliance spine, distinct from operational
  logs ([../02_architecture/event_driven_architecture.md §11](../02_architecture/event_driven_architecture.md#11-retention-policy-per-event-type)).

## 6. Replay as observability

The strongest lens: **shadow replay** of any `tenant_id` / `correlation_id` /
`partition_key` / time window into an isolated consumer reproduces exactly what
happened — which agent did what, under which token, and why a decision was made —
without touching production state. This is the basis of debugging, blast-radius
analysis, and compliance answers
([../02_architecture/event_driven_architecture.md §10](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance)).

## 7. Tenant scoping

All telemetry carries `org_id`; dashboards and queries are tenant-scoped, and a
tenant's admin sees only its own data. Telemetry never bridges tenants
([../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth)).

## 8. Vendor neutrality

OTel is the instrumentation standard; exporters point at Tempo/Jaeger/Grafana/
Datadog interchangeably — no proprietary agent is baked into the code
([../architecture/01_final_stack.md §4.11](../architecture/01_final_stack.md#411-observability--opentelemetry--langfuse--structured-logs)).

## 9. Ownership & evolution

- **Owner:** `director_platform` (tracing/metrics), `director_devops` (pipelines/
  exporters), `chief_data_officer` (telemetry governance & PII rules).
- **Evolution:** instrumentation is required on every new hop (the contract gate
  helps enforce span/log presence); at Scale, exporters and backends scale
  independently. The "observable by construction / replay is authoritative"
  property is permanent.
