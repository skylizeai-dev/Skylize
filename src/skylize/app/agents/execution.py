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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from ...dal.ports import DeliverableRow, HitlEscalation, HitlQueueRepository
from ...events.bus import EventBus
from ...schemas.hitl import HitlReplayEnvelope
from ...schemas.events.decision import (
    DecisionApproved,
    DecisionDeferredToHuman,
    DecisionEvaluated,
    DecisionRejected,
)
from ...tools.base import ToolError
from ...tools.proxy import ToolProxy
from ..decision_engine.evaluator import DecisionEvaluator
from ..decision_engine.events import (
    AGENT_EXECUTE_ACTION_KIND,
    DecisionProposal,
    DecisionResult,
    hitl_id_for,
)

log = logging.getLogger(__name__)

# HITL ticket lifetime — mirrors the async writer's default
# (decision_engine/hitl_writer.py:34) so both writers agree.
_HITL_EXPIRY_HOURS = 48

# Terminal outcome -> audit `result` vocabulary. IDENTICAL to the inline engine's
# mapping (app/decision_engine/engine.py) so a synchronous decision audits the
# same way an async one does.
_DECISION_AUDIT_RESULT: dict[str, str] = {
    "approved": "success",
    "rejected": "denied",
    "deferred_to_human": "escalated",
}

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


class AgentGovernanceRejected(Exception):
    """The synchronous decision gate rejected the request (owner decision D4:
    HTTP 403 carrying the decision reason). No LLM call, no deliverable, no
    ledger row."""


class AgentDeferredToHuman(Exception):
    """The synchronous decision gate deferred the request to a human (owner
    decision D4: HTTP 202 carrying hitl_id). A hitl_queue row is written first —
    guaranteed by _govern's ordering (D3): the row is durable before the terminal
    DecisionDeferredToHuman event and the audit record are emitted, so this
    exception is never raised for a hitl_id that does not exist. No LLM call, no
    deliverable, no ledger row."""

    def __init__(self, *, hitl_id: UUID, reason: str) -> None:
        super().__init__(reason)
        self.hitl_id = hitl_id
        self.reason = reason


@dataclass(frozen=True, slots=True)
class HitlApprovalContext:
    """Proof that a HUMAN approved a previously deferred request.

    Constructed ONLY by HitlQueueService.approve after its conditional
    status='pending' claim succeeded. When execute() receives one, the
    synchronous decision gate is already satisfied — the human verdict IS the
    decision the gate deferred to — so the evaluator is not consulted again
    (re-running it would defer forever).

    This object is unreachable from the ordinary request path by construction:
    POST /api/v1/agents/execute deserializes ExecuteAgentRequest
    (extra="forbid" — any extra body field is a 422) and calls execute() with
    exactly org_id/agent_id/input_data/user_id. An HTTP body is data; it can
    never inject a Python object into a keyword argument the route does not
    pass."""

    hitl_id: UUID
    decision_id: UUID | None
    # The ORIGINAL request correlation — recorded as causation_id on the
    # replay's audit record so defer -> approve -> execute is traceable (K8).
    original_correlation_id: UUID
    approved_by: str


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
        evaluator: DecisionEvaluator | None = None,
        hitl: HitlQueueRepository | None = None,
        bus: EventBus | None = None,
        governed_org_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._deliverables = deliverables
        self._tools = tools
        self._authority = authority
        self._audit = audit
        # Synchronous decision gate (owner decisions D1/D3/D4/D5). When
        # `governed_org_ids` is empty (the default, and every existing unit-test
        # harness), the gate never runs and execution is byte-identical to today.
        self._evaluator = evaluator
        self._hitl = hitl
        self._bus = bus
        self._governed_org_ids = governed_org_ids

    async def execute(
        self,
        *,
        org_id: str,
        agent_id: str,
        input_data: dict[str, Any],
        user_id: str,
        hitl_approval: HitlApprovalContext | None = None,
    ) -> DeliverableRow:
        # 1. Resolve contract (raises AgentNotRegistered on unknown id)
        contract = self._registry.resolve(agent_id)

        # 2. Validate input. On a HITL replay this IS the K7 re-validation: the
        # stored payload is checked against the agent's CURRENT input schema
        # before the gate, the mint, and any LLM spend — schema drift surfaces
        # here as AgentInputError and nothing executes.
        input_cls = resolve_model(contract.input_schema)
        try:
            validated_input = input_cls.model_validate(input_data)
        except ValidationError as exc:
            raise AgentInputError(str(exc)) from exc

        # 2.5 Synchronous in-process governance gate (owner decisions D1/D3/D4/D5).
        # Governed orgs only (decision_engine_org_ids is the sole switch, D3); every
        # other org executes exactly as today. Runs BEFORE prompt building, the
        # governance-token mint, and any LLM spend: a reject/defer verdict means no
        # LLM call, no deliverable, and no ledger row. `run_id` is the per-request
        # correlation the decision, the mint, the LLM call, and the audit all share.
        #
        # A HitlApprovalContext skips the evaluator: the gate already ran for the
        # original request and DEFERRED — the human approval it produced is the
        # gate's resolution, and re-evaluating would defer again forever. The
        # decision events + audit for that resolution are emitted by
        # HitlQueueService before this method is ever called.
        run_id: UUID = uuid4()
        if org_id in self._governed_org_ids and hitl_approval is None:
            await self._govern(
                contract=contract, org_id=org_id, agent_id=agent_id,
                correlation_id=run_id, validated_input=validated_input, user_id=user_id,
            )

        # 3. Build prompts
        system_prompt = _build_system_prompt(contract)
        user_prompt = _build_user_prompt(agent_id, validated_input)

        # 4. Call LLM — tool loop when the contract declares invocable_tools,
        # single-shot generate() otherwise (unchanged behavior).
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
            max_tokens = min(contract.max_token_budget // 2, 4096)
            if self._authority is not None:
                token = await self._authority.mint(
                    contract, org_id=org_id, correlation_id=run_id
                )
                governance_token_id = token.token_id
                # Pre-egress governance re-validation on the canonical single-shot
                # path — the same ordered pipeline the multi-turn loop runs before
                # every turn (signature -> expiry -> revocation -> scope -> budget
                # -> delegation). Running it here means a revoked token, killed
                # agent, or out-of-budget request is refused *before* the model is
                # ever called, not merely stamped onto the deliverable afterwards.
                # `max_tokens` is the real cost this one call could add and
                # `tokens_used_so_far` is 0 (single-shot makes exactly one call, so
                # there is no running ledger — this mirrors the loop's first turn).
                allowed_tool_ids = {grant.tool_id for grant in contract.allowed_tools}
                primary_tool = (
                    "llm.generate" if "llm.generate" in allowed_tool_ids
                    else next(iter(allowed_tool_ids))
                )
                gate = validate_tool_call(
                    token=token,
                    public_key=self._authority.public_key,
                    requested_tool_id=primary_tool,
                    contract_allowed_tool_ids=allowed_tool_ids,
                    requested_token_cost=max_tokens,
                    tokens_used_so_far=0,
                    live_state=self._authority.live_state_checker(org_id),
                )
                if not gate.is_valid:
                    is_budget = gate.failed_stage is ValidationStage.BUDGET
                    if self._audit is not None:
                        await self._audit.record(
                            org_id=org_id, correlation_id=run_id,
                            action_type=(
                                "governance.budget_exceeded" if is_budget
                                else "governance.tool_call_denied"
                            ),
                            result="escalated",
                            source_agent_id=agent_id,
                            authority_level=contract.authority_level,
                            governance_token_id=token.token_id,
                            result_reason=(
                                f"{gate.failed_stage.value if gate.failed_stage else 'unknown'}: {gate.reason}"
                            ),
                        )
                    if is_budget:
                        raise TokenBudgetExceeded(gate.reason or "token budget exceeded")
                    raise GovernanceDenied(gate.reason or "governance denied pre-egress")
            else:
                governance_token_id = run_id
            llm_request = LLMGenerateRequest(
                model="fast",
                prompt=user_prompt,
                system=system_prompt,
                requested_max_tokens=max_tokens,
                temperature=0.7,
                governance_token_id=governance_token_id,
                org_id=org_id,
                correlation_id=run_id,
                agent_id=agent_id,
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
        metadata: dict[str, Any] = {
            "input": input_data, "user_id": user_id, "llm_provider": provider,
        }
        if hitl_approval is not None:
            metadata["replay_of_hitl_id"] = str(hitl_approval.hitl_id)
        row = await self._deliverables.create_deliverable(
            org_id=org_id,
            agent_id=agent_id,
            deliverable_type=deliverable_type,
            title=title,
            content_markdown=content_markdown,
            governance_token_id=governance_token_id,
            metadata=metadata,
        )
        if self._audit is not None:
            # On a HITL replay, causation_id carries the ORIGINAL request
            # correlation (K8): defer -> approve -> execute share one chain.
            await self._audit.record(
                org_id=org_id,
                correlation_id=run_id,
                action_type="agent.executed",
                result="success",
                source_agent_id=agent_id,
                authority_level=contract.authority_level,
                governance_token_id=governance_token_id,
                causation_id=(
                    hitl_approval.original_correlation_id if hitl_approval else None
                ),
                result_reason=(
                    f"deliverable={row.id} provider={provider}"
                    + (f" hitl_id={hitl_approval.hitl_id}" if hitl_approval else "")
                ),
            )
        return row

    # ── Synchronous decision gate (D1/D3/D4/D5) ──────────────────────────────

    async def _govern(
        self,
        *,
        contract: AgentContract,
        org_id: str,
        agent_id: str,
        correlation_id: UUID,
        validated_input: Any,
        user_id: str,
    ) -> None:
        """Run the synchronous decision gate and act on the verdict.

        Reuses the pure inline evaluator (owner decision D2): builds a
        DecisionProposal for this agent.execute request and calls the same
        DecisionEvaluator.evaluate the async engine uses. The audit record AND the
        terminal decision event are emitted for ALL THREE outcomes before this
        method returns or raises (owner decision D5). Returns on `approved`
        (execution proceeds unchanged); raises on the two non-approve terminals so
        the route can map them (D4): AgentGovernanceRejected -> 403,
        AgentDeferredToHuman -> 202.

        ORDERING (D3). On a deferral the hitl_queue row is written BEFORE the
        terminal event and the audit record. It used to be the other way round,
        contradicting both AgentDeferredToHuman's docstring ("A hitl_queue row is
        written first") and edge/routes/agents.py ("the hitl_queue row was
        written before this response") — and, worse, a subscriber reacting to
        DecisionDeferredToHuman raced an empty table, while an _enqueue_hitl
        failure (Postgres down, FK violation, RLS refusal) 500'd the request with
        a terminal "deferred, hitl_id=X" event and audit record already published
        for a row that would never exist. A published terminal event now always
        describes a row that is already durable.
        """
        if self._evaluator is None:
            raise RuntimeError(
                f"org_id={org_id!r} is governed (decision_engine_org_ids) but "
                "AgentExecutionService was built without a DecisionEvaluator"
            )
        proposal = _build_execution_proposal(
            contract=contract, org_id=org_id, agent_id=agent_id, correlation_id=correlation_id
        )
        result = await self._evaluator.evaluate(proposal)

        # D3: durable row first. A raise here means NO terminal event and NO
        # audit record were published — the caller gets the failure and there is
        # nothing downstream claiming a hitl_id that was never written.
        hitl_id: UUID | None = None
        if result.outcome == "deferred_to_human":
            hitl_id = hitl_id_for(proposal.proposal_id)
            await self._enqueue_hitl(
                contract, proposal, result, hitl_id,
                validated_input=validated_input, user_id=user_id,
            )

        # D3: emission failure AFTER a written row is LOGGED AT ERROR NAMING THE
        # ROW AND RE-RAISED — never swallowed. Justification: an unannounced but
        # real 'pending' row stays visible and actionable in the reviewer's queue
        # (a human approval re-emits the terminal event), whereas swallowing the
        # failure would report a decision as delivered that no subscriber ever
        # received.
        try:
            await self._emit_decision(proposal, result)  # D5: audit + terminal event
        except Exception:
            if hitl_id is not None:
                log.error(
                    "hitl_row_written_but_decision_emit_failed",
                    extra={
                        "hitl_id": str(hitl_id),
                        "org_id": proposal.org_id,
                        "correlation_id": str(proposal.correlation_id),
                        "decision_id": str(result.decision_id),
                    },
                    exc_info=True,
                )
            raise

        if result.outcome == "approved":
            return
        if result.outcome == "rejected":
            raise AgentGovernanceRejected(
                "; ".join(result.reasons) or "governance rejected the request"
            )
        # deferred_to_human — the row is already durable; surface the 202.
        assert hitl_id is not None  # set above for exactly this outcome
        raise AgentDeferredToHuman(
            hitl_id=hitl_id,
            reason="; ".join(result.reasons) or result.hitl_trigger or "deferred to human",
        )

    async def _enqueue_hitl(
        self,
        contract: AgentContract,
        proposal: DecisionProposal,
        result: DecisionResult,
        hitl_id: UUID,
        *,
        validated_input: Any,
        user_id: str,
    ) -> None:
        """Persist the HITL escalation (and its parent decision) via the app-layer
        DAL (owner decision K3). hitl_id is minted once by hitl_id_for
        (events.py:54) and is the SAME id carried by the 202 response and the
        terminal event. request_json (owner decisions K4/K6) is the serialized
        HitlReplayEnvelope a later human approval executes."""
        if self._hitl is None:
            raise RuntimeError(
                f"org_id={proposal.org_id!r} deferred to human but "
                "AgentExecutionService was built without a HitlQueueRepository"
            )
        now = datetime.now(timezone.utc)
        envelope = HitlReplayEnvelope(
            agent_id=proposal.proposing_agent_id,
            input=validated_input.model_dump(mode="json"),
            user_id=user_id,
            correlation_id=proposal.correlation_id,
        )
        await self._hitl.enqueue(
            HitlEscalation(
                decision_id=result.decision_id,
                org_id=proposal.org_id,
                correlation_id=proposal.correlation_id,
                causation_event_id=proposal.source_event_id,
                partition_key=proposal.partition_key,
                proposing_agent=result.proposing_agent,
                authority_level=result.authority_level or contract.authority_level,
                action_kind=result.action_kind,
                proposal_json=proposal.model_dump(mode="json"),
                outcome=result.outcome,
                outcome_reason="; ".join(result.reasons) or None,
                policy_version=result.policy_version,
                score_json=None,
                governance_token_id=proposal.governance_token_id,
                hitl_id=hitl_id,
                trigger_reason=(
                    result.hitl_trigger or "; ".join(result.reasons) or "deferred_to_human"
                ),
                expires_at=now + timedelta(hours=_HITL_EXPIRY_HOURS),
                created_at=now,
                request_json=envelope.model_dump(mode="json"),
            )
        )

    async def _emit_decision(self, proposal: DecisionProposal, result: DecisionResult) -> None:
        """Emit the terminal decision synchronously (owner decision D5).

        Reuses the inline engine's terminal event classes — DecisionEvaluated then
        exactly one of DecisionApproved / DecisionRejected / DecisionDeferredToHuman
        — published on the same EventBus as DecisionEngine._emit, plus the
        AuditService.record mirror. No new event type or stream is invented. A
        decision that approves is still a decision and is recorded like the others.
        """
        if self._bus is None:
            raise RuntimeError(
                f"org_id={proposal.org_id!r} is governed but AgentExecutionService "
                "was built without an EventBus for decision emission"
            )
        await self._bus.publish(
            DecisionEvaluated(
                tenant_id=proposal.org_id,
                partition_key=proposal.partition_key,
                department="decision",
                governance_token_id=proposal.governance_token_id,
                causation_id=proposal.source_event_id,
                correlation_id=proposal.correlation_id,
                payload=DecisionEvaluated.Payload(
                    decision_id=result.decision_id,
                    proposing_agent=result.proposing_agent,
                    action_kind=result.action_kind,
                    stages_completed=result.stages_completed,
                    policy_version=result.policy_version,
                ),
            )
        )
        if result.outcome == "approved":
            await self._bus.publish(
                DecisionApproved(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionApproved.Payload(
                        decision_id=result.decision_id,
                        action_kind=result.action_kind,
                        approved_scope={
                            "agent": result.proposing_agent,
                            "department": proposal.department,
                            "partition_key": proposal.partition_key,
                        },
                    ),
                )
            )
        elif result.outcome == "rejected":
            await self._bus.publish(
                DecisionRejected(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionRejected.Payload(
                        decision_id=result.decision_id,
                        action_kind=result.action_kind,
                        stage_rejected_at=result.stage_failed_at or "unknown",
                        reasons=result.reasons,
                        policy_version=result.policy_version,
                    ),
                )
            )
        else:  # deferred_to_human
            await self._bus.publish(
                DecisionDeferredToHuman(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionDeferredToHuman.Payload(
                        decision_id=result.decision_id,
                        hitl_id=hitl_id_for(proposal.proposal_id),
                        trigger_reason=result.hitl_trigger or "unspecified",
                        routed_to=result.routed_to or "human_owner",
                    ),
                )
            )
        if self._audit is not None:
            await self._audit.record(
                org_id=proposal.org_id,
                correlation_id=proposal.correlation_id,
                action_type=f"decision.{result.outcome}",
                result=_DECISION_AUDIT_RESULT[result.outcome],
                source_agent_id=result.proposing_agent or None,
                authority_level=result.authority_level,
                governance_token_id=proposal.governance_token_id,
                causation_id=proposal.source_event_id,
                partition_key=proposal.partition_key,
                inputs={"action_kind": result.action_kind, "stages": result.stages_completed},
                outputs={
                    "reasons": result.reasons,
                    "score": result.score.value if result.score else None,
                },
                result_reason="; ".join(result.reasons) or None,
            )

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
                correlation_id=correlation_id,
                agent_id=token.agent_id,
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

def _build_execution_proposal(
    *, contract: AgentContract, org_id: str, agent_id: str, correlation_id: UUID
) -> DecisionProposal:
    """Build the DecisionProposal the synchronous gate evaluates for one
    agent.execute request.

    All ids derive from the per-request correlation id (so hitl_id_for is stable
    and the decision is idempotent on it). The proposal carries no spend and
    requires_external_launch=False: the external-publication signal for THIS
    vertical is the contract's human_in_loop_triggers, read by the evaluator
    (owner decision K1), not this flag (which drives the async business-event
    path). action_kind is the authorized agent.execute kind (K2)."""
    return DecisionProposal(
        proposal_id=correlation_id,
        correlation_id=correlation_id,
        partition_key=f"agent_execute:{agent_id}:{correlation_id}",
        org_id=org_id,
        department=contract.department,
        proposing_agent_id=agent_id,
        action_kind=AGENT_EXECUTE_ACTION_KIND,
        requires_external_launch=False,
        occurred_at=datetime.now(timezone.utc),
        source_event_id=correlation_id,
        source_type=AGENT_EXECUTE_ACTION_KIND,
        metadata={},
    )


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
