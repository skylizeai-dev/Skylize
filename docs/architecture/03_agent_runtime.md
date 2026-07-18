# 03 — Agent Runtime

**Status:** Production architecture (source of truth)
**Owner:** Principal Architect
**Related:** [02_system_architecture.md](./02_system_architecture.md) · [agent_governance.md](../03_agents/agent_governance.md) · [agent_contract_registry.md](../03_agents/agent_contract_registry.md) · [04_memory_architecture.md](./04_memory_architecture.md)

---

## 1. Purpose

How an agent actually executes: the sandbox, the tool proxy, the orchestration
frameworks, lifecycle, failure handling, and budget enforcement. This is the
runtime realization of the Agent Boundary (`IF-AGENT`) and the contracts in
[agent_contract_registry.md](../03_agents/agent_contract_registry.md).

---

## 2. Runtime principles

1. **Agents are untrusted.** The perimeter (tool proxy, adapters, DAL) enforces
   everything; the agent's own code is never trusted to self-police.
2. **Nothing implicit.** Capability = contract ∩ token ∩ live governance state
   (most restrictive wins, per
   [agent_governance.md §5](../03_agents/agent_governance.md#5-agent-capability-model)).
3. **Deterministic control, probabilistic reasoning.** LLM reasoning happens
   inside nodes; control flow (governance, escalation, HITL) is deterministic and
   inspectable.
4. **Everything is replayable.** Durable graph state + the event log reconstruct
   any run.

---

## 3. Execution stack

```
Orchestrator (facade)
   └─ resolves AgentContract, mints GovernanceToken, audits
        └─ LangGraph workflow (durable state machine, Postgres checkpointer)
             ├─ node: governance checkpoint (token/authority/kill-switch)
             ├─ node: agent step (single agent reasoning)
             ├─ node: subgraph (intra-team collaboration)
             ├─ node: human-in-the-loop pause (resumable)
             └─ node: decision / escalation / conflict branch
                  └─ Tool Proxy (IF-TOOL): validates token → dispatches
                       └─ Integration Adapters (IF-INTEGRATION): LLM gateway, etc.
```

- **LangGraph** owns durable control flow, checkpoints, resume, replay, and the
  governance/HITL nodes.
- **Intra-team collaboration** runs as a **LangGraph subgraph** for role-based
  team patterns, wrapped by the same control guarantees — not a separate framework.
- **Tool Proxy** is the only path from agent reasoning to a side effect.

See orchestration division of labor in
[02_system_architecture.md §5](./02_system_architecture.md#5-orchestration-architecture).

### 3.1 The LangGraph / Temporal split

Two layers, one runtime, with a clean seam between them: **LangGraph is the
orchestration graph — *what* runs and in what order; Temporal is the
durable-execution substrate the long-running units of work are meant to run on.**
They are complementary, not alternatives — LangGraph is not a substitute for
Temporal's durability, and Temporal does not orchestrate the agent graph
([ADR-0002](./adr/0002-crewai-removal-langgraph-only.md);
[../02_architecture/tech_stack.md §5](../02_architecture/tech_stack.md#5-how-temporal--langgraph--opa-fit-reconciliation)).
Temporal is a committed part of the stack — the decision of record is ADR-0002
and `temporalio>=1.7` is a hard runtime dependency
([`pyproject.toml`](../../pyproject.toml)); in managed environments the durable
substrate is **Temporal Cloud**.

> **Integration status.** The Temporal worker's activity layer is *defined* in
> code (detailed below) but is **not yet wired into the live execution path**.
> The current runtime invokes the LangGraph graph in-process —
> `build_creative_graph` compiled with an in-memory `MemorySaver`, and
> `agent_step` calling `runner.run(...)` directly — and no node dispatches to a
> Temporal activity. `pyproject.toml` lists `orchestrator.temporal.*` among the
> not-wired-into-bootstrap subsystems (a mypy override tagged "dead/paused code
> with no tracked removal or revival plan as of 2026-07-15"). This section documents the
> committed split and the code that realizes each half; the Temporal durability
> guarantees below take effect once the activity layer is wired in.

| Concern | Layer | Where in code |
|---|---|---|
| Node sequencing, conditional routing, entry/exit edges | **LangGraph** (live) | [`orchestrator/workflows/creative_workflow.py`](../../src/skylize/app/orchestrator/workflows/creative_workflow.py) — `build_creative_graph`; the `governance_checkpoint → agent_step → emit` nodes plus the `handle_failure` branch |
| Governance checkpoints (token → authority → kill-switch), HITL pause/resume gates | **LangGraph nodes** (live) | `governance_checkpoint` node driving the `validate_tool_call` pipeline |
| Retries, timeouts, crash recovery, durable persistence of each step | **Temporal** (defined; wiring pending) | [`orchestrator/temporal/activities.py`](../../src/skylize/app/orchestrator/temporal/activities.py) — the `@activity.defn` units on `WorkflowActivities` |

**LangGraph — the graph (what runs, in what order).** `build_creative_graph`
compiles an explicit `StateGraph`: which node runs next is a pure function of
state (`route_after_governance`, `route_after_agent`), and only the `agent_step`
node reasons. This is the "deterministic control, probabilistic reasoning"
principle (§2) made concrete, and it is the path the runtime executes today.

**Temporal — the durable substrate (each unit meant to survive a crash).** Each
long-running unit of work is defined as a Temporal **activity** — a method
decorated `@activity.defn` on `WorkflowActivities`, which Temporal is designed to
register, schedule, and *retry* on failure. Dependencies (`repo`, `judge`,
`minter`) are injected once at construction rather than threaded through every
call. The activity layer defines:

- **`run_judge_verification(JudgeRequest) -> JudgeVerdict`** — runs the LLM judge
  over a node's output against its `success_criteria`. With no judge wired it
  returns a verdict with `passed=False` and `raw.unverified=True` (the
  `unverified` flag lives in the verdict's `raw` payload, not as a top-level
  field) rather than silently passing — the fail-closed behavior the engine's
  gate depends on.
- **`write_run_step(StepRecordRequest) -> None`** — builds a `WorkflowRunStepRow`
  (status, input/output, judge verdict, retry count, timestamps) and persists it
  via `repo.record_step(...)`, so a run stays reconstructable from durable state.
  Note: `activities.py` imports `WorkflowRepository` and `WorkflowRunStepRow` from
  `dal/ports.py`, but those port/row definitions are **not yet present there** — a
  known gap (the module would otherwise fail to import) that must be closed as
  part of wiring the worker in.

Tenancy rides on **`RunContext`** — the per-run envelope (`org_id`, `run_id`,
`workflow_id`, `correlation_id`, `thread_id`, `triggered_by`) threaded through
every activity call, so each durable step would carry the same isolation and
audit-correlation keys as the orchestration graph above it. Activity arguments
are plain dataclasses (`JudgeRequest`, `StepRecordRequest`) because Temporal
serialises them across the worker boundary.

The intended seam: LangGraph's in-process graph decides *what* happens and
enforces governance at each edge, while Temporal is the substrate that will make
each unit of work *survive a restart* and retry deterministically once the
activity layer is wired into the graph. Neither replaces the other.

---

## 4. The agent sandbox (IF-AGENT)

Each agent runs in a restricted execution context:

- **No network egress.** Outbound is impossible except through the tool proxy.
- **No credentials.** Secrets live only in adapters at `IF-INTEGRATION`.
- **No DB driver.** Data access is via the DAL, gated by contract memory scopes.
- **Injected at start:** resolved `AgentContract`, signed `GovernanceToken`,
  scoped input (validated against `input_schema`), and a tool proxy handle.
- **Resource caps:** `max_execution_time_seconds` (wall clock) and
  `max_token_budget` (LLM tokens) from the contract, mirrored as ceilings in the
  token.

A violation (out-of-scope tool, budget overrun, timeout) aborts the action,
emits `governance.scope_violation` / `AuditEvent`, and triggers the contract
`failure_mode`. Repeated violations trip the circuit breaker
([agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules)).

---

## 5. Tool proxy (IF-TOOL)

Every tool call passes through the proxy, which validates **before** dispatch, in
the exact order defined in
[agent_governance.md §4.3](../03_agents/agent_governance.md#43-how-agents-validate-it-before-executing):

1. **Signature** — ECDSA P-384 against the Governance Authority public key.
2. **Expiry** — `now < expires_at`.
3. **Revocation / live state** — `token_id` not revoked; agent not suspended or
   kill-switched.
4. **Scope** — requested `tool_id ∈ token.scope` and `scope ⊆ contract.allowed_tools`.
5. **Budget / time** — call fits `max_token_budget`; run within
   `max_execution_time_seconds`.
6. **Delegation** — `delegation_chain` well-formed and rank-monotonic.

Any failure → deny + `AuditEvent` + `failure_mode`. No valid token ⇒ no side
effect. The proxy then dispatches to the matching adapter, normalizes the result,
and (for the LLM gateway) records cost in Langfuse keyed by `governance_token_id`.

Tool grants are declared per agent as the `allowed_tools` list of `ToolGrant`
objects (see [agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema));
a tool not in the manifest is unreachable.

---

## 6. Agent lifecycle

```
RESOLVE  → registry returns AgentContract (fail closed if unknown)
GATE     → governance: not suspended / circuit-broken / killed
VALIDATE → input parsed against input_schema
MINT     → run-scoped GovernanceToken (scope ⊆ allowed_tools), token_issued
RUN      → LangGraph node executes; tool calls via proxy; memory via DAL
            ├─ on tool/budget/scope violation → failure_mode
            ├─ on authority exceeded → decision.deferred_to_human (HITL pause)
            └─ on conflict → conflict resolution
EMIT     → output validated vs output_schema, wrapped as event, published
AUDIT    → audit.action_recorded for every step; token expires
```

Each transition is observable and durable, so a run can pause (HITL), resume, or
be replayed.

---

## 7. Failure modes

The contract's `failure_mode` (from
[agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema))
determines behavior on error or denial:

| Mode | Behavior | Typical use |
|---|---|---|
| `retry_then_escalate` | bounded retries, then escalate up `escalation_path` | directors/VPs, transient tools |
| `escalate_immediately` | no retry; escalate now | executives, high-stakes steps |
| `fail_closed` | stop; emit nothing actionable | **security** workers (deny on doubt) |
| `fallback_degraded` | return a reduced/safe result | creative workers (graceful degradation) |

Security agents (e.g. `fraud_detection_agent`) `fail_closed`; creative workers
(e.g. `hook_generator_agent`) `fallback_degraded` — matching the example
contracts in the registry.

---

## 8. Escalation, HITL, and conflict at runtime

- **Escalation:** when required authority exceeds the agent's `authority_level`,
  the Decision Engine routes the proposal to the next entry in `escalation_path`,
  emitting `governance.human_escalation_raised`. The chain walks the org tree to
  `human_owner`.
- **Human-in-the-loop:** any matching `human_in_loop_triggers` pauses the
  LangGraph workflow at a durable HITL node (`decision.deferred_to_human`) until a
  human approves, modifies, or rejects — each outcome audited.
- **Conflict:** two agents with overlapping mandates producing incompatible
  outputs on the same `partition_key` trigger `decision.conflict_detected`,
  resolved by the deterministic rule order (authority precedence → safety veto →
  policy → escalate) in
  [agent_governance.md §11](../03_agents/agent_governance.md#11-conflict-resolution).

---

## 9. Concurrency & scaling

- Workflows are stateless between checkpoints; LangGraph state lives in Postgres,
  so any worker process can resume any paused graph (horizontal scale).
- Event consumers use Redis Streams consumer groups; a `partition_key` is pinned
  to a consumer via consistent hashing to preserve per-key ordering under scale
  (see [event_driven_architecture.md §8](../02_architecture/event_driven_architecture.md#8-ordering-guarantees)).
- Per-tenant and per-agent rate/budget limits prevent one tenant or runaway agent
  from starving others.

---

## 10. Framework migration path

LangGraph sits behind the Orchestrator facade and the contract
registry. To replace the orchestration framework:
1. Keep `AgentContract`s and the `GovernanceToken` unchanged.
2. Reimplement the facade's node execution for affected agents on the new
   framework.
3. Migrate department by department; contracts, events, governance, and audit are
   untouched.

This isolation is the reason no agent imports a framework directly — the runtime
is swappable without touching the agent layer.
