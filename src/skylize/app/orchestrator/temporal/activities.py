"""Temporal activity definitions for the workflow engine.

Activities are the units of work that Temporal orchestrates. Each is decorated
with ``@activity.defn`` so Temporal can register, schedule, and retry them.
They are pure functions of their dataclass argument — no shared mutable state.

``WorkflowActivities`` groups related activities as instance methods so
dependencies (repo, judge, minter) are injected once at construction rather than
threaded through every call.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from temporalio import activity

from ....dal.ports import WorkflowRepository, WorkflowRunStepRow
from .judge import NodeJudge


# ---------------------------------------------------------------------------
# Request / response dataclasses (plain dataclasses — Temporal serialises them)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RunContext:
    """Per-run tenancy envelope threaded through every activity call."""
    org_id: str
    run_id: str
    workflow_id: str
    correlation_id: str
    thread_id: str
    triggered_by: str
    # The run's governance token id — DISTINCT from correlation_id. It is the
    # ai_cost_ledger run key (cost_ledger.py:114 "run_id == governance token id"),
    # so it must never be aliased to correlation_id: a judge-path ledger row that
    # recorded run_id == correlation_id would misattribute the charge. Typed as
    # UUID (not str) on purpose — a caller that hands over the wrong id, e.g. the
    # correlation_id, fails to type-check instead of silently recording a wrong
    # attribution. It MUST be populated by the workflow definitions from the token
    # the minter mints at run start; the minter is currently None
    # (worker.py:51-55) and no workflow definitions are registered yet
    # (worker.py:19-21), so today only tests construct RunContext. Appended last
    # so any positional construction of the older six-field shape fails loudly
    # rather than silently shifting fields onto it.
    governance_token_id: UUID


@dataclasses.dataclass
class JudgeRequest:
    ctx: RunContext
    node_name: str
    output: dict[str, Any]
    success_criteria: dict[str, Any]
    # agent_id of the node whose output is being judged — the engine that
    # constructs JudgeRequest tracks it per node (see StepRecordRequest).
    # Threaded so the judge's own LLM egress carries a real attribution id
    # rather than an invented judge identity. Appended last so any positional
    # construction of the older shape fails loudly instead of shifting fields.
    agent_id: str


@dataclasses.dataclass
class JudgeVerdict:
    passed: bool
    score: float | None
    raw: dict[str, Any]


@dataclasses.dataclass
class StepRecordRequest:
    ctx: RunContext
    node_name: str
    order: int
    agent_id: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    judge_verdict: dict[str, Any] | None
    error: str | None
    retry_count: int


# ---------------------------------------------------------------------------
# Activity class — dependencies injected at construction
# ---------------------------------------------------------------------------

class WorkflowActivities:
    """Temporal activity implementations for the Skylize workflow engine.

    ``judge`` and ``minter`` are optional — the judge activity degrades to an
    unverified (passed=False, unverified=True) verdict when no judge is wired,
    which the fail_closed gate in the engine treats as a block.

    ``judge`` is the LLM verifier port (``judge.NodeJudge``); the production
    impl is ``judge.LLMJudge`` constructed over the composition root's single
    shared content-gated gateway (``Container.llm``) — never a bare provider
    adapter, and never the planner's own model selection.
    """

    def __init__(
        self,
        *,
        repo: WorkflowRepository,
        builder: Any,
        judge: NodeJudge | None,
        minter: Any | None,
    ) -> None:
        self._repo = repo
        self._builder = builder
        self._judge = judge
        self._minter = minter

    @activity.defn
    async def run_judge_verification(self, req: JudgeRequest) -> JudgeVerdict:
        """Call the LLM judge and return a structured verdict.

        Without a judge wired, returns an unverified verdict (passed=False)
        rather than allowing silent pass-through.
        """
        if self._judge is None:
            raw: dict[str, Any] = {
                "passed": False,
                "score": None,
                "scored": False,
                "reason": "no judge configured",
                "unverified": True,
            }
            return JudgeVerdict(passed=False, score=None, raw=raw)

        context: dict[str, Any] = {
            "workflow_id": req.ctx.workflow_id,
            "node": req.node_name,
            "org_id": req.ctx.org_id,
            "governance_token_id": req.ctx.governance_token_id,
            "correlation_id": req.ctx.correlation_id,
            "agent_id": req.agent_id,
        }
        raw = await self._judge.judge(
            output=req.output,
            success_criteria=req.success_criteria,
            context=context,
        )
        return JudgeVerdict(
            passed=bool(raw.get("passed", False)),
            score=raw.get("score"),
            raw=raw,
        )

    @activity.defn
    async def write_run_step(self, req: StepRecordRequest) -> None:
        """Persist one node's execution record to the workflow_run_steps table."""
        now = datetime.now(timezone.utc)
        row = WorkflowRunStepRow(
            step_id=uuid4(),
            run_id=UUID(req.ctx.run_id),
            org_id=req.ctx.org_id,
            step_name=req.node_name,
            step_order=req.order,
            agent_id=req.agent_id,
            status=req.status,
            input=req.input,
            output=req.output,
            judge_verdict=req.judge_verdict,
            error_message=req.error,
            retry_count=req.retry_count,
            created_at=now,
            completed_at=now if req.status in ("completed", "failed") else None,
        )
        await self._repo.record_step(row)
