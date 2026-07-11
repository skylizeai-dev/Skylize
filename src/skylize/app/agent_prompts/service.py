"""Agent prompt resolution service — called by n8n before every LLM call."""

from __future__ import annotations

from skylize.contracts.registry import AgentRegistry
from skylize.schemas.agent_prompt import AgentPromptResponse

_FRONTIER_LEVELS = frozenset({"executive", "vp"})


class AgentPromptService:
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def get_prompt(self, agent_id: str, org_id: str = "platform") -> AgentPromptResponse:
        contract = self._registry.resolve(agent_id)

        output_schema_name = contract.output_schema.rsplit(".", 1)[-1]
        tools = ", ".join(g.tool_id for g in contract.allowed_tools)
        triggers = [t.value for t in contract.human_in_loop_triggers]

        system_prompt = (
            f"You are the {contract.agent_role}.\n"
            f"Department: {contract.department}\n"
            f"Authority level: {contract.authority_level}\n\n"
            f"Responsibilities:\n"
            f"- Operate within a token budget of {contract.max_token_budget} tokens per run.\n"
            f"- Use only the following tools: {tools or 'none'}.\n"
            f"- Your output must conform to the {output_schema_name} schema.\n\n"
            f"Governance rules:\n"
            f"- Failure mode: {contract.failure_mode.value}.\n"
            f"- Escalation path: {' -> '.join(contract.escalation_path)}.\n"
            + (
                f"- Pause for human approval on: {', '.join(triggers)}.\n"
                if triggers
                else ""
            )
        )

        model_tier: str = (
            "frontier" if contract.authority_level in _FRONTIER_LEVELS else "mini"
        )

        return AgentPromptResponse(
            agent_id=contract.agent_id,
            system_prompt=system_prompt,
            authority_level=contract.authority_level,
            department=contract.department,
            max_token_budget=contract.max_token_budget,
            failure_mode=contract.failure_mode.value,
            memory_read_access=list(contract.memory_read_access),
            human_in_loop_triggers=triggers,
            model_tier=model_tier,
        )
