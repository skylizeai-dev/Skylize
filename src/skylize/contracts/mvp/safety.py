"""Safety Suite contracts (MVP): chief_security_officer, director_ai_safety,
llm_safety_agent, prompt_injection_agent.

All safety agents are stateless (memory_read_access=[], memory_write_access=[]).
They must evaluate each run in isolation — cross-run state risks anchoring bias
and audit contamination.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

chief_security_officer = AgentContract(
    agent_id="chief_security_officer",
    agent_role="Chief Security Officer — security strategy & incident command",
    authority_level="executive",
    department="security",
    input_schema="skylize.schemas.agents.safety.SafetyAssessmentIn",
    output_schema="skylize.schemas.agents.safety.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="security reasoning", max_calls_per_run=3),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to security directors"),
    ],
    max_token_budget=60_000,
    max_execution_time_seconds=420,
    escalation_path=["ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

director_ai_safety = AgentContract(
    agent_id="director_ai_safety",
    agent_role="Director of AI Safety — oversees LLM safety evaluation pipeline",
    authority_level="director",
    department="security",
    input_schema="skylize.schemas.agents.safety.SafetyAssessmentIn",
    output_schema="skylize.schemas.agents.safety.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="ai safety reasoning", max_calls_per_run=3),
    ],
    max_token_budget=50_000,
    max_execution_time_seconds=360,
    escalation_path=["chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

llm_safety_agent = AgentContract(
    agent_id="llm_safety_agent",
    agent_role="LLM Safety Agent — scores model outputs for policy compliance",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.agents.safety.SafetyAssessmentIn",
    output_schema="skylize.schemas.agents.safety.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="policy compliance scoring", max_calls_per_run=2),
    ],
    max_token_budget=20_000,
    max_execution_time_seconds=120,
    escalation_path=["director_ai_safety", "chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
    ],
)

prompt_injection_agent = AgentContract(
    agent_id="prompt_injection_agent",
    agent_role="Prompt Injection Detector — detects adversarial prompt manipulation",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.agents.safety.SafetyAssessmentIn",
    output_schema="skylize.schemas.agents.safety.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="injection pattern detection", max_calls_per_run=2),
    ],
    max_token_budget=15_000,
    max_execution_time_seconds=90,
    escalation_path=["director_ai_safety", "chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
    ],
)

ALL_SAFETY_CONTRACTS: list[AgentContract] = [
    chief_security_officer,
    director_ai_safety,
    llm_safety_agent,
    prompt_injection_agent,
]
