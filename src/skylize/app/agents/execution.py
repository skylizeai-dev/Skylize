"""
AgentExecutionService — the agent execution pipeline.

Single-shot path (contract.invocable_tools is empty — every existing MVP
contract, unchanged):
  1. Resolve contract from AgentRegistry (fail closed on unknown agent_id).
  2. Validate customer input against contract's input_schema (Pydantic).
  3. Build system + user prompt from contract metadata and validated input.
  4. Call LLMGateway.generate() — routes to Demo or Anthropic adapter.
  5. Parse + validate LLM output against contract's output_schema.
  6. Format output as markdown.
  7. Persist via DeliverableService.create_deliverable().
  8. Return the created DeliverableRow.

Multi-turn path (contract.invocable_tools is non-empty):
  3a. Mint a real signed GovernanceToken scoped to the contract (Governance
      Authority) — this is the token the ToolProxy validates on every call.
  4a. Loop up to contract.max_tool_iterations: call
      LLMGateway.generate_with_tools(); if the model asks for a tool, dispatch
      each tool_use block through the ToolProxy (IF-TOOL), feed results back
      as tool_result blocks, and call again. Otherwise take the final text.
      Exceeding max_tool_iterations is a governance escalation (audited),
      not a silent truncation.
  5-8. Same as the single-shot path.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from ...adapters.llm.gateway import (
    LLMContentBlock,
    LLMGateway,
    LLMGenerateRequest,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    TokenBudgetExceeded,
)
from ...app.audit.service import AuditService
from ...app.deliverables.service import DeliverableService
from ...app.governance.authority import GovernanceAuthority, GovernanceDenied
from ...contracts.base import AgentContract
from ...contracts.registry import AgentRegistry, resolve_model
from ...contracts.token import ValidationStage, validate_tool_call
from ...dal.ports import DeliverableRow
from ...tools.base import ToolError
from ...tools.proxy import ToolProxy

log = logging.getLogger(__name__)

_AGENT_DELIVERABLE_TYPE: dict[str, str] = {
    "hook_generator_agent": "marketing_copy",
    "ad_copy_agent": "ad_creative",
    "caption_writer_agent": "social_post",
    "script_writer_agent": "other",
    "cta_optimizer_agent": "marketing_copy",
    "copy_director": "marketing_copy",
    "vp_creative": "strategy_doc",
    "director_growth": "strategy_doc",
    "seo_keyword_agent": "seo_report",
    "cfo_agent": "other",
}


class AgentInputError(Exception):
    """Customer input failed validation against the contract's input_schema."""


class AgentOutputError(Exception):
    """LLM response failed to parse or validate against the output_schema."""


class AgentToolLoopExceeded(AgentOutputError):
    """The tool-use loop hit max_tool_iterations without a final answer."""


class AgentExecutionService:
    def __init__(
        self,
        registry: AgentRegistry,
        llm: LLMGateway,
        deliverables: DeliverableService,
        *,
        tools: ToolProxy | None = None,
        authority: GovernanceAuthority | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._deliverables = deliverables
        self._tools = tools
        self._authority = authority
        self._audit = audit

    async def execute(
        self,
        *,
        org_id: str,
        agent_id: str,
        input_data: dict[str, Any],
        user_id: str,
    ) -> DeliverableRow:
        # 1. Resolve contract (raises AgentNotRegistered on unknown id)
        contract = self._registry.resolve(agent_id)

        # 2. Validate input
        input_cls = resolve_model(contract.input_schema)
        try:
            validated_input = input_cls.model_validate(input_data)
        except ValidationError as exc:
            raise AgentInputError(str(exc)) from exc

        # 3. Build prompts
        system_prompt = _build_system_prompt(contract)
        user_prompt = _build_user_prompt(agent_id, validated_input)

        # 4. Call LLM — tool loop when the contract declares invocable_tools,
        # single-shot generate() otherwise (unchanged behavior).
        run_id: UUID = uuid4()
        if contract.invocable_tools:
            token, response_text, provider, total_tokens = await self._execute_with_tools(
                contract=contract, org_id=org_id, correlation_id=run_id,
                system_prompt=system_prompt, user_prompt=user_prompt,
            )
            governance_token_id = token.token_id
            log.info(
                "agent_llm_response",
                extra={"agent_id": agent_id, "provider": provider, "tokens": total_tokens},
            )
        else:
            # Single-shot is governed too when the authority is wired (the
            # composition root always wires it): mint refuses suspended /
            # kill-switched agents and stamps the run with a real signed token,
            # so the deliverable's governance_token_id is verifiable — not a
            # bare correlation id. Without an authority (unit-test harnesses),
            # the legacy ungoverned path is preserved.
            if self._authority is not None:
                token = await self._authority.mint(
                    contract, org_id=org_id, correlation_id=run_id
                )
                governance_token_id = token.token_id
            else:
                governance_token_id = run_id
            llm_request = LLMGenerateRequest(
                model="fast",
                prompt=user_prompt,
                system=system_prompt,
                requested_max_tokens=min(contract.max_token_budget // 2, 4096),
                temperature=0.7,
                governance_token_id=governance_token_id,
                org_id=org_id,
            )
            response = await self._llm.generate(llm_request)
            response_text = response.text
            provider = response.provider
            log.info(
                "agent_llm_response",
                extra={"agent_id": agent_id, "provider": provider, "tokens": response.usage.total_tokens},
            )

        # 5. Parse + validate output
        output_cls = resolve_model(contract.output_schema)
        try:
            raw = json.loads(response_text)
            # Echo input-provided correlation fields the model isn't expected to
            # invent (e.g. brief_id, which ties the output back to its brief) from
            # the validated input, when the output schema shares that field and the
            # model omitted it. Same principle as the cfo_agent recompute below:
            # deterministic pass-through values never depend on the model.
            if isinstance(raw, dict):
                input_data_json = validated_input.model_dump(mode="json")
                for field in output_cls.model_fields:
                    if field not in raw and field in input_data_json:
                        raw[field] = input_data_json[field]
            validated_output = output_cls.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AgentOutputError(
                f"LLM output failed schema validation ({output_cls.__name__}): {exc}"
            ) from exc

        # cfo_agent's budget_summary: total/flags are arithmetic, not narrative
        # — recompute deterministically in Python rather than trust the model.
        if agent_id == "cfo_agent":
            total, flags = _compute_budget_summary(getattr(validated_input, "line_items", []))
            validated_output = validated_output.model_copy(update={"total": total, "flags": flags})

        # 6. Format as markdown
        content_markdown = _format_markdown(agent_id, validated_input, validated_output)
        title = _generate_title(agent_id, validated_input)

        # 7. Persist
        deliverable_type = _AGENT_DELIVERABLE_TYPE.get(agent_id, "other")
        row = await self._deliverables.create_deliverable(
            org_id=org_id,
            agent_id=agent_id,
            deliverable_type=deliverable_type,
            title=title,
            content_markdown=content_markdown,
            governance_token_id=governance_token_id,
            metadata={"input": input_data, "user_id": user_id, "llm_provider": provider},
        )
        if self._audit is not None:
            await self._audit.record(
                org_id=org_id,
                correlation_id=run_id,
                action_type="agent.executed",
                result="success",
                source_agent_id=agent_id,
                authority_level=contract.authority_level,
                governance_token_id=governance_token_id,
                result_reason=f"deliverable={row.id} provider={provider}",
            )
        return row

    async def _execute_with_tools(
        self,
        *,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[Any, str, str, int]:
        if self._tools is None or self._authority is None or self._audit is None:
            raise RuntimeError(
                f"agent_id={contract.agent_id!r} declares invocable_tools but "
                "AgentExecutionService was built without a ToolProxy/GovernanceAuthority/AuditService"
            )

        token = await self._authority.mint(contract, org_id=org_id, correlation_id=correlation_id)

        available = []
        for tool_id in contract.invocable_tools:
            if self._tools.registry.has(tool_id):
                available.append(self._tools.registry.resolve(tool_id))
            else:
                log.warning("agent_tool_not_registered", extra={"agent_id": contract.agent_id, "tool_id": tool_id})

        messages: list[LLMMessage] = [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text=user_prompt)])
        ]
        provider = "unknown"
        total_tokens = 0
        max_tokens = min(contract.max_token_budget // 2, 4096)
        allowed_tool_ids = {grant.tool_id for grant in contract.allowed_tools}
        primary_tool = (
            "llm.generate" if "llm.generate" in allowed_tool_ids else next(iter(allowed_tool_ids))
        )

        for iteration in range(contract.max_tool_iterations):
            # Pre-egress governance re-validation. `total_tokens` is the real
            # running ledger accrued from prior turns and `max_tokens` is the
            # ceiling this turn could add — so the BUDGET stage can actually trip
            # and the run stops before an over-budget call reaches a provider.
            # Re-running the ordered pipeline each turn also catches a token
            # revoked / agent killed mid-loop.
            gate = validate_tool_call(
                token=token,
                public_key=self._authority.public_key,
                requested_tool_id=primary_tool,
                contract_allowed_tool_ids=allowed_tool_ids,
                requested_token_cost=max_tokens,
                tokens_used_so_far=total_tokens,
                live_state=self._authority.live_state_checker(org_id),
            )
            if not gate.is_valid:
                is_budget = gate.failed_stage is ValidationStage.BUDGET
                await self._audit.record(
                    org_id=org_id, correlation_id=correlation_id,
                    action_type=(
                        "governance.budget_exceeded" if is_budget
                        else "governance.tool_call_denied"
                    ),
                    result="escalated",
                    source_agent_id=contract.agent_id,
                    authority_level=contract.authority_level,
                    governance_token_id=token.token_id,
                    result_reason=(
                        f"{gate.failed_stage.value if gate.failed_stage else 'unknown'}: {gate.reason}"
                    ),
                )
                if is_budget:
                    raise TokenBudgetExceeded(gate.reason or "token budget exceeded")
                raise GovernanceDenied(gate.reason or "governance denied mid-run")

            request = LLMGenerateWithToolsRequest(
                model="fast",
                system=system_prompt,
                messages=messages,
                requested_max_tokens=max_tokens,
                temperature=0.7,
                governance_token_id=token.token_id,
                org_id=org_id,
            )
            response = await self._llm.generate_with_tools(request, available)
            provider = response.provider
            total_tokens += response.usage.total_tokens
            log.info(
                "agent_llm_tool_turn",
                extra={
                    "agent_id": contract.agent_id, "iteration": iteration,
                    "stop_reason": response.stop_reason, "tokens": response.usage.total_tokens,
                },
            )

            if response.stop_reason != "tool_use":
                return token, response.text, provider, total_tokens

            messages.append(LLMMessage(role="assistant", content=response.content))
            result_blocks: list[LLMContentBlock] = []
            for call in (b for b in response.content if b.kind == "tool_use"):
                result_blocks.append(
                    await self._invoke_tool(
                        call=call, token=token, contract=contract,
                        org_id=org_id, correlation_id=correlation_id,
                    )
                )
            messages.append(LLMMessage(role="user", content=result_blocks))

        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="governance.tool_loop_exceeded", result="escalated",
            source_agent_id=contract.agent_id, authority_level=contract.authority_level,
            governance_token_id=token.token_id,
            result_reason=f"max_tool_iterations={contract.max_tool_iterations} exceeded",
        )
        raise AgentToolLoopExceeded(
            f"agent_id={contract.agent_id!r} exceeded max_tool_iterations="
            f"{contract.max_tool_iterations} without a final answer"
        )

    async def _invoke_tool(
        self,
        *,
        call: LLMContentBlock,
        token: Any,
        contract: AgentContract,
        org_id: str,
        correlation_id: UUID,
    ) -> LLMContentBlock:
        assert self._tools is not None
        try:
            result = await self._tools.invoke(
                tool_id=call.tool_name or "",
                input_data=call.tool_input or {},
                governance_token=token,
                contract=contract,
                org_id=org_id,
                correlation_id=correlation_id,
            )
            return LLMContentBlock(
                kind="tool_result",
                tool_use_id=call.tool_use_id,
                tool_output=json.dumps(result.output_json()),
            )
        except ToolError as exc:
            return LLMContentBlock(
                kind="tool_result", tool_use_id=call.tool_use_id,
                tool_output=str(exc), is_error=True,
            )

    def list_agents(self) -> list[dict[str, Any]]:
        """Return agent metadata + JSON Schema for each registered agent."""
        result = []
        for contract in self._registry.all():
            try:
                input_cls = resolve_model(contract.input_schema)
                schema = input_cls.model_json_schema()
            except Exception:
                schema = {}
            result.append({
                "agent_id": contract.agent_id,
                "name": _friendly_name(contract.agent_id),
                "description": contract.agent_role,
                "department": contract.department,
                "authority_level": contract.authority_level,
                "input_schema": schema,
            })
        return result


# ── Private helpers ──────────────────────────────────────────────────────────

def _build_system_prompt(contract: Any) -> str:
    lines = [
        f"You are a {contract.agent_role}.",
        f"Department: {contract.department}.",
    ]
    if contract.invocable_tools:
        lines.append(
            "You have tools available — use them to gather real information "
            "before answering; do not guess when a tool can tell you."
        )
    lines.append(
        "Return ONLY valid JSON matching the requested output schema. "
        "No prose before or after the JSON object."
    )
    return "\n".join(lines)


def _compute_budget_summary(line_items: list[Any]) -> tuple[float, list[str]]:
    """Deterministic total + concentration flags — never trusted to the model."""
    total = sum(item.amount for item in line_items)
    flags: list[str] = []
    if total > 0:
        for item in line_items:
            if item.amount > 0.4 * total:
                pct = item.amount / total
                flags.append(
                    f"{item.category} is {pct:.0%} of total spend "
                    f"(${item.amount:,.2f}) — exceeds the 40% concentration threshold"
                )
    return total, flags


def _build_user_prompt(agent_id: str, validated_input: Any) -> str:
    # mode="json" so UUID/datetime fields serialize to strings (a plain
    # model_dump() leaves them as objects that json.dumps cannot encode).
    input_json = json.dumps(validated_input.model_dump(mode="json"), indent=2)
    return (
        f"Agent: {agent_id}\n\n"
        f"Input:\n{input_json}\n\n"
        "Generate output as a JSON object."
    )


def _format_markdown(agent_id: str, inp: Any, out: Any) -> str:
    inp_data = inp.model_dump()
    out_data = out.model_dump()

    if agent_id == "hook_generator_agent":
        brand = inp_data.get("brand_name", "")
        lines = [
            f"# Marketing Hooks — {brand}",
            "",
        ]
        hooks: list[str] = out_data.get("hooks", [])
        for i, hook in enumerate(hooks, 1):
            lines.append(f"{i}. {hook}")
        lines += [
            "",
            "---",
            f"*Generated by `{agent_id}` · brand: {brand} · audience: {inp_data.get('target_audience', '')} · tone: {inp_data.get('tone', '')}*",
        ]
        return "\n".join(lines)

    if agent_id == "seo_keyword_agent":
        topic = inp_data.get("topic", "")
        lines = [f"# SEO Keyword Research — {topic}", "", "## Primary Keywords"]
        for kw in out_data.get("primary_keywords", []):
            lines.append(f"- {kw}")
        lines += [
            "",
            "## Keyword Difficulty Notes",
            out_data.get("keyword_difficulty_notes", ""),
            "",
            "## Content Angle Suggestions",
        ]
        for angle in out_data.get("content_angle_suggestions", []):
            lines.append(f"- {angle}")
        lines += [
            "",
            "---",
            f"*Generated by `{agent_id}` · topic: {topic} · market: {inp_data.get('target_market', '')}*",
        ]
        return "\n".join(lines)

    if agent_id == "cfo_agent":
        dept = inp_data.get("department", "")
        period = inp_data.get("period", "")
        lines = [
            f"# Budget Summary — {dept} ({period})",
            "",
            out_data.get("summary", ""),
            "",
            f"**Total:** ${out_data.get('total', 0):,.2f}",
        ]
        flags = out_data.get("flags", [])
        if flags:
            lines += ["", "## Flags"]
            for flag in flags:
                lines.append(f"- {flag}")
        lines += [
            "",
            "## Recommendation",
            out_data.get("recommendation", ""),
            "",
            "---",
            f"*Generated by `{agent_id}`*",
        ]
        return "\n".join(lines)

    # Generic formatter for all other agents
    lines = [f"# Output — {_friendly_name(agent_id)}", ""]
    for key, value in out_data.items():
        if isinstance(value, list):
            lines.append(f"## {key.replace('_', ' ').title()}")
            for item in value:
                lines.append(f"- {item}")
            lines.append("")
        else:
            lines.append(f"## {key.replace('_', ' ').title()}")
            lines.append(str(value))
            lines.append("")
    lines.append(f"---\n*Generated by `{agent_id}`*")
    return "\n".join(lines)


def _generate_title(agent_id: str, inp: Any) -> str:
    inp_data = inp.model_dump()
    if agent_id == "hook_generator_agent":
        brand = inp_data.get("brand_name", "")
        return f"Hooks — {brand}" if brand else "Generated Hooks"
    if agent_id == "seo_keyword_agent":
        topic = inp_data.get("topic", "")
        return f"SEO Keywords — {topic}" if topic else "SEO Keyword Research"
    if agent_id == "cfo_agent":
        dept = inp_data.get("department", "")
        period = inp_data.get("period", "")
        return f"Budget Summary — {dept} {period}".strip()
    return f"{_friendly_name(agent_id)} Output"


def _friendly_name(agent_id: str) -> str:
    return agent_id.replace("_agent", "").replace("_", " ").title()
