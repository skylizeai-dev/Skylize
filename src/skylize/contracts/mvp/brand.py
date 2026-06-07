"""Brand crew contracts (MVP): brand_guardian_agent, tone_of_voice_agent.

`brand_guardian_agent` is a safety-style reviewer: it `FAIL_CLOSED`s and can
veto lower-authority approvals (safety veto, agent_governance.md §11).
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_BRAND_CHAIN = ["vp_creative", "cmo", "ceo", "human_owner"]

brand_guardian_agent = AgentContract(
    agent_id="brand_guardian_agent",
    agent_role="Brand Guardian — enforces brand & legal compliance on content",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.brand.BrandCheckIn",
    output_schema="skylize.schemas.agents.brand.BrandVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="evaluate brand/legal fit", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall brand guidelines"),
    ],
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=_BRAND_CHAIN,
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=["brand:*"],
    memory_write_access=[],
    human_in_loop_triggers=[HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE],
)

tone_of_voice_agent = AgentContract(
    agent_id="tone_of_voice_agent",
    agent_role="Tone of Voice — aligns content to brand voice",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.brand.ToneCheckIn",
    output_schema="skylize.schemas.agents.brand.ToneAdjustedOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="adjust tone", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall brand voice"),
    ],
    max_token_budget=6_000,
    max_execution_time_seconds=60,
    escalation_path=_BRAND_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["brand:voice"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

ALL_BRAND_CONTRACTS: list[AgentContract] = [brand_guardian_agent, tone_of_voice_agent]
