"""
Finance department contracts.

Sources:
  docs/03_agents/01_executive_board/CFO/cfo.md
  docs/03_agents/01_executive_board/CFO/Finance/vp_finance.md
  docs/03_agents/01_executive_board/CFO/Finance/director_capital_allocation.md
  docs/03_agents/01_executive_board/CFO/Finance/director_fpanda.md
  docs/03_agents/01_executive_board/CFO/Finance/director_risk.md
  docs/03_agents/01_executive_board/CFO/Finance/director_treasury.md

Escalation paths follow the org chart exactly:
  cfo           → human_owner
  vp_finance    → cfo → human_owner
  directors     → vp_finance → cfo → human_owner
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, HumanInLoopTrigger, ToolGrant

_DIRECTOR_FINANCE_CHAIN = ["vp_finance", "cfo", "human_owner"]

cfo_contract = AgentContract(
    agent_id="cfo",
    agent_role="Chief Financial Officer — protect and allocate company capital",
    authority_level="executive",
    department="finance",
    input_schema="skylize.schemas.finance.CapitalMandateIn",
    output_schema="skylize.schemas.finance.CapitalPolicyOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="strategic financial reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall org-wide financial context"),
        ToolGrant(tool_id="bi.query", purpose="read company KPIs and spend signals"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate mandates to vp_finance and finance directors"),
    ],
    max_token_budget=120_000,
    max_execution_time_seconds=600,
    escalation_path=["human_owner"],
    failure_mode=FailureMode.ESCALATE_IMMEDIATELY,
    memory_read_access=["finance:*", "org:*", "strategy:summary"],
    memory_write_access=["finance:policy", "finance:ceilings"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ],
)

vp_finance_contract = AgentContract(
    agent_id="vp_finance",
    agent_role="VP Finance — translate capital policy into department budgets, forecasting, and risk controls",
    authority_level="vp",
    department="finance",
    input_schema="skylize.schemas.finance.VpFinanceIn",
    output_schema="skylize.schemas.finance.VpFinanceOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="financial reasoning and direction"),
        ToolGrant(tool_id="memory.search", purpose="recall finance context"),
        ToolGrant(tool_id="bi.query", purpose="read financial KPIs"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="operate finance directors"),
    ],
    max_token_budget=80_000,
    max_execution_time_seconds=420,
    escalation_path=["cfo", "human_owner"],
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["finance:*", "org:summary"],
    memory_write_access=["finance:strategy", "finance:approvals"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH,
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_capital_allocation_contract = AgentContract(
    agent_id="director_capital_allocation",
    agent_role="Director Capital Allocation — distribute budget across departments and campaigns within delegated caps",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.finance.DirectorCapitalAllocationIn",
    output_schema="skylize.schemas.finance.DirectorCapitalAllocationOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="allocation reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall allocation history"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to manager_budgeting"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_DIRECTOR_FINANCE_CHAIN,
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["finance:allocation:*", "campaign:summary"],
    memory_write_access=["finance:allocation:approved"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_fpanda_contract = AgentContract(
    agent_id="director_fpanda",
    agent_role="Director FP&A — own financial planning, forecasts, variance analysis, and profitability",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.finance.DirectorFpandaIn",
    output_schema="skylize.schemas.finance.DirectorFpandaOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="forecasting and variance analysis"),
        ToolGrant(tool_id="memory.search", purpose="recall historical financial data"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="delegate to FP&A workers"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_DIRECTOR_FINANCE_CHAIN,
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["finance:fpa:*", "finance:summary"],
    memory_write_access=["finance:fpa:reports"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_risk_contract = AgentContract(
    agent_id="director_risk",
    agent_role="Director Risk — identify and bound financial risk; veto unsafe allocations",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.finance.DirectorRiskIn",
    output_schema="skylize.schemas.finance.DirectorRiskOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="risk assessment reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall risk history and fraud signals"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="coordinate with risk workers"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_DIRECTOR_FINANCE_CHAIN,
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["finance:risk:*", "security:fraud:summary"],
    memory_write_access=["finance:risk:limits"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

director_treasury_contract = AgentContract(
    agent_id="director_treasury",
    agent_role="Director Treasury — own cash/settlement reconciliation and budget ledger integrity",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.finance.DirectorTreasuryIn",
    output_schema="skylize.schemas.finance.DirectorTreasuryOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="reconciliation reasoning"),
        ToolGrant(tool_id="memory.search", purpose="recall settlement history"),
        ToolGrant(tool_id="orchestrator.delegate", purpose="coordinate treasury workers"),
    ],
    max_token_budget=40_000,
    max_execution_time_seconds=300,
    escalation_path=_DIRECTOR_FINANCE_CHAIN,
    failure_mode=FailureMode.RETRY_THEN_ESCALATE,
    memory_read_access=["finance:treasury:*"],
    memory_write_access=["finance:treasury:recon"],
    governance_token_required=True,
    human_in_loop_triggers=[
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE,
        HumanInLoopTrigger.SPEND_OVER_CEILING,
    ],
)

ALL_FINANCE_CONTRACTS: list[AgentContract] = [
    cfo_contract,
    vp_finance_contract,
    director_capital_allocation_contract,
    director_fpanda_contract,
    director_risk_contract,
    director_treasury_contract,
]
