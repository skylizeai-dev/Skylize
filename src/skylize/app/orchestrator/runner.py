"""
The AgentRunner seam.

The runner is what executes inside the LangGraph "agent step" node.
`LLMStepRunner` is the production runner: it drives the governed LLM gateway
(Anthropic when a key is configured, the deterministic `[DEMO]` adapter
otherwise) using the same prompt builders as `AgentExecutionService`, so an
agent behaves identically whether reached via `/agents/execute` or a workflow.
`StubAgentRunner` remains for tests that need fully deterministic output with
zero model involvement. Swapping runners is a constructor change, not a graph
change (agent_runtime.md §10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ...adapters.llm.gateway import LLMGateway, LLMGenerateRequest
from ...contracts.base import AgentContract, GovernanceToken
from ...contracts.registry import resolve_model
from ..agents.execution import _build_system_prompt, _build_user_prompt


@dataclass(frozen=True, slots=True)
class RunnerMeta:
    """Honest provenance for a run: which model produced it, at what cost."""

    provider: str
    model: str
    total_tokens: int


class AgentRunner(Protocol):
    async def run(
        self,
        *,
        contract: AgentContract,
        input_payload: dict[str, Any],
        token: GovernanceToken,
        org_id: str,
        correlation_id: UUID,
    ) -> tuple[dict[str, Any], RunnerMeta]:
        """Execute the agent; return (output matching `contract.output_schema`, meta).

        `correlation_id` is the run-level id the Orchestrator minted (or was
        handed) — threaded so LLM egress carries the SAME id the run's audit
        trail uses, never a fresh one minted at the call site.
        """
        ...


class StubAgentRunner:
    """Deterministic runner — no LLM calls; meta honestly reports zero cost."""

    async def run(
        self,
        *,
        contract: AgentContract,
        input_payload: dict[str, Any],
        token: GovernanceToken,
        org_id: str,
        correlation_id: UUID,
    ) -> tuple[dict[str, Any], RunnerMeta]:
        meta = RunnerMeta(provider="stub", model="stub", total_tokens=0)

        if contract.agent_id == "hook_generator_agent":
            product = input_payload.get("product_description", "the product")
            count = int(input_payload.get("count", 3))
            hooks = [f"Hook {i + 1}: why {product} changes everything" for i in range(count)]
            return {"hooks": hooks}, meta

        if contract.agent_id == "copy_director":
            brief_id = input_payload["brief_id"]
            return {
                "brief_id": brief_id,
                "hooks": ["Stop scrolling.", "You need this."],
                "body_copy": ["Concise, on-brand body copy."],
                "ctas": ["Shop now"],
            }, meta

        raise NotImplementedError(
            f"StubAgentRunner has no producer for {contract.agent_id!r}; "
            "use LLMStepRunner for LLM-backed agents"
        )


class LLMStepRunner:
    """LLM-backed AgentRunner — the production agent step.

    The output is the model's JSON, parsed but NOT schema-validated here; the
    Orchestrator validates against `contract.output_schema` right after (one
    validator for every runner).
    """

    def __init__(self, llm: LLMGateway) -> None:
        self._llm = llm

    async def run(
        self,
        *,
        contract: AgentContract,
        input_payload: dict[str, Any],
        token: GovernanceToken,
        org_id: str,
        correlation_id: UUID,
    ) -> tuple[dict[str, Any], RunnerMeta]:
        input_model = resolve_model(contract.input_schema).model_validate(input_payload)
        request = LLMGenerateRequest(
            model="fast",
            prompt=_build_user_prompt(contract.agent_id, input_model),
            system=_build_system_prompt(contract),
            requested_max_tokens=min(contract.max_token_budget // 2, 4096),
            temperature=0.7,
            governance_token_id=token.token_id,
            org_id=org_id,
            correlation_id=correlation_id,
            agent_id=token.agent_id,
        )
        response = await self._llm.generate(request)
        try:
            output = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc
        if not isinstance(output, dict):
            raise ValueError("LLM output must be a JSON object")
        meta = RunnerMeta(
            provider=response.provider,
            model=response.concrete_model,
            total_tokens=response.usage.total_tokens,
        )
        return output, meta
