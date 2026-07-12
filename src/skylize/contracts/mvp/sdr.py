"""SDR contracts (MVP): sdr_outreach_agent, lead_qualifier_agent.

Workers use `FALLBACK_DEGRADED`; outreach triggers HITL on first external launch
to prevent unsanctioned bulk sends. Qualifier is fully automated (low blast radius).
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_SDR_CHAIN = ["director_b2b_sales", "vp_sales", "cro", "human_owner"]

sdr_outreach_agent = AgentContract(
    agent_id="sdr_outreach_agent",
    agent_role="SDR Outreach Agent — executes personalized outbound sequences",
    authority_level="worker",
    department="sales",
    input_schema="skylize.schemas.agents.sdr.SDROutreachInput",
    output_schema="skylize.schemas.agents.sdr.SDROutreachOutput",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate outreach copy", max_calls_per_run=5),
        ToolGrant(tool_id="memory.search", purpose="recall lead context"),
        ToolGrant(tool_id="crm.write", purpose="log outreach activity"),
    ],
    max_token_budget=15_000,
    max_execution_time_seconds=120,
    escalation_path=_SDR_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["sales:leads:*", "brand:voice"],
    memory_write_access=["sales:outreach:sent"],
    governance_token_required=True,
    human_in_loop_triggers=[HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH],
)

lead_qualifier_agent = AgentContract(
    agent_id="lead_qualifier_agent",
    agent_role="Lead Qualifier — scores and qualifies inbound leads against ICP",
    authority_level="worker",
    department="sales",
    input_schema="skylize.schemas.agents.sdr.LeadQualifierInput",
    output_schema="skylize.schemas.agents.sdr.LeadQualifierOutput",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="reason over lead data", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall ICP and lead history"),
        ToolGrant(tool_id="crm.read", purpose="fetch lead record"),
    ],
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=_SDR_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["sales:leads:*", "sales:icp:*"],
    memory_write_access=["sales:leads:qualified"],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

ALL_SDR_CONTRACTS: list[AgentContract] = [
    sdr_outreach_agent,
    lead_qualifier_agent,
]
