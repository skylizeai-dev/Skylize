"""
Security department contracts.

Sources:
  docs/03_agents/ (safety.py MVP contracts, registry doc §4.5)

Escalation paths (org chart):
  chief_security_officer  → ceo → human_owner
  director_ai_safety      → chief_security_officer → ceo → human_owner
  llm_safety_agent        → director_ai_safety → chief_security_officer → ceo → human_owner
  prompt_injection_agent  → director_ai_safety → chief_security_officer → ceo → human_owner
  fraud_detection_agent   → manager_security_operations → director_cybersecurity
                            → chief_security_officer → human_owner

All security/safety agents: FAIL_CLOSED — deny on doubt.
All are stateless: memory_read_access and memory_write_access minimal.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

chief_security_officer_contract = AgentContract(
    agent_id="chief_security_officer",
    agent_role="Chief Security Officer — security strategy & incident command",
    authority_level="executive",
    department="security",
    input_schema="skylize.schemas.security.SafetyAssessmentIn",
    output_schema="skylize.schemas.security.SafetyVerdictOut",
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
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

director_ai_safety_contract = AgentContract(
    agent_id="director_ai_safety",
    agent_role="Director of AI Safety — oversees LLM safety evaluation pipeline",
    authority_level="director",
    department="security",
    input_schema="skylize.schemas.security.SafetyAssessmentIn",
    output_schema="skylize.schemas.security.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="ai safety reasoning", max_calls_per_run=3),
    ],
    max_token_budget=50_000,
    max_execution_time_seconds=360,
    escalation_path=["chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

llm_safety_agent_contract = AgentContract(
    agent_id="llm_safety_agent",
    agent_role="LLM Safety Agent — scores model outputs for policy compliance",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.security.SafetyAssessmentIn",
    output_schema="skylize.schemas.security.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="policy compliance scoring", max_calls_per_run=2),
    ],
    max_token_budget=20_000,
    max_execution_time_seconds=120,
    escalation_path=["director_ai_safety", "chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
    ],
)

prompt_injection_agent_contract = AgentContract(
    agent_id="prompt_injection_agent",
    agent_role="Prompt Injection Detector — detects adversarial prompt manipulation",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.security.SafetyAssessmentIn",
    output_schema="skylize.schemas.security.SafetyVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="injection pattern detection", max_calls_per_run=2),
    ],
    max_token_budget=15_000,
    max_execution_time_seconds=90,
    escalation_path=["director_ai_safety", "chief_security_officer", "ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
    ],
)

fraud_detection_agent_contract = AgentContract(
    agent_id="fraud_detection_agent",
    agent_role="Fraud Detection — flags fraudulent/anomalous activity",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.security.ActivitySignalIn",
    output_schema="skylize.schemas.security.FraudVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="reason over signals", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall known fraud patterns"),
        ToolGrant(tool_id="bi.query", purpose="read transaction/activity aggregates"),
    ],
    max_token_budget=12_000,
    max_execution_time_seconds=90,
    escalation_path=[
        "manager_security_operations",
        "director_cybersecurity",
        "chief_security_officer",
        "human_owner",
    ],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=["security:fraud:*", "security:patterns"],
    memory_write_access=["security:fraud:signals"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

ALL_SECURITY_CONTRACTS: list[AgentContract] = [
    chief_security_officer_contract,
    director_ai_safety_contract,
    llm_safety_agent_contract,
    prompt_injection_agent_contract,
    fraud_detection_agent_contract,
]
