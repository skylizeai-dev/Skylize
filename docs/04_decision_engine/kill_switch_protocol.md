# Kill Switch Protocol

**Status:** Subsystem specification (source of truth for emergency stop)
**Owner:** `chief_security_officer` · Principal Architect · human owner
**Related:** [decision_engine.md](./decision_engine.md) · [guardrails.md](./guardrails.md) · [../03_agents/agent_governance.md §8](../03_agents/agent_governance.md#8-kill-switch-protocol) · [../architecture/05_security_architecture.md §9](../architecture/05_security_architecture.md#9-incident-controls) · [../08_operations/incident_response.md](../08_operations/incident_response.md)

---

## 1. Purpose

The kill switch is the **human override of last resort**: an immediate, scoped
stop that revokes all authority and halts all side effects within its scope,
overriding everything — including `executive`-level agents. It is the platform's
guarantee that no matter what an agent does, a human can stop it now. This
document is the operational protocol; the canonical definition lives in
[agent_governance.md §8](../03_agents/agent_governance.md#8-kill-switch-protocol).

## 2. Architectural role

The kill switch is enforced by the **Governance Authority** (which holds the
ECDSA P-384 key and the revocation set) and respected by the **tool proxy** and
**every integration adapter** at token-validation step 3 (revocation/live-state)
([agent_governance.md §4.3](../03_agents/agent_governance.md#43-how-agents-validate-it-before-executing)).
The Decision Engine treats kill-switch state as the **first check** in every
decision, short-circuiting all other stages
([decision_flow.md §3](./decision_flow.md#3-the-flow)). It is coarser and stronger
than the automatic circuit breaker ([agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules)).

## 3. Scopes

A kill switch is engaged at exactly one scope; broader scopes subsume narrower:

| Scope | Stops | Use |
|---|---|---|
| **agent** | one `agent_id` | a single misbehaving agent |
| **department** | a whole department/team channel | a compromised or runaway team |
| **tenant** | one `org_id` | a tenant-level incident or customer request |
| **platform** | everything | systemic emergency |

## 4. Who may engage

- A **human owner** via the control plane (authenticated at `IF-EDGE`, authorized
  as a human-owner action) — at any scope.
- The **`chief_security_officer`** agent for an automated emergency stop **within
  its authority** (e.g. agent/department scope on a confirmed high-severity
  security verdict), which itself immediately notifies a human.
- No other agent can engage a kill switch. Platform scope is human-only.

## 5. Engage protocol

```
1. Operator issues KILL(scope) via control plane  ──▶ authenticated + authorized
2. Governance Authority:
     • revokes ALL governance tokens in scope (adds token_ids to revocation set)
     • sets scope live-state = "killed"
     • stops the Orchestrator from minting new tokens for that scope
3. Tool proxy + adapters reject every action in scope
     (validation step 3 fails) ──▶ no side effects, even from in-flight agents
4. In-flight events on the bus are QUARANTINED (routed for human review), not auto-processed
5. Emit governance.kill_switch_engaged {scope, operator_identity, reason, ts}
     + full AuditEvent chain
6. Notify on-call + the affected org tree (incident opened)
```

Engagement is effective in **milliseconds** for new actions (token rejection is
local to each proxy/adapter using the cached revocation set + live-state).

## 6. While engaged

- No new tokens are minted for the scope; no agent in scope can act.
- Quarantined events are held for human triage — approved for replay, patched, or
  discarded with an audited reason
  ([../02_architecture/event_driven_architecture.md §9-10](../02_architecture/event_driven_architecture.md#9-dead-letter-queue-strategy)).
- Spend in scope is frozen ([capital_allocation.md §7](./capital_allocation.md#7-failure--safety)).
- The rest of the platform (out of scope) continues normally — isolation is the
  point.

## 7. Disengage protocol

Disengage is a **deliberate, audited human action**; the platform never
self-clears a kill switch.

```
1. Human owner reviews the incident + quarantined events
2. Human owner issues UNKILL(scope) via control plane
3. Governance Authority clears scope live-state; new mints resume
4. Quarantined events are explicitly replayed, patched, or discarded (each audited)
5. Emit governance.kill_switch_disengaged {scope, operator_identity, ts}
```

A post-incident review and (if warranted) a circuit-breaker/policy update follow
([../08_operations/incident_response.md](../08_operations/incident_response.md)).

## 8. Relationship to the circuit breaker

| | Circuit breaker | Kill switch |
|---|---|---|
| Trigger | automatic (violations/anomaly) | human (or CSO within authority) |
| Scope | per-agent / per-department | agent → department → tenant → platform |
| Clears | governance action / reinstatement | human-only, deliberate |
| Strength | suspends an agent | overrides all authority incl. executive |

The breaker is the automatic first responder; the kill switch is the human
backstop. Both revoke tokens and flip live-state; both are fully audited.

## 9. Testing & assurance

- The kill switch is **exercised in staging** as part of CI replay/smoke tests —
  a tested control, not a theoretical one.
- Drills are run periodically per the incident runbooks; mean-time-to-stop is an
  operational SLO ([../08_operations/monitoring.md](../08_operations/monitoring.md)).

## 10. Ownership & evolution

- **Owner:** `chief_security_officer` (operational authority), Principal Architect
  (mechanism), human owner (ultimate authority).
- **Evolution:** scopes and notification routing are configurable per tenant
  within platform floors; the invariants — human can always stop, kill overrides
  all authority, never self-clears, fully audited — are permanent.
