"""The co-work agent — the human-present shape.

This is the agent an employee talks to during a session. It is the FIRST
contract in this codebase whose authority does not originate at its own
contract: a co-work token carries an `on_behalf_of` claim, and
`GovernanceAuthority.mint` intersects this contract's `allowed_tools` with the
human's compiled authority before signing. So the ceiling below is exactly that
-- a ceiling. What any given session can actually do is the intersection with
that particular employee's grants, which is never wider and is usually narrower.

WHY IT IS NOT ONE OF THE STATELESS AGENTS, AND CANNOT REACH THEM.
`cfo_agent`, `chief_security_officer`, `director_ai_safety`, `llm_safety_agent`
and `prompt_injection_agent` all declare memory_read_access=[] /
memory_write_access=[] and must stay outside any feedback loop: they judge
content, so letting them read memory that their own judgements shaped would
close a loop where the system grades its own homework. This agent is a distinct
agent_id with genuinely non-empty memory access, so their invariant is
untouched.

"Cannot be routed to them" is enforced through the one agent-to-agent routing
mechanism this codebase actually has: `escalation_path`, which
`GovernanceAuthority._escalation_path_for` resolves from the contract. This
contract escalates straight to `human_owner` and names no agent at all.
(`invocable_tools` routes to TOOLS, not agents, so it cannot reach them either.)
`tests/contract/test_cowork_contract.py` pins both properties.

LIFECYCLE: sandbox. It is registered so the tool proxy can resolve it, but it is
reachable only where a caller names it explicitly -- the autonomous fleet never
picks it up.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

#: The memory namespace pattern a co-work session may touch. The `{principal_id}`
#: segment is NOT resolved here -- a contract is static and shared by every
#: employee, so a contract-level grant alone would let one employee's session
#: read another's. `MemoryGateway` binds the concrete principal at call time and
#: refuses a mismatch; see memory/gateway.py.
PRINCIPAL_NAMESPACE_PATTERN = "principal:*"

cowork_agent = AgentContract(
    agent_id="cowork_agent",
    agent_role=(
        "Co-work agent — works alongside one employee during an interactive "
        "session, strictly within that employee's own authority"
    ),
    authority_level="worker",
    department="cowork",
    lifecycle_status="sandbox",
    input_schema="skylize.schemas.agents.cowork.CoworkTurnIn",
    output_schema="skylize.schemas.agents.cowork.CoworkTurnOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="converse with the employee"),
        ToolGrant(
            tool_id="memory.search",
            purpose="recall what this employee's own agents did previously",
            max_calls_per_run=10,
        ),
    ],
    # Both are offered to the model. Every one still goes through ToolProxy.invoke
    # and the full ordered validation -- there is no chat fast path.
    invocable_tools=["llm.generate", "memory.search"],
    max_token_budget=40_000,
    max_execution_time_seconds=120,
    # Straight to the human. Deliberately names NO agent, so an escalation from a
    # co-work session can never be routed into a stateless judge.
    escalation_path=["human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    # Scoped to the principal's OWN namespace at call time by MemoryGateway.
    memory_read_access=[PRINCIPAL_NAMESPACE_PATTERN],
    memory_write_access=[PRINCIPAL_NAMESPACE_PATTERN],
    human_in_loop_triggers=[
        HumanInLoopTrigger.AUTHORITY_EXCEEDED,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

ALL_COWORK_CONTRACTS: list[AgentContract] = [cowork_agent]
