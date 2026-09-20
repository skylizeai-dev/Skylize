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
from ...dal.ports import WorkflowRunWriter
from ...events.bus import EventBus
from ...schemas.events.creative import CreativeHooksGenerated
from ..audit.service import AuditService
from ..governance.authority import GovernanceAuthority, GovernanceDenied
from .runner import AgentRunner, RunnerMeta
from .workflows.creative_workflow import GraphDeps, build_creative_graph

# Stages that indicate the agent overstepped its grant → feed the circuit breaker.
_VIOLATION_STAGES = {"scope", "budget", "delegation"}

#: The one workflow this orchestrator can actually run. `build_creative_graph`
#: is the only graph builder constructed below, so it is the only workflow name
#: a run row can legitimately carry. When a second graph is built, this stops
#: being a constant — it does NOT become a hand-written catalogue.
CREATIVE_WORKFLOW_NAME = "creative"


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
        run_writer: WorkflowRunWriter | None = None,
    ) -> None:
        self._registry = registry
        self._authority = authority
        self._audit = audit
        self._bus = bus
        # Optional by design: on the memory backend there is no durable store
        # for run history, and a run must still execute. None means "no run row
        # is kept", never "no run happens".
        self._run_writer = run_writer
        self._graph = build_creative_graph(
            GraphDeps(
                runner=runner,
                public_key=authority.public_key,
                live_state_for=authority.live_state_checker,
            )
        )

    @property
    def registry(self) -> AgentRegistry:
        """The contract registry this orchestrator resolves against.

        Read-only accessor for the workflow catalogue route, which must derive
        what it advertises from what this orchestrator can actually resolve —
        not from a second, hand-maintained list that would drift.
        """
        return self._registry

    async def invoke(
        self,
        agent_id: str,
        payload: dict[str, Any],
        *,
        org_id: str,
        correlation_id: UUID | None = None,
    ) -> WorkflowResult:
        correlation_id = correlation_id or uuid4()

        # 0. Open the run row. BEST EFFORT, ALWAYS: `_start_run` never raises,
        # so a database that is down costs the operator a history row and
        # nothing else. The run itself proceeds. The row is opened BEFORE the
        # resolve gate so that a denied run is still a run the console can see
        # — a denial nobody can find is the failure mode a governance console
        # exists to prevent.
        await self._start_run(org_id, correlation_id, agent_id)

        # 1. Resolve (fail closed)
        try:
            contract = self._registry.resolve(agent_id)
        except AgentNotRegistered as exc:
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.resolve", result="denied", result_reason=str(exc),
            )
            return await self._finish_run(
                WorkflowResult("denied", agent_id, correlation_id, reason=str(exc)),
                org_id, stage="resolve",
            )

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
            return await self._finish_run(
                WorkflowResult("denied", agent_id, correlation_id, reason=str(exc)),
                org_id, stage="gate",
            )

        # 3. Validate input against the contract's declared input_schema
        try:
            input_model = resolve_model(contract.input_schema).model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            await self._audit.record(
                org_id=org_id, correlation_id=correlation_id,
                action_type="orchestrator.validate_input", result="failed",
                source_agent_id=agent_id, result_reason=str(exc),
            )
            return await self._finish_run(
                WorkflowResult(
                    "failed", agent_id, correlation_id, reason=f"invalid input: {exc}"
                ),
                org_id, stage="validate_input",
            )

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
            return await self._finish_run(
                WorkflowResult("failed", agent_id, correlation_id, token.token_id,
                               reason=f"invalid output: {exc}"),
                org_id, stage="validate_output",
            )

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
        return await self._finish_run(
            WorkflowResult(
                "completed", agent_id, correlation_id, token.token_id,
                output=output_model.model_dump(mode="json"), event_type=event_type,
            ),
            org_id,
        )

    # -- run history (best effort) -------------------------------------------
    #
    # WHY HERE and not in the route. `Orchestrator.invoke` is "the single entry
    # to the agent layer" (module docstring) and owns the whole lifecycle,
    # including the three pre-graph denial paths — resolve, gate, validate_input
    # — that return before any graph runs. A route-level wrapper sees only what
    # the route calls, so every future trigger (a scheduler, another route, the
    # autonomous runner) would have to re-implement the same bookkeeping and
    # would drift. Persisting at the orchestrator means one writer, one
    # lifecycle, and a denied run recorded as faithfully as a completed one.
    #
    # BOTH METHODS SWALLOW EVERYTHING. A run must not fail because its history
    # row could not be written; that is the same best-effort posture
    # `OrgAutonomyModeDAL.set_mode` takes around its audit call, applied to the
    # weaker of the two records. The audit trail remains the evidence of record;
    # this table is the operator's view of it.

    async def _start_run(self, org_id: str, correlation_id: UUID, agent_id: str) -> None:
        if self._run_writer is None:
            return
        try:
            await self._run_writer.start_run(
                run_id=correlation_id,
                org_id=org_id,
                workflow_name=CREATIVE_WORKFLOW_NAME,
                agent_id=agent_id,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 — history is best effort; the run is not
            pass

    async def _finish_run(
        self, result: WorkflowResult, org_id: str, *, stage: str | None = None
    ) -> WorkflowResult:
        """Close the run row and return `result` unchanged.

        Returns its argument so every terminal path in `invoke` can wrap its
        existing return value without restructuring — and so no path can close
        the row without also being the path that returns.
        """
        if self._run_writer is None:
            return result
        try:
            await self._run_writer.finish_run(
                run_id=result.correlation_id,
                org_id=org_id,
                status=result.status,
                failure_stage=stage,
                reason=result.reason,
            )
        except Exception:  # noqa: BLE001 — history is best effort; the run is not
            pass
        return result

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
        # `stage` here is the graph's own `failed_stage` — where the run
        # STOPPED. It is not progress: no node records having been reached.
        return await self._finish_run(
            WorkflowResult(
                "failed", contract.agent_id, correlation_id, token_id, reason=reason
            ),
            org_id, stage=stage,
        )

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
