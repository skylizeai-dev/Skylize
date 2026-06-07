# Monitoring

**Status:** Operations specification (source of truth for SLOs & alerting)
**Owner:** `director_platform` · `manager_security_operations` · `director_devops`
**Related:** [observability.md](./observability.md) · [incident_response.md](./incident_response.md) · [../architecture/06_deployment_architecture.md §9](../architecture/06_deployment_architecture.md#9-observability--operations)

---

## 1. Purpose

Monitoring defines **what we measure, what good looks like (SLOs), and when we
page**. It turns the observability data ([observability.md](./observability.md))
into actionable signals so incidents are caught by alerts, not by customers.

## 2. Architectural role

Monitoring consumes OpenTelemetry metrics/traces, Langfuse LLM data, and
structured logs, plus the event bus's own governance/DLQ signals. Because every
significant transition is already an event, many SLIs are derived directly from
the event stream (e.g. DLQ rate, breaker trips, decision latency) rather than
bolted on.

## 3. Golden signals (per service & per tenant)

For each service in the [service_map](../02_architecture/service_map.md):
**latency, traffic, errors, saturation**. Tracked per `org_id` so one tenant's
problem is visible and isolable.

## 4. Platform-specific SLIs (the ones that matter here)

| SLI | Why it matters | Alert when |
|---|---|---|
| **DLQ rate** (per dept) | events failing/being dropped | > threshold over window → SEV3, page on-call |
| **Circuit-breaker trips** | agents malfunctioning/compromised | any spike / repeated trips → investigate |
| **Decision latency** | proposal → terminal outcome | p95 > SLO → SEV2/3 |
| **Token mint failures** | governance authority health | any sustained failures → SEV2 (agents can't act) |
| **Cross-tenant access denials** (`audit.access_denied`) | isolation/attack signal | any unexpected rise → SEV1 candidate |
| **Spend velocity vs. ceiling** | financial runaway | approaching/over ceiling → finance + on-call |
| **HITL queue age** | humans not approving in time | aging beyond SLA → notify approvers |
| **LLM cost / tokens** (Langfuse) | cost runaway | budget burn-rate anomaly → finance |
| **Kill-switch / replay drills** | controls actually work | drill MTTR regression → review |

## 5. SLOs (illustrative targets)

| Service / flow | SLO |
|---|---|
| Gateway availability | 99.9% |
| Decision latency (non-HITL) | p95 < 2s |
| Token mint success | 99.99% |
| Event durability (no loss) | 100% (archive-before-trim guarantee) |
| Cross-tenant isolation breaches | **0** (any breach is SEV1) |
| Mean-time-to-stop (kill switch) | < a few seconds for new actions |

Per-tenant **error budgets** are tracked; budget burn gates risky changes.

## 6. Alerting & paging

- Alerts route by severity ([incident_response.md §3](./incident_response.md#3-severity-levels)):
  SEV1 pages the owner + security; SEV2 pages on-call; SEV3 notifies; SEV4 tickets.
- **Security signals** (`audit.access_denied` spikes, prompt-injection verdicts,
  fraud flags) route to `manager_security_operations` and can auto-escalate to
  `chief_security_officer`.
- **Financial signals** route to `director_risk` / `cfo` line.
- Alerts are **symptom-based** (SLO breach) not just cause-based, to catch unknown
  failure modes.

## 7. Dashboards

- **Platform health:** golden signals per service.
- **Governance:** breaker trips, kill-switch state, token mint/revoke, HITL queue.
- **Decision/finance:** decision outcomes mix, spend vs. ceilings, ROAS trend.
- **Per-tenant:** isolated view for each `org_id` (and the tenant's own admin UI).

## 8. Ownership & evolution

- **Owner:** `director_platform` (platform SLIs/SLOs), `director_devops`
  (alerting infra), `manager_security_operations` (security signals).
- **Evolution:** OTel exporters are vendor-neutral (Prometheus/Grafana, Datadog,
  etc., interchangeable); SLOs tighten as the platform matures. The
  zero-tolerance isolation SLO and the event-loss SLO are permanent.
