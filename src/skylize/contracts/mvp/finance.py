"""Finance contracts (MVP): cfo, vp_finance, and four finance directors.

CFO is stateless — no memory read or write access.  Financial decisions are
derived from the input payload alone; retaining cross-run state creates
audit-trail risk.
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_FINANCE_CHAIN = ["vp_finance", "cfo", "human_owner"]

cfo = AgentContract(
    agent_id="cfo",
    agent_role="Chief Financial Officer — financial governance & capital allocation",
    authority_level="executive",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="financial reasoning", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall financial history"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to finance directors"),
    ],
    max_token_budget=120_000,
    max_execution_time_seconds=600,
    escalation_path=["human_owner"],
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:*", "org:decisions", "strategy:*"],
    memory_write_access=["finance:decisions"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
        HumanInLoopTrigger.AUTHORITY_EXCEEDED,
    ],
)

vp_finance = AgentContract(
    agent_id="vp_finance",
    agent_role="VP Finance — financial planning & analysis oversight",
    authority_level="vp",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="financial analysis", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall financial plans"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to finance directors"),
    ],
    max_token_budget=80_000,
    max_execution_time_seconds=420,
    escalation_path=["cfo", "human_owner"],
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:*", "campaign:*"],
    memory_write_access=["finance:plans"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.AUTHORITY_EXCEEDED,
    ],
)

director_capital_allocation = AgentContract(
    agent_id="director_capital_allocation",
    agent_role="Director Capital Allocation — allocates budget across campaigns",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="allocation reasoning", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall allocation history"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_FINANCE_CHAIN,
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:allocations:*", "campaign:summary"],
    memory_write_access=["finance:allocations"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_fpanda = AgentContract(
    agent_id="director_fpanda",
    agent_role="Director FP&A — financial planning & analysis",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="FP&A reasoning", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall financial forecasts"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_FINANCE_CHAIN,
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:forecasts:*", "finance:actuals"],
    memory_write_access=["finance:forecasts"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_risk = AgentContract(
    agent_id="director_risk",
    agent_role="Director Risk — financial risk assessment & mitigation",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="risk reasoning", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall risk history"),
    ],
    max_token_budget=35_000,
    max_execution_time_seconds=240,
    escalation_path=_FINANCE_CHAIN,
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=["finance:risk:*"],
    memory_write_access=["finance:risk:assessments"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_treasury = AgentContract(
    agent_id="director_treasury",
    agent_role="Director Treasury — cash management & treasury operations",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.agents.finance.FinancialReviewIn",
    output_schema="skylize.schemas.agents.finance.FinancialDecisionOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="treasury reasoning", max_calls_per_run=2),
        ToolGrant(tool_id="memory.search", purpose="recall treasury positions"),
    ],
    max_token_budget=35_000,
    max_execution_time_seconds=240,
    escalation_path=_FINANCE_CHAIN,
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:treasury:*"],
    memory_write_access=["finance:treasury:ops"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.AUTHORITY_EXCEEDED,
    ],
)

# cfo_agent's first tool-enabled capability: budget_summary. Still stateless
# (memory_read_access=[]/memory_write_access=[]) — only utility.current_datetime
# is invocable, never memory.search, per the CFO/Safety statelessness rule.
cfo_agent = AgentContract(
    agent_id="cfo_agent",
    agent_role=(
        "Chief Financial Officer — financial oversight, spend authorisation "
        "& departmental budget summaries"
    ),
    authority_level="executive",
    department="finance",
    input_schema="skylize.schemas.agents.finance.BudgetSummaryExecuteIn",
    output_schema="skylize.schemas.agents.finance.BudgetSummaryExecuteOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="narrative summary & recommendation", max_calls_per_run=2),
        ToolGrant(tool_id="utility.current_datetime", purpose="timestamp the budget summary"),
    ],
    invocable_tools=["utility.current_datetime"],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=["ceo", "human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=[],
    memory_write_access=[],
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

ALL_FINANCE_CONTRACTS: list[AgentContract] = [
    cfo,
    vp_finance,
    director_capital_allocation,
    director_fpanda,
    director_risk,
    director_treasury,
]

# cfo_agent is the stateless variant registered in the definitions registry
# (used by the memory gateway permission tests).
ALL_FINANCE_DEFINITIONS: list[AgentContract] = [*ALL_FINANCE_CONTRACTS, cfo_agent]
