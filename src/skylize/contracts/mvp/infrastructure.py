"""The infrastructure executor: the one contract permitted to stop a customer's VM.

WHY A DEDICATED CONTRACT RATHER THAN GRANTING THE TOOL TO AN EXISTING AGENT
---------------------------------------------------------------------------
The propose/act split this platform already enforces requires an actor that is
NOT one of the stateless observers. `cfo_agent` and the four safety agents may
notice an overspend and propose a containment; none of them may hold the tool
that performs it (asserted by
tests/contract/test_stateless_agents_no_oauth_access.py). This contract is the
stateful executor on the other side of that split, and it holds exactly one
externally-mutating tool and nothing else.

WHY `FIRST_EXTERNAL_LAUNCH`, AND WHY THAT SPECIFIC TRIGGER
-----------------------------------------------------------
The synchronous agent-execution vertical decides on trigger PRESENCE
(app/decision_engine/evaluator.py:230-245): `FIRST_EXTERNAL_LAUNCH` present means
`deferred_to_human`, and it is checked BEFORE the `defers_on_trigger_presence`
opt-out, so it is the one trigger that cannot be suppressed by a future contract
edit. For an action that stops production infrastructure, "cannot be opted out
of" is the property worth having.

The consequence is that this agent can NEVER auto-approve. Every containment goes
to a human, lands in `hitl_queue`, and executes only on an explicit approval —
which is also what makes `hitl_id` available to derive a retry-stable
provider-side idempotency key from (see app/gcp/actions.py).

NO MEMORY ACCESS, and no llm.generate. This contract does not reason; it carries
one parameterised action through the gate. Giving it a model would introduce a
path where a prompt could influence WHICH machine is stopped.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

infrastructure_executor = AgentContract(
    agent_id="infrastructure_executor",
    agent_role=(
        "Infrastructure containment executor - stops a specific customer VM and "
        "removes its external IP, only on an approved human decision"
    ),
    authority_level="executive",
    department="security",
    input_schema="skylize.schemas.agents.infrastructure.ContainInstanceIn",
    output_schema="skylize.schemas.agents.infrastructure.ContainInstanceOut",
    allowed_tools=[
        ToolGrant(
            tool_id="integration.gcp_stop_instance",
            purpose="stop the named VM and remove its external IP access config",
            max_calls_per_run=1,
        ),
    ],
    invocable_tools=["integration.gcp_stop_instance"],
    max_token_budget=8_000,
    max_execution_time_seconds=300,
    escalation_path=["chief_security_officer", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    # Presence of this trigger forces deferral in the agent-execution vertical
    # and cannot be opted out of. See the module docstring.
    human_in_loop_triggers=[HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH],
)

ALL_INFRASTRUCTURE_CONTRACTS: list[AgentContract] = [infrastructure_executor]
