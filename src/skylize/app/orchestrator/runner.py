"""
The AgentRunner seam.

The runner is what executes inside the LangGraph "agent step" node. In Sprint 2
the real runner wraps a CrewAI crew whose tools route through the tool proxy and
the LLM gateway. The foundation/core ships a deterministic `StubAgentRunner` so
the whole control plane (resolve → gate → mint → run → validate → emit → audit)
is end-to-end runnable WITHOUT a model provider; swapping in the LLM-backed
runner is a constructor change, not a graph change (agent_runtime.md §10).
"""

from __future__ import annotations

from typing import Any, Protocol

from ...contracts.base import AgentContract, GovernanceToken


class AgentRunner(Protocol):
    async def run(
        self, *, contract: AgentContract, input_payload: dict[str, Any], token: GovernanceToken
    ) -> dict[str, Any]:
        """Execute the agent and return a dict matching `contract.output_schema`."""
        ...


class StubAgentRunner:
    """Deterministic runner for the worked creative path — no LLM calls."""

    async def run(
        self, *, contract: AgentContract, input_payload: dict[str, Any], token: GovernanceToken
    ) -> dict[str, Any]:
        if contract.agent_id == "hook_generator_agent":
            brief_id = input_payload["brief_id"]
            product = input_payload.get("product", "the product")
            count = int(input_payload.get("count", 3))
            hooks = [f"Hook {i + 1}: why {product} changes everything" for i in range(count)]
            return {"brief_id": brief_id, "hooks": hooks}

        if contract.agent_id == "copy_director":
            brief_id = input_payload["brief_id"]
            return {
                "brief_id": brief_id,
                "hooks": ["Stop scrolling.", "You need this."],
                "body_copy": ["Concise, on-brand body copy."],
                "ctas": ["Shop now"],
            }

        raise NotImplementedError(
            f"StubAgentRunner has no producer for {contract.agent_id!r}; "
            "wire the LLM-backed runner in Sprint 2"
        )
