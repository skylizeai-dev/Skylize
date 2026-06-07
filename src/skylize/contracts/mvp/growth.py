"""Growth contracts (MVP): director_growth.

The growth half of "creative + growth": proposes campaigns/budget that the
Decision Engine evaluates. A director may launch internal-only actions and
allocate within a delegated cap; external launch / over-ceiling spend defers to
a human (capital_allocation.md §5).
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

director_growth = AgentContract(
    agent_id="director_growth",
    agent_role="Director Growth — proposes campaigns & budget reallocations",
    authority_level="director",
    department="growth",
    input_schema="skylize.schemas.agents.growth.GrowthMandateIn",
    output_schema="skylize.schemas.agents.growth.CampaignProposalOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="campaign reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall performance & playbooks"),
    ],
    max_token_budget=30_000,
    max_execution_time_seconds=240,
    escalation_path=["vp_marketing", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["campaign:*", "sales:*", "org:playbooks"],
    memory_write_access=["campaign:proposals"],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH,
    ],
)

ALL_GROWTH_CONTRACTS: list[AgentContract] = [director_growth]
