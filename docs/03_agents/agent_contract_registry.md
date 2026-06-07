# Agent Contract Registry

**Status:** Foundation document (source of truth)
**Owner:** Chief Systems Architect
**Related:** [agent_governance.md](./agent_governance.md) · [system_boundaries.md](../02_architecture/system_boundaries.md) · [event_driven_architecture.md](../02_architecture/event_driven_architecture.md)

> **CONSISTENCY CONTRACT.** The `authority_level` field uses the **identical**
> canonical set defined in
> [agent_governance.md §2](./agent_governance.md#2-authority-hierarchy) —
> `executive`, `vp`, `director`, `manager`, `worker`. The `GovernanceToken` in
> §3 is **byte-identical** to
> [agent_governance.md §4](./agent_governance.md#4-governance-token). The
> `escalation_path` field is the concrete realization of the escalation concept
> in [agent_governance.md §3](./agent_governance.md#3-authority--escalation).

---

## 1. Purpose

Every agent in Skylize is defined by a **contract** — a Pydantic v2 model that
declares, statically and auditably, exactly what the agent is, what it may
consume and produce, what it may touch, and how it fails. The **registry** stores
these contracts and the **Orchestrator** resolves them at runtime to compose
runs and mint governance tokens (see
[system_boundaries.md §4.2](../02_architecture/system_boundaries.md#42-application-boundary--interfaces-if-agent-if-data-if-event)).

Nothing about an agent is implicit. If it is not in the contract, the agent
cannot do it.

---

## 2. The Contract Schema

```python
from __future__ import annotations
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


# Canonical authority levels — IDENTICAL to agent_governance.md §2
AuthorityLevel = Literal["executive", "vp", "director", "manager", "worker"]


class FailureMode(str, Enum):
    """What the agent does when it errors or is denied."""
    RETRY_THEN_ESCALATE = "retry_then_escalate"
    ESCALATE_IMMEDIATELY = "escalate_immediately"
    FAIL_CLOSED = "fail_closed"          # stop, emit nothing actionable
    FALLBACK_DEGRADED = "fallback_degraded"


class HumanInLoopTrigger(str, Enum):
    SPEND_OVER_CEILING = "spend_over_ceiling"
    FIRST_EXTERNAL_LAUNCH = "first_external_launch"
    BRAND_LEGAL_SENSITIVE = "brand_legal_sensitive"
    AUTHORITY_EXCEEDED = "authority_exceeded"
    SECURITY_SEVERITY_HIGH = "security_severity_high"
    LOW_CONFIDENCE_IRREVERSIBLE = "low_confidence_irreversible"


class ToolGrant(BaseModel):
    tool_id: str
    purpose: str
    max_calls_per_run: int | None = None
    requires_governance_token: bool = True


class AgentContract(BaseModel):
    """Static, auditable definition of one agent. Stored in the registry,
    resolved by the Orchestrator, enforced by the tool proxy / Decision Engine."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str                                    # globally unique
    agent_role: str                                  # human-readable role
    authority_level: AuthorityLevel                  # canonical set
    department: str                                  # owning department channel

    # I/O contracts — fully-qualified Pydantic model paths (resolved at runtime)
    input_schema: str
    output_schema: str

    # Capability declaration (the tool manifest, see governance §6)
    allowed_tools: list[ToolGrant]

    # Budgets — also become ceilings in the governance token (§3)
    max_token_budget: int
    max_execution_time_seconds: int

    # Escalation — ordered chain ending at a human role (governance §3)
    escalation_path: list[str]

    failure_mode: FailureMode

    # Memory scope — namespaces the agent may read / write (governance §5)
    memory_read_access: list[str]
    memory_write_access: list[str]

    # Governance
    governance_token_required: bool = True
    human_in_loop_triggers: list[HumanInLoopTrigger] = Field(default_factory=list)
```

**Field-by-field meaning** (cross-referenced to governance):

| Field | Meaning | Enforced by |
|---|---|---|
| `agent_id` | Unique identity, used in tokens, events, audit | Registry / everywhere |
| `agent_role` | Human-readable role | Docs / UI |
| `authority_level` | Canonical level (§2 governance) | Decision Engine authority check |
| `department` | Event channel + publisher contract | Event bus |
| `input_schema` / `output_schema` | Typed I/O; output is wrapped into events | Orchestrator |
| `allowed_tools` | Tool manifest allow-list | Tool proxy (`IF-TOOL`) |
| `max_token_budget` | LLM cost ceiling per run | Token + LLM adapter |
| `max_execution_time_seconds` | Wall-clock ceiling | Runtime + circuit breaker |
| `escalation_path` | Ordered escalation chain → human | Decision Engine |
| `failure_mode` | Behavior on error/denial | Runtime |
| `memory_read_access` / `memory_write_access` | Memory namespaces | DAL (`IF-DATA`) |
| `governance_token_required` | Whether a token is needed to act (always true for side effects) | Tool proxy / adapters |
| `human_in_loop_triggers` | Conditions forcing human approval | Decision Engine (governance §9) |

---

## 3. Governance Token

> **Byte-identical** to
> [agent_governance.md §4](./agent_governance.md#4-governance-token). Reproduced
> here so contract authors see the token their `authority_level`,
> `allowed_tools`, and budgets feed into.

```python
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class GovernanceToken(BaseModel):
    """Signed proof of an agent's authority to act. Minted ONLY by the
    Governance Authority. Validated by the tool proxy and integration adapters
    before any side-effecting action."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    token_id: UUID
    agent_id: str
    authority_level: Literal[
        "executive", "vp", "director", "manager", "worker"
    ]
    department: str
    delegation_chain: list[str]
    scope: list[str]                      # subset of contract.allowed_tools
    max_token_budget: int
    max_execution_time_seconds: int
    issued_at: datetime
    expires_at: datetime
    nonce: str
    signature: str                        # ECDSA P-384 over canonical serialization (ADR 0001)
```

The Orchestrator derives a token's `scope` and budget ceilings from the resolved
contract, narrowing (never widening) `allowed_tools` and budgets for the specific
run. Validation order (signature → expiry → revocation → scope → budget →
delegation) is defined in
[agent_governance.md §4.3](./agent_governance.md#43-how-agents-validate-it-before-executing).

---

## 4. Example Contracts (5 representative agents)

These reference real agents in the org chart
([00_organization_chart.md](./00_organization_chart.md)). Schemas are referenced
by dotted path; payload models live in `skylize.schemas.*`.

### 4.1 `ceo` — executive

```python
ceo_contract = AgentContract(
    agent_id="ceo",
    agent_role="Chief Executive — company-wide strategy & arbitration",
    authority_level="executive",
    department="executive_office",
    input_schema="skylize.schemas.exec.StrategicDirectiveIn",
    output_schema="skylize.schemas.exec.StrategicDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="strategic reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall org-wide context"),
        ToolGrant(tool_id="bi.query", purpose="read company KPIs"),
        ToolGrant(tool_id="orchestrator.delegate",
                  purpose="delegate mandates to VP/C-suite agents"),
    ],
    max_token_budget=120_000,
    max_execution_time_seconds=600,
    escalation_path=["human_owner"],          # top of the tree → human
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["org:*", "strategy:*", "finance:summary"],
    memory_write_access=["strategy:directives", "org:decisions"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)
```

### 4.2 `vp_creative` — vp

```python
vp_creative_contract = AgentContract(
    agent_id="vp_creative",
    agent_role="VP Creative — owns creative production strategy & approvals",
    authority_level="vp",
    department="creative",
    input_schema="skylize.schemas.creative.CreativeMandateIn",
    output_schema="skylize.schemas.creative.CreativeStrategyOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="creative direction"),
        ToolGrant(tool_id="memory.search", purpose="recall brand & past wins"),
        ToolGrant(tool_id="orchestrator.delegate",
                  purpose="assign work to directors (copy/art/video)"),
        ToolGrant(tool_id="bi.query", purpose="read creative performance"),
    ],
    max_token_budget=80_000,
    max_execution_time_seconds=420,
    escalation_path=["cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:*", "brand:*", "campaign:summary"],
    memory_write_access=["creative:strategy", "creative:approvals"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
    ],
)
```

### 4.3 `copy_director` — director

```python
copy_director_contract = AgentContract(
    agent_id="copy_director",
    agent_role="Copy Director — owns the copy workflow & quality",
    authority_level="director",
    department="creative",
    input_schema="skylize.schemas.creative.CopyBriefIn",
    output_schema="skylize.schemas.creative.CopyPackageOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="copy review & synthesis"),
        ToolGrant(tool_id="memory.search", purpose="recall voice & top copy"),
        ToolGrant(tool_id="orchestrator.delegate",
                  purpose="assign to copy workers (hook/ad/caption/cta)"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=["vp_creative", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:copy:*", "brand:voice", "campaign:summary"],
    memory_write_access=["creative:copy:approved"],
    governance_token_required=True,
    human_in_loop_triggers=[HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE],
)
```

### 4.4 `hook_generator_agent` — worker

```python
hook_generator_contract = AgentContract(
    agent_id="hook_generator_agent",
    agent_role="Hook Generator — produces ad/scroll-stopping hooks",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.HookRequestIn",
    output_schema="skylize.schemas.creative.HooksOut",   # → creative.hooks_generated
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate hooks",
                  max_calls_per_run=3),
        ToolGrant(tool_id="memory.search",
                  purpose="recall high-performing hook patterns"),
    ],
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=["copy_director", "vp_creative", "cmo", "ceo",
                     "human_owner"],
    failure_mode=FailureMode.FALLBACK_DEGRADED,   # return fewer/simpler hooks
    memory_read_access=["creative:copy:hooks", "brand:voice"],
    memory_write_access=[],                        # workers propose, don't persist
    governance_token_required=True,
    human_in_loop_triggers=[],                     # bounded task, no human gate
)
```

### 4.5 `fraud_detection_agent` — worker (security)

```python
fraud_detection_contract = AgentContract(
    agent_id="fraud_detection_agent",
    agent_role="Fraud Detection — flags fraudulent/anomalous activity",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.security.ActivitySignalIn",
    output_schema="skylize.schemas.security.FraudVerdictOut",  # → sales/governance
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="reason over signals",
                  max_calls_per_run=2),
        ToolGrant(tool_id="memory.search",
                  purpose="recall known fraud patterns"),
        ToolGrant(tool_id="bi.query",
                  purpose="read transaction/activity aggregates"),
    ],
    max_token_budget=12_000,
    max_execution_time_seconds=90,
    escalation_path=["manager_security_operations",
                     "director_cybersecurity",
                     "chief_security_officer", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,          # on error, block, don't pass
    memory_read_access=["security:fraud:*", "security:patterns"],
    memory_write_access=["security:fraud:signals"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)
```

**Note the patterns:** workers have tight budgets and short timeouts; security
workers `FAIL_CLOSED` (deny on doubt) while creative workers `FALLBACK_DEGRADED`
(degrade gracefully); `escalation_path` always walks up the org tree to
`human_owner`; `memory_write_access` is empty for non-persisting workers; and the
`human_in_loop_triggers` map directly to the conditions in
[agent_governance.md §9](./agent_governance.md#9-human-in-the-loop-trigger-conditions).

---

## 5. Registry Lookup Pattern

The **registry** is the authoritative store of contracts; the **Orchestrator**
resolves them at runtime.

### 5.1 Storage & loading
- Contracts are versioned records in Postgres (`agent_contracts`, keyed by
  `agent_id` + `version`), seeded from the `.md`/Python definitions in the org
  chart, and validated against `AgentContract` on load. Invalid contracts fail
  startup (`fail-closed`).
- A hot in-memory map `agent_id → AgentContract` is cached and invalidated on
  contract version bump (a `governance.*` config event).

### 5.2 Resolution at runtime

```python
class AgentRegistry:
    def resolve(self, agent_id: str, *, tenant_id: str) -> AgentContract:
        contract = self._cache.get(agent_id) or self._load(agent_id)
        if contract is None:
            raise AgentNotRegistered(agent_id)        # fail closed
        # tenant may disable/override budgets within policy ceilings
        return self._apply_tenant_policy(contract, tenant_id)


class Orchestrator:
    def invoke(self, agent_id: str, payload, *, ctx: RequestContext):
        contract = self.registry.resolve(agent_id, tenant_id=ctx.org_id)

        # 1. Governance gate: not suspended / killed (governance §6–8)
        self.governance.assert_active(agent_id, ctx)

        # 2. Validate input against the contract's declared input_schema
        model_in = load_model(contract.input_schema).model_validate(payload)

        # 3. Mint a run-scoped governance token (scope ⊆ allowed_tools)
        token = self.governance.mint(
            contract=contract,
            scope=[t.tool_id for t in contract.allowed_tools],
            tenant_id=ctx.org_id,
        )                                              # → governance.token_issued

        # 4. Run agent in the sandbox (IF-AGENT) with token + tool proxy
        result = self.runtime.run(contract, model_in, token)

        # 5. Validate output, wrap as an event, publish with provenance
        model_out = load_model(contract.output_schema).model_validate(result)
        self.events.publish_agent_output(
            contract=contract, output=model_out, token=token, ctx=ctx,
        )
        return model_out
```

### 5.3 Resolution rules
1. **Unknown `agent_id` → fail closed** (`AgentNotRegistered`); never run an
   unregistered agent.
2. **Authority is read from the resolved contract**, not from the caller — the
   Decision Engine trusts the registry, not the request.
3. **Token scope is derived from the contract** and may only narrow it.
4. **Tenant policy may tighten** budgets/triggers/disable an agent, never loosen
   below platform floors.
5. Every resolution + mint is audited (`audit.action_recorded`,
   `governance.token_issued`), tying the run to its contract version for replay
   (see
   [event_driven_architecture.md §10](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance)).

---

## 6. Invariants (must always hold)

1. `authority_level` ∈ {`executive`, `vp`, `director`, `manager`, `worker`} —
   identical to [agent_governance.md §2](./agent_governance.md#2-authority-hierarchy).
2. `GovernanceToken` here is byte-identical to
   [agent_governance.md §4](./agent_governance.md#4-governance-token).
3. `escalation_path` is an ordered chain up the org tree ending at
   `human_owner`, realizing governance §3 escalation.
4. An agent's runtime capability never exceeds its contract; the token narrows,
   never widens.
5. Unknown or invalid contracts fail closed.
6. Every resolution, mint, input, and output is validated and audited.
