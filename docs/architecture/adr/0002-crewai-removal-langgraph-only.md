# ADR 0002 — Remove CrewAI: LangGraph is the Sole Agent-Orchestration Framework

**Status:** Accepted
**Date:** 2026-07-12
**Deciders:** Principal Architect, human owner
**Supersedes:** [01_final_stack.md §4.7](../01_final_stack.md#47-orchestration--langgraph-sole-orchestration-layer) — the original "LangGraph (control plane) + CrewAI (team patterns)" orchestration decision
**Related:** [02_system_architecture.md §5](../02_system_architecture.md#5-orchestration-architecture) · [03_agent_runtime.md §3](../03_agent_runtime.md#3-execution-stack) · [../../02_architecture/tech_stack.md §5](../../02_architecture/tech_stack.md#5-how-temporal--langgraph--opa-fit-reconciliation) · [../../03_agents/agent_contract_registry.md](../../03_agents/agent_contract_registry.md) · `src/skylize/app/orchestrator/temporal/`

---

## Context

The original stack decision ([01_final_stack.md §4.7](../01_final_stack.md#47-orchestration--langgraph-sole-orchestration-layer))
paired two orchestration frameworks in the agent runtime: **LangGraph** as the
durable control plane, and **CrewAI** for role-based, intra-team collaboration
run *inside* a LangGraph node. The hybrid was chosen because CrewAI expressed
department-crew collaboration (e.g. the copy team) ergonomically, and both
frameworks sat behind the **Orchestrator** facade and the `AgentContract`
registry, so either could in principle be swapped.

Since that decision, the governance model has hardened around a
**single-orchestrator** requirement. Every agent side effect must pass through
explicit, inspectable LangGraph governance nodes (token → authority →
kill-switch), pause and resume at human-in-the-loop nodes, and land in **one**
replayable audit trail. A second orchestration framework executing inside a node
is a second execution model — with its own control flow, failure semantics, and
scheduling — running underneath the guarantees the platform depends on. That
widens two surfaces that the project is explicitly trying to keep narrow:

- **Audit / determinism surface.** Governance and compliance rest on the claim
  that a run is fully reconstructable from durable LangGraph state plus the event
  log. Collaboration happening inside an opaque second framework is not natively
  part of that record.
- **Sandbox surface.** The agent runtime is deliberately sandboxed and
  outbound-restricted; a second framework is more code and more potential egress
  inside the perimeter, for collaboration patterns that LangGraph subgraphs
  already express.

Notably, the CrewAI coupling never reached executable code — it lived only in the
architecture docs and a handful of comments. No module imports CrewAI, and it is
not a declared dependency. Removing it is therefore a documentation-and-comment
correction plus a standing rule to keep it out, not a code migration.

## Decision

**Remove CrewAI from the platform. LangGraph OSS is the sole agent-orchestration
framework.**

- Role-based, intra-team collaboration (department crews) is expressed as
  **LangGraph subgraphs / nodes**, not a separate framework. It therefore
  inherits the same durability, governance checkpoints, HITL, and audit trail as
  every other piece of control flow.
- **Durable execution sits *beneath* LangGraph** via Temporal — the
  `src/skylize/app/orchestrator/temporal/` worker (Temporal Cloud in managed
  environments). LangGraph owns orchestration and control flow; Temporal provides
  the durable-execution substrate underneath it.
- The **Orchestrator** facade and the `AgentContract` registry are unchanged.
  Agents never import an orchestration framework directly — the same isolation
  seam that ADR-era docs relied on is what makes this removal a contained change.

This ADR supersedes the orchestration decision in
[01_final_stack.md §4.7](../01_final_stack.md#47-orchestration--langgraph-sole-orchestration-layer),
which is retained (marked *Superseded*) as the historical record.

## Scope / invariants preserved

- **One orchestration model.** LangGraph only; no framework runs *inside* a node.
- **Agent contracts and the `GovernanceToken` chain of trust are unchanged** —
  the removal does not touch the contract schema or the signature scheme
  ([ADR-0001](./0001-governance-signature-scheme.md)).
- **Governance validation order** (token → authority → kill-switch), **HITL
  pause/resume**, and **replay** are unchanged; intra-team collaboration now runs
  as subgraphs under exactly those guarantees.
- **The Orchestrator / `AgentContract` seam is unchanged.** Agents still name no
  framework, so the runtime remains swappable behind the facade.

## Consequences

- All architecture docs are updated from "LangGraph + CrewAI" to LangGraph-only
  orchestration, each consistent with this ADR. Affected files:
  `architecture/01_final_stack.md` (§2 stack table, §4.1, §4.7 superseded
  banner), `architecture/02_system_architecture.md`,
  `architecture/03_agent_runtime.md`,
  `architecture/06_deployment_architecture.md`,
  `02_architecture/tech_stack.md`, `02_architecture/system_architecture.md`,
  `02_architecture/service_map.md`, `02_architecture/system_boundaries.md`,
  `01_vision/vision.md`,
  `03_agents/01_executive_board/CTO/Engineering/director_agent_infrastructure.md`,
  and `_BUILD_LOG.md`.
- **§4.7 is preserved, not deleted** — it now carries a *Superseded by ADR-0002*
  banner so the original rationale and alternatives stay on the paper trail.
- Two code comments (`src/skylize/adapters/llm/gateway.py`, `pyproject.toml`) and
  one content-data string (`scripts/agent_content.js`) that named CrewAI are
  updated. **No executable logic changes** result from this ADR.
- `docs/testing/triage_report_2026-07-12.md` is a dated triage snapshot and is
  **left as-is**: it records the forbidden-stack finding that motivated this ADR,
  and rewriting a point-in-time report would falsify the record.
- **Out of scope / follow-up:** `02_architecture/tech_stack.md §5` still reads
  "Temporal not separately run in v1", which predates the
  `orchestrator/temporal/` integration named in the Decision above. Reconciling
  that Temporal-durability wording is a separate change; this ADR is scoped to
  the removal of CrewAI and does not rewrite the Temporal narrative.

## Alternatives considered

- **Keep the LangGraph + CrewAI hybrid.** Rejected: two orchestration models
  widen the audit and sandbox surfaces and complicate the single-orchestrator
  governance guarantee, for ergonomics that LangGraph subgraphs already provide.
- **Replace LangGraph with CrewAI (CrewAI-only).** Rejected: CrewAI lacks the
  durable, checkpointed, replayable control plane that governance checkpoints and
  HITL depend on.
- **Adopt a third framework (AutoGen / bespoke engine).** Rejected: the
  single-orchestrator argument applies equally, with no benefit over LangGraph
  subgraphs and additional cost/risk.
