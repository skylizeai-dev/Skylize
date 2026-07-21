# Permissions & Authorization Model

**Status:** Security specification (source of truth for authorization)
**Owner:** `chief_security_officer` · `director_identity_access` · Principal Architect
**Related:** [../architecture/05_security_architecture.md](../architecture/05_security_architecture.md) · [../03_agents/agent_governance.md](../03_agents/agent_governance.md) · [../03_agents/agent_contract_registry.md](../03_agents/agent_contract_registry.md) · [../04_decision_engine/guardrails.md](../04_decision_engine/guardrails.md)

---

## 1. Purpose

This document defines **who and what may do what** in Skylize: the human RBAC
model, the agent capability model, the governance-token authorization, and how
the three combine. It is the operational permissions reference behind the
consolidated [../architecture/05_security_architecture.md](../architecture/05_security_architecture.md).

## 2. Architectural role

Authorization is **three independent layers; the most restrictive wins**
([agent_governance.md §5](../03_agents/agent_governance.md#5-agent-capability-model)).
A request is authorized only if it passes all three:

```
1. Human RBAC        — may this user/tenant request this at all?     (edge/service)
2. Agent contract    — static capability of the acting agent          (registry)
3. Governance token  — per-run narrowing + live governance state      (proxy/adapters)
       ∩  evaluated against guardrails (this specific action)          (decision engine)
       =  permitted
```

> **Status of the guardrail step (verified 2026-07-21).** That fourth line is
> enforced today by **hard-coded Python**, not by OPA. The wired engine's policy
> stage is `src/skylize/app/decision_engine/evaluator.py:216`, described in its own
> docstring as "the MVP stand-in for OPA Rego"; it holds no OPA client and makes no
> HTTP call. OPA becomes the arbiter only when `SKYLIZE_DECISION_ENGINE` is flipped
> to `opa` (`src/skylize/config.py:107`, default `"inline"`), which is blocked on
> owner-approved policy content and a live server — see
> `docs/08_operations/opa_staging_bring_up.md`.

## 3. Layer 1 — Human RBAC

- Humans authenticate via the OIDC IdP; the edge derives a short-lived signed
  `RequestContext{org_id, user_id, roles}` (≤5 min TTL). Internal services trust
  the `RequestContext`, never the raw IdP token
  ([../architecture/05_security_architecture.md §3](../architecture/05_security_architecture.md#3-identity--authentication)).
- **Roles** gate what a human may request and approve:

| Role | May do |
|---|---|
| `owner` | everything in the tenant incl. kill switch, ceilings, HITL approvals |
| `admin` | configure agents/budgets within owner-set floors; approve most HITL |
| `operator` | run workflows; approve routine HITL; view audit |
| `analyst` | read dashboards, audit, memory (read-only) |
| `viewer` | read-only dashboards |

- Roles are tenant-scoped; a user in one tenant has no roles in another.
- HITL approvals are authorized by role: high-severity/irreversible approvals
  require `owner`/`admin`.

## 4. Layer 2 — Agent contract (static capability)

Every agent's static permissions are its `AgentContract`
([agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema)):
`authority_level`, `allowed_tools` (the tool manifest), `memory_read_access` /
`memory_write_access`, budgets, `escalation_path`, `failure_mode`,
`human_in_loop_triggers`. If it is not in the contract, the agent cannot do it.
Contracts are versioned, validated on load, and fail closed if unknown/invalid.

## 5. Layer 3 — Governance token (per-run)

The `GovernanceToken` narrows the contract for one run and carries live state
([agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token)).
Validated before any side effect in the canonical order:

**signature → expiry → revocation → scope → budget → delegation**

`scope ⊆ contract.allowed_tools` always — the token narrows, never widens. No
valid token ⇒ no side effect, ever.

## 6. The permission matrix (authority × action)

Canonical, matching [agent_governance.md §3](../03_agents/agent_governance.md#3-authority--escalation):

| Action class | worker | manager | director | vp | executive | human |
|---|---|---|---|---|---|---|
| Produce bounded artifact | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Read granted memory | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Approve worker output | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| Allocate within delegated cap | — | small | ✅ | ✅ | ✅ | ✅ |
| External publish/launch | — | — | internal only | ✅(in policy) | ✅ | ✅ |
| Raise a budget ceiling | — | — | — | up to fn cap | top-level | ✅ |
| Legal/irreversible commitment | — | — | — | — | escalate | ✅ |
| Engage kill switch | — | — | — | — | CSO (in authority) | ✅ |

Anything above an agent's row escalates via `escalation_path`; anything matching a
`human_in_loop_trigger` defers to a human.

## 7. Data & memory permissions

- **Tenant isolation** at `IF-DATA` is independent of upstream checks: Postgres
  RLS by `org_id`, Qdrant payload filter, S3 prefix, `org_id` required on every
  Memory call ([../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth)).
- **Memory namespaces** gate agent reads/writes; out-of-scope access →
  `audit.access_denied` and a fraud-detection signal.
- **Secrets** are readable only by adapters and the DAL — never agents, never the
  agent runtime ([../architecture/05_security_architecture.md §7](../architecture/05_security_architecture.md#7-secrets-management)).

## 8. Enforcement points (defense in depth)

| Check | Where |
|---|---|
| Human RBAC | edge + service layer |
| Webhook signature | edge |
| Contract resolution / authority | orchestrator + decision engine |
| Token validation | tool proxy + every adapter |
| Guardrail evaluation (inline Python today; OPA once the flag flips) | decision engine |
| Tenant isolation | DAL (RLS/namespacing) |
| Kill switch / suspension | governance authority, respected everywhere |

A bypass at one layer is still caught at the next.

## 9. Ownership & evolution

- **Owner:** `chief_security_officer` (model), `director_identity_access`
  (RBAC/IdP integration), Principal Architect (enforcement architecture).
- **Evolution:** roles and ceilings are tenant-configurable within platform
  floors; at Scale, enterprise tenants may add SSO group→role mapping and
  dedicated isolation. The three-layer / most-restrictive-wins invariant and the
  token validation order are permanent.
