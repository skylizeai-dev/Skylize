# Agent Governance

**Status:** Foundation document (source of truth)
**Owner:** Chief Systems Architect
**Related:** [agent_contract_registry.md](./agent_contract_registry.md) · [system_boundaries.md](../02_architecture/system_boundaries.md) · [event_driven_architecture.md](../02_architecture/event_driven_architecture.md)

> **CONSISTENCY CONTRACT.** This document and
> [agent_contract_registry.md](./agent_contract_registry.md) use **identical**
> authority level names — `executive`, `vp`, `director`, `manager`, `worker` —
> and the **identical** governance token definition in §4. The `escalation_path`
> field of every agent contract is the concrete realization of the escalation
> concept defined in §3 here.

---

## 1. Purpose

Governance is the set of rules that make a multi-agent digital company **safe,
accountable, and overridable**. It answers: who may decide what, what they must
escalate, how an agent proves it is allowed to act, how a misbehaving agent is
stopped, how a human takes control, what must be recorded, and how conflicts are
resolved.

Governance is enforced at the Application Boundary by the **Governance
Authority** and at the Agent/Integration boundaries by the **tool proxy** and
**adapters** (see
[system_boundaries.md §5](../02_architecture/system_boundaries.md#5-boundary-enforcement-mechanisms)).
Agents themselves are untrusted; the perimeter enforces governance.

---

## 2. Authority Hierarchy

Five authority levels, top to bottom. **These names are canonical** and reused
verbatim in the contract registry's `authority_level` field.

| Level | Org examples | Scope of mandate |
|---|---|---|
| `executive` | `ceo`, `cmo`, `cfo`, `coo`, `cto`, `cso`, `chief_security_officer` | Company-wide strategy, budgets, cross-department arbitration |
| `vp` | `vp_creative`, `vp_marketing`, `vp_engineering`, `vp_sales` | A function within a C-suite domain |
| `director` | `copy_director`, `art_director`, `director_growth`, `director_cybersecurity` | A department/team; owns a workflow |
| `manager` | `creative_operations_manager`, `manager_incident_response` | A team of workers; routing & QA |
| `worker` | `hook_generator_agent`, `fraud_detection_agent`, `image_generation_agent` | A single bounded task |

Authority flows **down** as delegation and **up** as escalation. A higher level
may delegate a *subset* of its authority to a lower level via the
`delegation_chain` in the governance token (§4); it may never delegate authority
it does not itself hold.

---

## 3. Authority & Escalation

### 3.1 What each level may decide autonomously vs. must escalate

The Decision Engine (see
[event_driven_architecture.md §7](../02_architecture/event_driven_architecture.md#7-the-decision-engine))
checks the originating agent's `authority_level` against the action's required
level. If the agent's level is sufficient → autonomous. Otherwise → escalate.

| Level | May decide autonomously | Must escalate |
|---|---|---|
| `worker` | Produce its bounded artifact (a hook, a score, a draft, a detection); read granted memory; call its allowed tools within budget | Any spend; any external publish/launch; anything outside its single task; any low-confidence security verdict requiring action |
| `manager` | Accept/route/reject worker outputs; request rework; approve artifacts within a small pre-set quality threshold | Budget changes; external publishing; cross-team coordination; policy exceptions |
| `director` | Approve a workflow's outputs for its department; allocate within a delegated budget cap; launch internal-only actions | Spend above delegated cap; brand/legal-sensitive launches; cross-department dependencies; org policy changes |
| `vp` | Approve department strategy; reallocate budget within a function; approve external launches within risk policy | Cross-function trade-offs; budget above function cap; anything triggering legal/compliance/security review |
| `executive` | Company strategy; cross-department arbitration; top-level budget; declare org-wide policy | Actions the platform policy reserves for a **human owner** (see §7) — e.g. legal commitments, irreversible spend above the human-approval ceiling |

**Escalation = `escalation_path`.** Every agent contract declares an
`escalation_path` (an ordered list of `agent_id`s ending at a human role). When
an agent hits a decision above its authority, or its `failure_mode` requires
escalation, the Decision Engine emits `governance.human_escalation_raised` /
routes the proposal to the next entry in `escalation_path`. The chain mirrors the
org tree: `worker → manager → director → vp → executive → human_owner`. This is
exactly the field defined in
[agent_contract_registry.md §2](./agent_contract_registry.md#2-the-contract-schema).

---

## 4. Governance Token

> This definition is **identical** to
> [agent_contract_registry.md §3](./agent_contract_registry.md#3-governance-token)
> and to the summary in
> [system_boundaries.md §5.3](../02_architecture/system_boundaries.md#53-signed-governance-tokens-per-agent-application--agent--integration).
> It is the single root of an agent's right to act.

### 4.1 Contents

```python
from __future__ import annotations
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class GovernanceToken(BaseModel):
    """The signed proof of an agent's authority to act. Minted ONLY by the
    Governance Authority (Application Boundary). Validated by the tool proxy
    and every integration adapter before any side-effecting action."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    token_id: UUID                                   # unique; used for revocation
    agent_id: str                                    # who this token is for
    authority_level: Literal[                        # CANONICAL set
        "executive", "vp", "director", "manager", "worker"
    ]
    department: str

    # Delegation: ordered chain of agent_ids from the root authority down to
    # this agent. Proves the authority was legitimately delegated, never forged.
    delegation_chain: list[str]

    # Scope: the concrete actions/tools this token authorizes (subset of the
    # agent contract's allowed_tools, narrowed for this invocation).
    scope: list[str]

    # Budget ceilings enforced at the tool proxy / adapters
    max_token_budget: int                            # LLM tokens for this run
    max_execution_time_seconds: int

    # Validity window
    issued_at: datetime
    expires_at: datetime                             # short-lived (minutes)

    nonce: str                                       # anti-replay
    signature: str                                   # ECDSA P-384 over the
                                                     # canonical serialization of all above
```

### 4.2 How it is signed

> Signature scheme is **ECDSA P-384** ([ADR 0001](../architecture/adr/0001-governance-signature-scheme.md)).

- The **Governance Authority** holds a root **ECDSA P-384** private key in the
  secrets manager. Only it can mint or revoke tokens.
- The canonical JSON serialization of every field except `signature` is signed;
  `signature` is the ECDSA P-384 signature, base64url-encoded.
- Tokens are **short-lived** (minutes, bounded by `expires_at`) and
  **single-run** scoped. The matching public key is distributed to the tool proxy
  and adapters for verification.
- Every mint emits `governance.token_issued`; every revoke emits
  `governance.token_revoked` and adds `token_id` to the revocation set.

### 4.3 How agents validate it before executing

An agent does not validate its own token (it is untrusted). The **tool proxy**
and **integration adapters** validate it before any side effect, in order:

1. **Signature** verifies against the Governance Authority public key. (Fail →
   forged/tampered → deny.)
2. **Expiry**: `now < expires_at`. (Fail → expired → deny, require re-mint.)
3. **Revocation**: `token_id` not in the revocation set, and the agent is not
   suspended / kill-switched (§6, §7). (Fail → deny.)
4. **Scope**: the requested tool/action ∈ `scope`, and `scope ⊆` the agent
   contract's `allowed_tools`. (Fail → `governance.scope_violation`.)
5. **Budget/time**: the call fits `max_token_budget` and the run is within
   `max_execution_time_seconds`. (Fail → deny + `failure_mode`.)
6. **Delegation**: `delegation_chain` is well-formed and each link's authority
   ≥ the next. (Fail → deny.)

Any failure aborts the action, emits an `AuditEvent`, and triggers the agent's
contract `failure_mode`. No valid token ⇒ no side effect, ever.

---

## 5. Agent Capability Model

What *any* agent can do is the intersection of three layers; the most
restrictive wins:

1. **Contract** (static) — `allowed_tools`, `memory_read_access`,
   `memory_write_access`, budgets (see
   [agent_contract_registry.md §2](./agent_contract_registry.md#2-the-contract-schema)).
2. **Governance token** (per-run) — `scope`, budget ceilings, validity.
3. **Live governance state** — circuit breaker, suspension, kill switch (§6–7).

Concretely, per verb:

- **Read** — only memory namespaces in `memory_read_access`; only tenant-scoped
  data via the DAL (`IF-DATA`). No raw DB access.
- **Write** — only memory namespaces in `memory_write_access`; business writes
  only by emitting events the Decision Engine persists. No direct DB/S3 writes.
- **Execute** — only tools in `allowed_tools` ∩ token `scope`, via the tool proxy.
- **Call** — only the integration adapters reachable through allowed tools; never
  the external network directly (see
  [system_boundaries.md §4.3](../02_architecture/system_boundaries.md#43-agent-boundary--interface-if-agent)).

---

## 6. Tool Manifest Standard

Each agent **declares**, never assumes, its tools. The manifest is the
`allowed_tools` field of its contract and is the allow-list the tool proxy
enforces.

```python
class ToolGrant(BaseModel):
    tool_id: str                       # e.g. "llm.generate", "qdrant.search"
    purpose: str                       # why this agent needs it (audited)
    max_calls_per_run: int | None = None
    requires_governance_token: bool = True

class ToolManifest(BaseModel):
    agent_id: str
    tools: list[ToolGrant]
```

Rules:
- A tool not in the manifest is unreachable — calling it is a
  `governance.scope_violation`.
- Each `ToolGrant.purpose` is recorded so audits can answer "why did this agent
  have this capability".
- The governance token's `scope` may *narrow* the manifest for a given run but
  can never *widen* it.

---

## 7. Circuit Breaker Rules

A circuit breaker auto-suspends an agent when its behavior indicates malfunction
or compromise, before a human is even notified.

**Trip conditions (any one):**
- ≥ N `governance.scope_violation` events within a rolling window.
- `max_execution_time_seconds` exceeded repeatedly (runaway loops).
- `max_token_budget` overshoot attempts (cost runaway).
- Output schema validation failures above a threshold (broken/poisoned output).
- A security agent (e.g. `prompt_injection_agent`, `fraud_detection_agent`)
  flags the agent's output as anomalous.
- Error rate / DLQ rate for the agent's events crosses an SLO threshold.

**On trip:**
1. Governance Authority revokes the agent's active tokens
   (`governance.token_revoked`) and marks it `suspended`.
2. Emits `governance.circuit_breaker_tripped` + `governance.agent_suspended`.
3. The agent's in-flight work follows its `failure_mode`; pending proposals are
   escalated along `escalation_path`.
4. The agent's manager/director is notified; a human review is opened.

**Reinstatement** requires an explicit governance action (human or sufficiently
authorized executive), emitting `governance.agent_reinstated`. Breakers are
per-agent and, for systemic faults, per-department.

---

## 8. Kill Switch Protocol

The kill switch is the human override of last resort. It is **coarser and
stronger** than a circuit breaker.

**Scopes:** single agent · whole department/team · whole tenant · entire
platform.

**Who can engage:** a human owner via the control plane, or the
`chief_security_officer` agent for an automated emergency stop within its
authority (which itself notifies a human).

**Protocol:**
1. Operator issues a kill command at a chosen scope through the control plane
   (authenticated at `IF-EDGE`, authorized as a human-owner action).
2. Governance Authority immediately: revokes all governance tokens in scope,
   sets the scope state to `killed`, and stops the Orchestrator from minting new
   tokens for that scope.
3. The tool proxy and adapters reject every action in scope (validation step 3
   in §4.3 fails), so no side effects can occur even from in-flight agents.
4. Emits `governance.kill_switch_engaged` with scope and operator identity; a
   full `AuditEvent` chain is written.
5. In-flight events already on the bus are quarantined (routed for human review),
   not auto-processed.

**Disengage** is a deliberate, audited human action; the platform never
self-clears a kill switch. Kill-switch state overrides everything, including
`executive`-level agents.

---

## 9. Human-in-the-Loop Trigger Conditions

An action pauses for human approval (`decision.deferred_to_human`) when any
contract `human_in_loop_triggers` matches. Standard triggers across the platform:

- Spend or budget change above a tenant-configured ceiling.
- First external publish/launch on a new channel or ad account.
- Brand-, legal-, or compliance-sensitive content (flagged by brand/legal
  agents).
- Any action whose required authority exceeds the acting agent's level (§3).
- Security verdicts above a severity threshold (e.g. confirmed fraud, suspected
  prompt injection with action impact).
- Low model confidence on an irreversible action.
- Anything explicitly enumerated in the agent's `human_in_loop_triggers`.

On trigger: the Decision Engine emits `decision.deferred_to_human` +
`governance.human_escalation_raised`, routes to the human at the end of
`escalation_path`, and holds the action until approved, modified, or rejected —
each outcome audited.

---

## 10. Audit Log Requirements

Every agent action **must** record an `AuditEvent`
(see [event taxonomy](../02_architecture/event_driven_architecture.md#5-event-taxonomy)).
At minimum, each record carries:

- `event_id`, `correlation_id`, `causation_id` (traceability).
- `tenant_id`, `department`, `source_agent_id`, `authority_level`.
- `governance_token_id` (which token authorized the action).
- The **action**: tool/event type, inputs hash, outputs hash (PII-safe),
  decision outcome.
- **Result**: success / denied / escalated / failed, with reason.
- `occurred_at` and processing latency.

Audit logs are append-only and immutable (object-lock cold storage, §11 of the
event doc). They are sufficient to reconstruct, for any tenant and window: who
acted, under what authority, with what token, on what input, with what result,
and why — satisfying the compliance/replay invariant in
[event_driven_architecture.md §10](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance).

---

## 11. Conflict Resolution

When two agents with overlapping mandates produce conflicting outputs (e.g.
`copy_director` approves copy that `brand_guardian_agent` rejects; two growth
agents propose contradictory budget reallocations), the Decision Engine detects
it and resolves deterministically.

**Detection:** two proposals with the same `partition_key` (e.g.
`campaign:{id}`) and incompatible terminal intents → `decision.conflict_detected`.

**Resolution order (first applicable wins):**
1. **Authority precedence** — the proposal from the higher `authority_level`
   prevails (executive > vp > director > manager > worker).
2. **Safety veto** — a security/brand/legal/compliance agent's *reject* vetoes a
   peer or lower-authority *approve*, regardless of level ordering. (Safety is
   not outvoted by hierarchy.)
3. **Explicit policy** — a registered Decision Engine policy for that conflict
   class (e.g. "budget conflicts resolve to the lower spend pending review").
4. **Same level, no policy** — escalate to the nearest common ancestor in
   `escalation_path` (the shared manager/director/vp), i.e. human-in-the-loop if
   needed.

The Decision Engine then emits `decision.conflict_resolved` naming the winning
proposal and the rule applied, with a full `AuditEvent`. Conflicts are never
silently coin-flipped; the resolution rule is always recorded.

---

## 12. Invariants (must always hold)

1. Authority level names are exactly `executive`, `vp`, `director`, `manager`,
   `worker` — everywhere, including
   [agent_contract_registry.md](./agent_contract_registry.md).
2. No agent acts without a valid, in-scope, unexpired, unrevoked governance token
   (§4).
3. An agent's effective capability is the *intersection* of contract, token, and
   live governance state — most restrictive wins.
4. Every action is auditable and reconstructable (§10).
5. Kill-switch and suspension state override all authority, including executive.
6. Conflicts resolve by a recorded deterministic rule, with safety vetoes
   outranking hierarchy.
