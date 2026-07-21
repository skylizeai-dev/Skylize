# Guardrails (OPA Policy)

**Status:** Subsystem specification (source of truth for policy governance)
**Owner:** Principal Architect · `director_compliance` · `chief_security_officer`
**Related:** [decision_engine.md](./decision_engine.md) · [decision_flow.md](./decision_flow.md) · [kill_switch_protocol.md](./kill_switch_protocol.md) · [../03_agents/agent_governance.md](../03_agents/agent_governance.md) · [../07_security/permissions.md](../07_security/permissions.md) · [../architecture/05_security_architecture.md](../architecture/05_security_architecture.md)

---

## 1. Purpose

Guardrails are the **declarative policies** the Decision Engine evaluates to
decide whether an action is allowed. They encode the authority/escalation matrix,
spend ceilings, brand/legal/compliance constraints, and security vetoes as
versioned, testable, auditable rules — separate from application code — using
**Open Policy Agent (OPA / Rego)**.

The principle: *authority and limits are policy, not code.* A new constraint is a
new policy version, not a code deploy; every decision names the policy version
that produced it.

## 2. Architectural role

OPA is the **policy engine behind the Decision Engine and the Governance
Authority** ([../02_architecture/tech_stack.md §5](../02_architecture/tech_stack.md#5-how-temporal--langgraph--opa-fit-reconciliation)).
Per [ADR-0004](../architecture/adr/0004-opa-production-arbiter.md), the
OPA/Rego Decision Engine (`src/skylize/decision_engine/`) is the designated
**production** governance arbiter; the inline evaluator
(`src/skylize/app/decision_engine/`) remains the development and fallback
implementation until OPA's consumer transport is rebuilt onto the live
EventBus.
It does **not** replace the cryptographic `GovernanceToken` chain of trust
([../03_agents/agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token)) —
the token authorizes *that an agent may act at all*; OPA decides *whether this
specific action is within policy*. Both must pass:

```
GovernanceToken (signature→expiry→revocation→scope→budget→delegation)  [the right to act]
                              ∩
OPA policy evaluation (authority, spend, brand/legal, safety)          [this action allowed]
                              =
                        action permitted
```

Most restrictive wins, consistent with the three-layer capability model
([agent_governance.md §5](../03_agents/agent_governance.md#5-agent-capability-model)).

## 3. Policy classes

| Class | Question it answers | Example rule |
|---|---|---|
| **authority** | Is the proposer's `authority_level` ≥ the action's required level? | a `worker` may not approve any spend |
| **spend** | Is the amount within tenant/department/campaign ceilings? | spend > tenant ceiling → defer to human |
| **external_action** | Is this a first launch / new channel / new ad account? | first external launch → HITL |
| **brand_legal** | Is the content brand/legal/compliance-sensitive? | claims flagged by brand/legal agents → HITL |
| **security_veto** | Has a security/safety agent rejected this? | a `fail_closed` security reject vetoes lower-authority approve |
| **data_access** | Is the data access cross-tenant or out-of-scope? | any cross-`org_id` access → deny + audit |

Each class is a Rego package; the Decision Engine dispatches on action class to
the matching policy.

## 4. Rule shape (illustrative Rego)

```rego
package skylize.guardrails.spend

import future.keywords.if

# default deny — fail closed
default allow := false

allow if {
    input.action.kind == "spend"
    input.action.amount <= data.ceilings[input.tenant_id][input.department]
    input.agent.authority_level != "worker"      # workers never approve spend
}

require_human if {
    input.action.kind == "spend"
    input.action.amount > data.ceilings[input.tenant_id][input.department]
}

deny[msg] if {
    input.action.kind == "spend"
    input.agent.authority_level == "worker"
    msg := "workers may not authorize spend"
}
```

- **Default deny.** Absence of an explicit allow is a denial (fail closed).
- Inputs come from the proposal: `tenant_id`, `agent` (id, authority_level,
  department), `action` (kind, amount, target), and `governance_token_id`.
- `data.*` holds tenant-configurable ceilings/config, loaded per-tenant and
  never below platform floors.

## 5. Evaluation contract

For each proposal STAGE 2 (and the capital STAGE 4) calls OPA with the proposal
input and receives `{allow, require_human, deny: [reasons], policy_version}`:

- `allow=true, require_human=false` → proceed to next stage.
- `require_human=true` → `decision.deferred_to_human`.
- `allow=false` → `decision.rejected`, with the `deny` reasons and
  `policy_version` recorded on the `decision.evaluated` record.

A **safety veto** policy can override an authority-based allow (safety is not
outranked by hierarchy), matching conflict-resolution rule 2
([agent_governance.md §11](../03_agents/agent_governance.md#11-conflict-resolution)).

## 6. Versioning, testing, and audit

> **This section describes the TARGET contract, not the current build (verified
> 2026-07-21).** None of the three bullets below is in force yet. Stated plainly so
> nobody reads a green CI run as evidence that policy is being tested:
>
> - **No Rego test exists.** `policy/` contains exactly seven `.rego` files and no
>   `*_test.rego`; no file under `policy/` contains a `test_` rule.
> - **No CI job touches OPA or Rego.** Searching `.github/` for `opa` or `rego`
>   returns zero matches. The steps in `.github/workflows/ci.yml:20-35` are ruff,
>   lint-imports, forbidden-imports, module-importability, orphan-modules, mypy and
>   pytest — there is no policy step, so nothing about a policy can block the build.
> - **No policy emits `policy_version`.** Searching `policy/` for `policy_version`
>   returns no matches, so the replay property below cannot hold today. The client
>   tolerates the absence and only logs a warning
>   (`src/skylize/decision_engine/opa_client.py:176-186`).
>
> These become true when real policy content is authored — which is gated on owner
> approval of `policy_inputs.md`. See `docs/08_operations/opa_staging_bring_up.md`.

- Policies are **versioned**; every decision records the exact `policy_version`,
  so a past decision can be re-evaluated under the policy that was in force
  (replay/compliance).
- Policies ship with **unit tests** (Rego `test_*`) run in CI; a policy that
  fails its tests or fails to load **blocks the build** (the contract gate,
  [../architecture/06_deployment_architecture.md §7](../architecture/06_deployment_architecture.md#7-cicd-pipeline)).
- Policy changes are reviewed like code (PR + approval) and are themselves audited
  as `governance.*` config events.
- Standard HITL triggers in policy map 1:1 to the `HumanInLoopTrigger` enum
  ([../03_agents/agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema)).

## 7. Ownership & evolution

- **Owner:** Principal Architect for the OPA integration; **policy ownership is
  distributed by class** — `cfo`/`director_capital_allocation` own spend ceilings,
  `chief_legal_officer`/`director_compliance` own brand_legal, `chief_security_officer`
  owns security_veto and data_access, executives own authority limits.
- **Evolution:** new action classes add a Rego package + tests; tenants tune
  `data.*` within platform floors; at Scale, OPA runs as a sidecar/bundle service
  with signed policy bundles. The default-deny posture and "policy names every
  decision" invariant never change.
