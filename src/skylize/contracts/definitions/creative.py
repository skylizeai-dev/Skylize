"""
Creative department worker contracts.

Sources:
  docs/03_agents/01_executive_board/CMO/Marketing/Social_Media/vp_creative/copy_team/workers/
  docs/03_agents/.../brand_team/workers/tone_of_voice_agent.md
  agent_contract_registry.md §4.3–4.4

Escalation paths (org chart):
  copy workers     → copy_director → vp_creative → cmo → ceo → human_owner
  tone_of_voice    → brand_director → vp_creative → cmo → human_owner

All workers: FALLBACK_DEGRADED except tone_of_voice (FAIL_CLOSED per doc).
Workers do not write memory — they propose via memory.write_requested.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, ToolGrant

_COPY_WORKER_CHAIN = ["copy_director", "vp_creative", "cmo", "ceo", "human_owner"]
_BRAND_WORKER_CHAIN = ["brand_director", "vp_creative", "cmo", "human_owner"]

hook_generator_agent_contract = AgentContract(
    agent_id="hook_generator_agent",
    agent_role="Hook Generator — produces ad/scroll-stopping hooks",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.HookRequestIn",
    output_schema="skylize.schemas.creative.HooksOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate hooks", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall high-performing hook patterns"),
    ],
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=_COPY_WORKER_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:hooks", "brand:voice"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

ad_copy_agent_contract = AgentContract(
    agent_id="ad_copy_agent",
    agent_role="Ad Copy Agent — writes primary ad copy variants",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.AdCopyRequestIn",
    output_schema="skylize.schemas.creative.AdCopyOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write ad copy", max_calls_per_run=4),
        ToolGrant(tool_id="memory.search", purpose="recall top-performing copy"),
    ],
    max_token_budget=10_000,
    max_execution_time_seconds=90,
    escalation_path=_COPY_WORKER_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:*", "brand:voice"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

caption_writer_agent_contract = AgentContract(
    agent_id="caption_writer_agent",
    agent_role="Caption Writer — writes social and display captions",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.CaptionRequestIn",
    output_schema="skylize.schemas.creative.CaptionsOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write captions", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall brand voice examples"),
    ],
    max_token_budget=6_000,
    max_execution_time_seconds=60,
    escalation_path=_COPY_WORKER_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:captions", "brand:voice"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

script_writer_agent_contract = AgentContract(
    agent_id="script_writer_agent",
    agent_role="Script Writer — writes video scripts",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.ScriptRequestIn",
    output_schema="skylize.schemas.creative.ScriptOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write scripts", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall high-retention structures"),
    ],
    max_token_budget=12_000,
    max_execution_time_seconds=120,
    escalation_path=_COPY_WORKER_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:scripts", "brand:voice"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

cta_optimizer_agent_contract = AgentContract(
    agent_id="cta_optimizer_agent",
    agent_role="CTA Optimizer — selects and optimizes calls to action",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.CTARequestIn",
    output_schema="skylize.schemas.creative.CTAOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate CTA variants", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall CTA performance data"),
    ],
    max_token_budget=4_000,
    max_execution_time_seconds=45,
    escalation_path=_COPY_WORKER_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:ctas", "campaign:summary"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

tone_of_voice_agent_contract = AgentContract(
    agent_id="tone_of_voice_agent",
    agent_role="Tone of Voice — enforce brand tone of voice across all copy",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.creative.ToneOfVoiceAgentIn",
    output_schema="skylize.schemas.creative.ToneOfVoiceAgentOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="tone check and correction", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall brand voice guidelines"),
    ],
    max_token_budget=10_000,
    max_execution_time_seconds=90,
    escalation_path=_BRAND_WORKER_CHAIN,
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=["brand:voice"],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[],
)

ALL_CREATIVE_CONTRACTS: list[AgentContract] = [
    hook_generator_agent_contract,
    ad_copy_agent_contract,
    caption_writer_agent_contract,
    script_writer_agent_contract,
    cta_optimizer_agent_contract,
    tone_of_voice_agent_contract,
]
