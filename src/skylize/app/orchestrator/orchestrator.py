"""
The Orchestrator — the single entry to the agent layer (system_architecture.md §5.1).

Per invocation it: resolves the contract (fail closed) → gates on governance
state → validates input → mints a run-scoped token → runs the LangGraph workflow
→ validates output → wraps it as a typed event and publishes → audits every
step. Both LangGraph and the runner sit behind this facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from ...contracts.base import AgentContract
from ...contracts.registry import AgentNotRegistered, AgentRegistry, resolve_model
from ...events.bus import EventBus
from ...schemas.events.creative import CreativeHooksGenerated
from ..audit.service import AuditService
from ..governance.authority import GovernanceAuthority, GovernanceDenied
from .runner import AgentRunner, RunnerMeta
from .workflows.creative_workflow import GraphDeps, build_creative_graph

# Stages that indicate the agent overstepped its grant → feed the circuit breaker.
_VIOLATION_STAGES = {"scope", "budget", "delegation"}


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    status: str  # completed | denied | failed
    agent_id: str
    correlation_id: UUID
    token_id: UUID | None = None
    output: dict[str, Any] | None = None
    event_type: str | None = None
    reason: str | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        authority: GovernanceAuthority,
        audit: AuditService,
        bus: EventBus,
        runner: AgentRunner,
    ) -> None:
        self._registry = registry
        self._authority = authority
        self._audit = audit
        self._bus = bus
        self._graph = build_creative_graph(
            GraphDeps(
                runner=runner,
                public_key=authority.public_key,
                live_state_for=authority.live_state_checker,
            )
        )

    async def invoke(
        self,
        agent_id: str,
        payload: dict[str, Any],
        *,
        org_id: str,
        correlation_id: UUID | None = None,
    ) -> WorkflowResult:
        correlation_id = correlation_id or uuid4()

        # 1. Resolve (fail closed)
        try:
            contract = self._registry.resolve(agent_id)
        except AgentNotRegistered as exc:
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.resolve", result="denied", result_reason=str(exc),
            )
            return WorkflowResult("denied", agent_id, correlation_id, reason=str(exc))

        # 2. Governance gate
        try:
            await self._authority.assert_active(agent_id, org_id)
        except GovernanceDenied as exc:
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.gate", result="denied",
                source_agent_id=agent_id, authority_level=contract.authority_level,
                result_reason=str(exc),
            )
            return WorkflowResult("denied", agent_id, correlation_id, reason=str(exc))

        # 3. Validate input against the contract's declared input_schema
        try:
            input_model = resolve_model(contract.input_schema).model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.validate_input", result="failed",
                source_agent_id=agent_id, result_reason=str(exc),
            )
            return WorkflowResult("failed", agent_id, correlation_id, reason=f"invalid input: {exc}")

        # 4. Mint a run-scoped token
        token = await self._authority.mint(
            contract, org_id=org_id, correlation_id=correlation_id
        )

        # 5. Run the workflow graph
        final = await self._graph.ainvoke(
            {
                "org_id": org_id,
                "correlation_id": correlation_id,
                "agent_id": agent_id,
                "contract": contract,
                "token": token,
                "input_payload": input_model.model_dump(),
                "output": None,
                "run_meta": None,
                "failure": None,
                "failed_stage": None,
            },
            config={"configurable": {"thread_id": str(correlation_id)}},
        )

        if final.get("failure"):
            return await self._on_failure(contract, org_id, correlation_id, token.token_id, final)

        # 6. Validate output, wrap as event, publish, audit
        output = final["output"]
        try:
            output_model = resolve_model(contract.output_schema).model_validate(output)
        except Exception as exc:  # noqa: BLE001
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.validate_output", result="failed",
                source_agent_id=agent_id, governance_token_id=token.token_id,
                result_reason=str(exc),
            )
            return WorkflowResult("failed", agent_id, correlation_id, token.token_id,
                                  reason=f"invalid output: {exc}")

        meta = final.get("run_meta") or RunnerMeta(provider="unknown", model="unknown", total_tokens=0)
        event_type = await self._publish_output(contract, output_model.model_dump(), org_id,
                                                 correlation_id, token.token_id, meta)
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="orchestrator.run", result="success",
            source_agent_id=agent_id, authority_level=contract.authority_level,
            governance_token_id=token.token_id,
            inputs=input_model.model_dump(mode="json"),
            outputs=output_model.model_dump(mode="json"),
        )
        return WorkflowResult(
            "completed", agent_id, correlation_id, token.token_id,
            output=output_model.model_dump(mode="json"), event_type=event_type,
        )

    # -- helpers ------------------------------------------------------------
    async def _on_failure(
        self, contract: AgentContract, org_id: str, correlation_id: UUID,
        token_id: UUID, final: dict[str, Any],
    ) -> WorkflowResult:
        reason = final.get("failure") or "unknown"
        stage = final.get("failed_stage")
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="orchestrator.run", result="failed",
            source_agent_id=contract.agent_id, authority_level=contract.authority_level,
            governance_token_id=token_id, result_reason=f"[{stage}] {reason}",
        )
        # A scope/budget/delegation failure is an agent overstep → circuit breaker.
        if stage in _VIOLATION_STAGES:
            await self._authority.record_violation(
                agent_id=contract.agent_id, org_id=org_id,
                reason=f"{stage}: {reason}", correlation_id=correlation_id,
            )
        return WorkflowResult("failed", contract.agent_id, correlation_id, token_id, reason=reason)

    async def _publish_output(
        self, contract: AgentContract, output: dict[str, Any], org_id: str,
        correlation_id: UUID, token_id: UUID, meta: RunnerMeta,
    ) -> str | None:
        """Wrap the validated agent output into its typed business event."""
        if contract.agent_id == "hook_generator_agent":
            # Operator-executed hooks carry no upstream brief; the run's
            # correlation_id is the brief surrogate so the partition key stays
            # stable and the payload stays honest about its origin.
            brief_ref = output.get("brief_id") or correlation_id
            event = CreativeHooksGenerated(
                tenant_id=org_id, partition_key=f"brief:{brief_ref}",
                department=contract.department, source_agent_id=contract.agent_id,
                authority_level=contract.authority_level, governance_token_id=token_id,
                correlation_id=correlation_id,
                payload=CreativeHooksGenerated.Payload(
                    brief_id=brief_ref, hooks=output["hooks"],
                    model_used=meta.model, token_cost=meta.total_tokens,
                ),
            )
            await self._bus.publish(event)
            return event.type
        return None  # other agents: no business event mapped in MVP core
