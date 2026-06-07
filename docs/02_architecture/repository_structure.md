# Repository Structure

**Status:** Architecture reference (source of truth for layout)
**Owner:** Principal Architect · VP Engineering
**Related:** [system_boundaries.md](./system_boundaries.md) · [service_map.md](./service_map.md) · [tech_stack.md](./tech_stack.md) · [../architecture/06_deployment_architecture.md](../architecture/06_deployment_architecture.md)

---

## 1. Purpose

This document explains **where code lives and why**, so the physical repository
maps cleanly onto the logical boundaries in
[system_boundaries.md](./system_boundaries.md). The layout is a deliberate
projection of the architecture: each top-level package corresponds to a boundary
or a cross-boundary concern, and the dependency direction between packages is
enforced (inner layers never import outer layers).

## 2. Architectural role

Repository structure is the first line of boundary enforcement. If the agent
sandbox must hold no credentials ([system_boundaries.md §4.3](./system_boundaries.md#43-agent-boundary--interface-if-agent)),
then the `agents/` package must not be able to import the `adapters/` package
where secrets live — and the layout plus an import-linter rule make that a
build-time guarantee, not a convention.

## 3. Top-level layout

```
skylize/
├── docs/                      # this documentation set (spine + architecture + agents)
├── scripts/                   # build/ops tooling (e.g. gen_manifest.js)
├── infra/                     # Terraform, Helm charts, Compose files (IaC)
├── src/skylize/
│   ├── edge/                  # IF-EDGE: FastAPI gateway, OIDC verify, rate limit, webhooks
│   ├── app/                   # Application Boundary
│   │   ├── orchestrator/      #   contract resolution, LangGraph control plane
│   │   ├── governance/        #   Governance Authority: minting, revocation, kill switch
│   │   └── decision_engine/   #   event consumer; emits DecisionEvents
│   ├── agents/                # IF-AGENT: agent graphs/crews (UNTRUSTED, sandboxed)
│   ├── runtime/               # sandbox host + tool proxy (IF-TOOL)
│   ├── memory/                # Memory service (VectorStore + MemoryRepository ports)
│   ├── events/                # IF-EVENT: BaseEvent models, EventBus port, Redis adapter
│   ├── dal/                   # IF-DATA: Data Access Layer (RLS, namespacing) — sole DB creds
│   ├── adapters/              # IF-INTEGRATION: LLM gateway, Shopify/Stripe/Meta/TikTok/n8n — sole egress + secrets
│   ├── schemas/               # Pydantic payload models referenced by AgentContract input/output_schema
│   └── contracts/             # AgentContract definitions + the registry loader
└── tests/
    ├── unit/
    ├── contract/              # schema/event/AgentContract conformance (the CI gate)
    └── replay/                # event-replay regression tests
```

## 4. Package-to-boundary mapping

| Package | Boundary / interface | Trust | May import |
|---|---|---|---|
| `edge/` | Edge (`IF-EDGE`) | perimeter | `app/`, `schemas/` |
| `app/orchestrator`, `app/governance`, `app/decision_engine` | Application | trusted | `events/`, `dal/`, `runtime/`, `contracts/`, `schemas/` |
| `runtime/` (tool proxy) | `IF-TOOL` | trusted perimeter | `adapters/`, `events/`, `dal/` |
| `agents/` | Agent (`IF-AGENT`) | **untrusted** | `schemas/` **only** (no `adapters/`, no `dal/`) |
| `memory/` | part of Data path | trusted | `dal/`, `events/` |
| `events/` | Event (`IF-EVENT`) | trusted | `schemas/` |
| `dal/` | Data (`IF-DATA`) | trusted (holds DB creds) | drivers |
| `adapters/` | Integration (`IF-INTEGRATION`) | trusted (holds secrets, sole egress) | external SDKs |
| `schemas/`, `contracts/` | cross-cutting | trusted | (leaf) |

**Enforced rule:** `agents/` may import only `schemas/`. An import-linter check in
CI fails the build if an agent module reaches `adapters/`, `dal/`, or a vendor
SDK — making the sandbox guarantee structural. This mirrors the runtime sandbox
in [../architecture/03_agent_runtime.md §4](../architecture/03_agent_runtime.md#4-the-agent-sandbox-if-agent).

## 5. The `docs/03_agents/` mirror

The agent documentation tree (`docs/03_agents/01_executive_board/...`) is an
**org-chart-shaped mirror**, not a code package. Directory nesting encodes
reporting lines (`CMO/Marketing/Social_Media/vp_creative/copy_team/workers/...`),
which the manifest generator walks to infer `authority_level`, `parent_agent_id`,
and `escalation_path`
([_generation_manifest.csv](../03_agents/_generation_manifest.csv)). Code-level
`AgentContract`s live in `src/skylize/contracts/`; the docs describe the role,
the contracts encode it.

### Known issues carried from the manifest
The manifest flags structural anomalies that are documented in-place (paths are
**not** renamed on disk, to preserve history and links):
- `COO/Procurement/vc_procurement.md` → intended `agent_id: vp_procurement` (`vc` typo).
- `creative_operations_departmant/` → spelling of "department"; path kept as-is.
- `director_vendor_management` appears under both Operations and Procurement (two distinct agents, disambiguated in their specs).
- Duplicate CPO role files (`CPO/chief_product_officer.md` and `CPO/Product/cpo.md`) — both documented with a cross-reference noting the canonical `cpo`.
- Several `director_*` files sit under a `managers/` directory (depth contradiction); the **manifest's** `authority_level` is authoritative over the path.

## 6. Ownership & evolution

- **Owner:** Principal Architect for the layout rule; VP Engineering for
  enforcement in CI.
- **Evolution:** at the Scale tier, packages become independently deployable
  units ([../architecture/06_deployment_architecture.md §2](../architecture/06_deployment_architecture.md#2-deployable-units))
  without moving code — the boundary already exists at the package seam. A new
  external system adds a package under `adapters/`; a new department adds a subtree
  under `agents/` + `docs/03_agents/` and contracts under `contracts/` — never a
  new back-channel.
