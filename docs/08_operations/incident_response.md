# Incident Response

**Status:** Operations runbook (source of truth for incident handling)
**Owner:** `manager_incident_response` · `chief_security_officer` · `director_platform`
**Related:** [monitoring.md](./monitoring.md) · [observability.md](./observability.md) · [../04_decision_engine/kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md) · [../architecture/05_security_architecture.md §9](../architecture/05_security_architecture.md#9-incident-controls) · [../architecture/06_deployment_architecture.md §10](../architecture/06_deployment_architecture.md#10-disaster-recovery)

---

## 1. Purpose

This runbook defines how Skylize **detects, contains, eradicates, recovers from,
and learns from incidents** — whether a runaway agent, a security event, a
data-isolation concern, or an infrastructure failure. The platform is built so
that the *primary* containment tools (circuit breaker, kill switch) and the
*primary* recovery tool (event replay) are first-class, tested capabilities.

## 2. Architectural role

Incident response sits on top of the platform's built-in controls:
- **Detection** comes from monitoring/observability ([monitoring.md](./monitoring.md), [observability.md](./observability.md)).
- **Automatic containment** is the **circuit breaker** ([agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules)).
- **Human containment** is the **kill switch** ([kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md)).
- **Recovery** is **replay** of the immutable event log ([../architecture/06_deployment_architecture.md §10](../architecture/06_deployment_architecture.md#10-disaster-recovery)).

## 3. Severity levels

| Sev | Definition | Example | Response |
|---|---|---|---|
| **SEV1** | platform-wide / cross-tenant data risk / safety | suspected cross-tenant leak, prompt-injection with action impact | page owner; kill switch ready; war room |
| **SEV2** | single-tenant or department outage / runaway | runaway department, decision-latency SLO breach | page on-call; scoped kill switch likely |
| **SEV3** | degraded, contained | elevated DLQ rate, one agent circuit-broken | on-call investigates; no customer impact |
| **SEV4** | minor / cosmetic | dashboard glitch | ticket |

## 4. Lifecycle

```
DETECT   ──▶ alert fires (SLO breach, breaker trip, security verdict, anomaly)
TRIAGE   ──▶ on-call assigns severity, opens incident, identifies blast radius (replay/trace)
CONTAIN  ──▶ automatic: circuit breaker already suspended the agent
             human: kill switch at the tightest sufficient scope (agent/dept/tenant)
ERADICATE──▶ find root cause via OTel trace (correlation_id) + audit replay
RECOVER  ──▶ replay/repair: rebuild state from event log; reinstate agents; disengage kill switch
LEARN    ──▶ blameless postmortem → org:lessons; policy/breaker/runbook updates
```

## 5. Containment decision

| Situation | Action |
|---|---|
| One agent misbehaving, already breaker-tripped | verify, investigate; reinstate after fix |
| One agent misbehaving, breaker not tripped | engage **agent-scope** kill switch |
| A whole team/department compromised or runaway | **department-scope** kill switch |
| Tenant-level incident or customer request | **tenant-scope** kill switch |
| Systemic / cross-tenant safety risk (SEV1) | **platform-scope** kill switch; page owner |

Always engage at the **tightest sufficient scope** to preserve isolation —
unaffected scopes keep running ([kill_switch_protocol.md §6](../04_decision_engine/kill_switch_protocol.md#6-while-engaged)).

## 6. Blast-radius analysis (replay)

Because every action is an immutable audited event tied by `correlation_id`, an
incident's blast radius is **computed, not guessed**: replay the affected
`correlation_id`s / `partition_key`s / time window in **shadow mode** to see
exactly which decisions, spends, and external actions occurred, under which tokens,
by which agents ([../02_architecture/event_driven_architecture.md §10](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance)).

## 7. Recovery patterns

| Failure | Recovery |
|---|---|
| App node loss | replicas/HPA reschedule (stateless between checkpoints) |
| Postgres loss | PITR restore; LangGraph + canonical memory recovered |
| Redis loss | AOF restore; gaps rebuilt from S3 archive replay |
| Qdrant loss | rebuild index from Postgres + `memory.*` replay |
| Compromised agent/dept | breaker/kill switch isolates; replay audits blast radius |
| Bad decision/spend | reverse via adapter where possible; record + escalate; tighten policy |

## 8. Communication & roles

- **Incident commander:** `manager_incident_response` (or on-call human).
- **Security lead (SEV1/2 security):** `chief_security_officer`.
- **Platform lead:** `director_platform`.
- **Customer comms:** owner/admin per tenant contract.
- Status is tracked in the incident record; all containment actions are audited
  `governance.*` events.

## 9. Postmortem

Every SEV1/SEV2 gets a **blameless postmortem** written to `org:lessons`
([../05_memory/organizational_memory.md §3](../05_memory/organizational_memory.md#3-what-it-stores)),
with timeline (reconstructed from the audit log), root cause, blast radius,
remediation, and follow-up actions (policy/breaker thresholds/runbook/test gaps).

## 10. Ownership & evolution

- **Owner:** `manager_incident_response` (process), `chief_security_officer`
  (security incidents), `director_platform` (infra incidents).
- **Evolution:** runbooks and breaker/alert thresholds tighten from postmortems;
  kill-switch and DR drills run periodically in staging and are themselves SLO'd
  ([monitoring.md](./monitoring.md)). The "tightest sufficient scope" and
  "replay to compute blast radius" practices are permanent.
