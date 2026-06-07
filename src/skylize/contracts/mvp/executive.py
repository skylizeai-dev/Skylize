"""Executive contracts (MVP): ceo, cmo.

These sit at the top of the creative escalation chain
(worker -> ... -> cmo -> ceo -> human_owner).
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

ceo = AgentContract(
    agent_id="ceo",
    agent_role="Chief Executive — company-wide strategy & arbitration",
    authority_level="executive",
    department="executive_office",
    input_schema="skylize.schemas.agents.executive.StrategicDirectiveIn",
    output_schema="skylize.schemas.agents.executive.StrategicDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="strategic reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall org-wide context"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to C-suite"),
    ],
    max_token_budget=120_000,
    max_execution_time_seconds=600,
    escalation_path=["human_owner"],
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["org:*", "strategy:*", "finance:summary"],
    memory_write_access=["strategy:directives", "org:decisions"],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

cmo = AgentContract(
    agent_id="cmo",
    agent_role="Chief Marketing Officer — marketing & creative strategy",
    authority_level="executive",
    department="marketing",
    input_schema="skylize.schemas.agents.executive.StrategicDirectiveIn",
    output_schema="skylize.schemas.agents.executive.StrategicDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="marketing strategy"),
        ToolGrant(tool_id="memory.search", purpose="recall brand & performance"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to VPs"),
    ],
    max_token_budget=100_000,
    max_execution_time_seconds=540,
    escalation_path=["ceo", "human_owner"],
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["creative:*", "brand:*", "campaign:*", "org:decisions"],
    memory_write_access=["creative:strategy", "org:decisions"],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
    ],
)

ALL_EXECUTIVE_CONTRACTS: list[AgentContract] = [ceo, cmo]
