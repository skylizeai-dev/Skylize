"""SEO contracts (MVP): seo_keyword_agent.

First tool-enabled worker outside creative: proves the platform, not a single
department's feature. Researches the live SERP landscape via `search.web`,
recalls this org's past SEO work via `memory.search` (closed-loop learning),
then synthesizes keyword strategy. Non-persisting worker — proposes findings,
never writes back to org memory (agent_contract_registry.md §4 pattern).
"""

from __future__ import annotations

from ..base import AgentContract, FailureMode, ToolGrant

seo_keyword_agent = AgentContract(
    agent_id="seo_keyword_agent",
    agent_role=(
        "SEO Keyword Strategist — researches the SERP landscape and proposes "
        "keyword targets & content angles"
    ),
    authority_level="worker",
    department="growth",
    input_schema="skylize.schemas.agents.seo.SeoKeywordExecuteIn",
    output_schema="skylize.schemas.agents.seo.SeoKeywordExecuteOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="synthesize keyword strategy", max_calls_per_run=3),
        ToolGrant(tool_id="search.web", purpose="research current SERP landscape", max_calls_per_run=3),
        ToolGrant(tool_id="memory.search", purpose="recall past SEO work for this org"),
    ],
    invocable_tools=["search.web", "memory.search"],
    max_token_budget=20_000,
    max_execution_time_seconds=120,
    escalation_path=["director_seo", "vp_marketing", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["seo:*", "brand:voice", "campaign:summary"],
    memory_write_access=[],
    human_in_loop_triggers=[],
)

ALL_SEO_CONTRACTS: list[AgentContract] = [seo_keyword_agent]
