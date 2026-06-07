"""Security contracts (MVP): fraud_detection_agent.

Security workers `FAIL_CLOSED` (deny on doubt) and raise high-severity HITL.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

fraud_detection_agent = AgentContract(
    agent_id="fraud_detection_agent",
    agent_role="Fraud Detection — flags fraudulent/anomalous activity",
    authority_level="worker",
    department="security",
    input_schema="skylize.schemas.agents.security.ActivitySignalIn",
    output_schema="skylize.schemas.agents.security.FraudVerdictOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="reason over signals", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall known fraud patterns"),
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
    human_in_loop_triggers=[
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

ALL_SECURITY_CONTRACTS: list[AgentContract] = [fraud_detection_agent]
