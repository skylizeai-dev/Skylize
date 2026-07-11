"""Agency ops contracts (MVP): agency_requirements_analyst, agency_deliverable_drafter.

Requirements analyst retries and escalates (structured elicitation must not silently degrade).
Deliverable drafter falls back degraded but triggers HITL for brand/legal-sensitive output.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_AGENCY_CHAIN = ["director_client_delivery", "vp_client_ops", "coo", "human_owner"]

agency_requirements_analyst = AgentContract(
    agent_id="agency_requirements_analyst",
    agent_role="Agency Requirements Analyst — elicits and structures client project requirements",
    authority_level="worker",
    department="agency_ops",
    input_schema="skylize.schemas.agents.agency.RequirementsAnalystInput",
    output_schema="skylize.schemas.agents.agency.RequirementsAnalystOutput",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="structure and analyse brief", max_calls_per_run=4),
        ToolGrant(tool_id="memory.search", purpose="recall client history and past briefs"),
    ],
    max_token_budget=12_000,
    max_execution_time_seconds=90,
    escalation_path=_AGENCY_CHAIN,
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["agency:clients:*", "agency:briefs:*"],
    memory_write_access=["agency:requirements:*"],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

agency_deliverable_drafter = AgentContract(
    agent_id="agency_deliverable_drafter",
    agent_role="Agency Deliverable Drafter — produces client-ready deliverable drafts",
    authority_level="worker",
    department="agency_ops",
    input_schema="skylize.schemas.agents.agency.DeliverableDrafterInput",
    output_schema="skylize.schemas.agents.agency.DeliverableDrafterOutput",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="draft client deliverable", max_calls_per_run=6),
        ToolGrant(tool_id="memory.search", purpose="recall requirements, templates, and brand voice"),
    ],
    max_token_budget=20_000,
    max_execution_time_seconds=180,
    escalation_path=_AGENCY_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["agency:requirements:*", "brand:voice", "agency:templates:*"],
    memory_write_access=["agency:deliverables:drafts"],
    governance_token_required=True,
    human_in_loop_triggers=[HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE],
)

ALL_AGENCY_CONTRACTS: list[AgentContract] = [
    agency_requirements_analyst,
    agency_deliverable_drafter,
]
