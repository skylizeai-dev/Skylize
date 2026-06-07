"""Creative crew contracts (MVP) — 9 agents.

Patterns (agent_contract_registry.md §4): workers have tight budgets/short
timeouts and `FALLBACK_DEGRADED`; non-persisting workers have empty
`memory_write_access`; `escalation_path` always walks the org tree to
`human_owner`.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_CREATIVE_CHAIN = ["copy_director", "vp_creative", "cmo", "ceo", "human_owner"]

vp_creative = AgentContract(
    agent_id="vp_creative",
    agent_role="VP Creative — owns creative production strategy & approvals",
    authority_level="vp",
    department="creative",
    input_schema="skylize.schemas.agents.creative.CreativeMandateIn",
    output_schema="skylize.schemas.agents.creative.CreativeStrategyOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="creative direction"),
        ToolGrant(tool_id="memory.search", purpose="recall brand & past wins"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="assign to directors"),
    ],
    max_token_budget=80_000,
    max_execution_time_seconds=420,
    escalation_path=["cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:*", "brand:*", "campaign:summary"],
    memory_write_access=["creative:strategy", "creative:approvals"],
    human_in_loop_triggers=[
        HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
    ],
)

copy_director = AgentContract(
    agent_id="copy_director",
    agent_role="Copy Director — owns the copy workflow & quality",
    authority_level="director",
    department="creative",
    input_schema="skylize.schemas.agents.creative.CopyBriefIn",
    output_schema="skylize.schemas.agents.creative.CopyPackageOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="copy review & synthesis"),
        ToolGrant(tool_id="memory.search", purpose="recall voice & top copy"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="assign to copy workers"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=["vp_creative", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:copy:*", "brand:voice", "campaign:summary"],
    memory_write_access=["creative:copy:approved"],
    human_in_loop_triggers=[HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE],
)

art_director = AgentContract(
    agent_id="art_director",
    agent_role="Art Director — owns visual production & quality",
    authority_level="director",
    department="creative",
    input_schema="skylize.schemas.agents.creative.ArtBriefIn",
    output_schema="skylize.schemas.agents.creative.ArtPackageOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="art direction reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall brand visuals"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="assign to art workers"),
    ],
    max_token_budget=30_000,
    max_execution_time_seconds=300,
    escalation_path=["vp_creative", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:visual:*", "brand:guidelines"],
    memory_write_access=["creative:visual:approved"],
    human_in_loop_triggers=[HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE],
)

creative_operations_manager = AgentContract(
    agent_id="creative_operations_manager",
    agent_role="Creative Ops Manager — routes tasks, tracks deadlines",
    authority_level="manager",
    department="creative",
    input_schema="skylize.schemas.agents.creative.OpsTaskIn",
    output_schema="skylize.schemas.agents.creative.OpsStatusOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="task routing decisions"),
        ToolGrant(tool_id="memory.search", purpose="check workflow state"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="route to workers"),
    ],
    max_token_budget=10_000,
    max_execution_time_seconds=120,
    escalation_path=["vp_creative", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["creative:*"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

hook_generator_agent = AgentContract(
    agent_id="hook_generator_agent",
    agent_role="Hook Generator — produces ad/scroll-stopping hooks",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.HookRequestIn",
    output_schema="skylize.schemas.agents.creative.HooksOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate hooks", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall high-performing patterns"),
    ],
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=_CREATIVE_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:hooks", "brand:voice"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

ad_copy_agent = AgentContract(
    agent_id="ad_copy_agent",
    agent_role="Ad Copy Agent — writes primary ad copy variants",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.AdCopyRequestIn",
    output_schema="skylize.schemas.agents.creative.AdCopyOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write ad copy", max_calls_per_run=4),
        ToolGrant(tool_id="memory.search", purpose="recall top-performing copy"),
    ],
    max_token_budget=10_000,
    max_execution_time_seconds=90,
    escalation_path=_CREATIVE_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:*", "brand:voice"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

caption_writer_agent = AgentContract(
    agent_id="caption_writer_agent",
    agent_role="Caption Writer — writes social and display captions",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.CaptionRequestIn",
    output_schema="skylize.schemas.agents.creative.CaptionsOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write captions", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall brand voice examples"),
    ],
    max_token_budget=6_000,
    max_execution_time_seconds=60,
    escalation_path=_CREATIVE_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:captions", "brand:voice"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

script_writer_agent = AgentContract(
    agent_id="script_writer_agent",
    agent_role="Script Writer — writes video scripts",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.ScriptRequestIn",
    output_schema="skylize.schemas.agents.creative.ScriptOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="write scripts", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall high-retention structures"),
    ],
    max_token_budget=12_000,
    max_execution_time_seconds=120,
    escalation_path=_CREATIVE_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:scripts", "brand:voice"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

cta_optimizer_agent = AgentContract(
    agent_id="cta_optimizer_agent",
    agent_role="CTA Optimizer — selects and optimizes calls to action",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.CTARequestIn",
    output_schema="skylize.schemas.agents.creative.CTAOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate CTA variants", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall CTA performance data"),
    ],
    max_token_budget=4_000,
    max_execution_time_seconds=45,
    escalation_path=_CREATIVE_CHAIN,
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:ctas", "campaign:summary"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

ALL_CREATIVE_CONTRACTS: list[AgentContract] = [
    vp_creative,
    copy_director,
    art_director,
    creative_operations_manager,
    hook_generator_agent,
    ad_copy_agent,
    caption_writer_agent,
    script_writer_agent,
    cta_optimizer_agent,
]
