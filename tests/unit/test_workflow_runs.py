"""Workflow run history — the parts provable without a database.

The RLS and migration-shape guarantees need real Postgres and live in
``tests/integration/test_workflow_runs_pg.py``, which SKIPS without
``SKYLIZE_TEST_DB_URL`` / ``SKYLIZE_TEST_APP_DB_URL``. These tests run in the
unit gate unconditionally, so the two load-bearing behaviors below are proven
on every CI run rather than only on a Postgres-equipped one:

  * ``Orchestrator.invoke`` writes a run row best-effort — a `WorkflowRunWriter`
    that raises on start, on finish, or on both must never change the run's
    outcome or propagate;
  * a run with no writer configured (the memory backend) behaves exactly as
    before this change;
  * the catalogue route derives its one entry from the live registry rather
    than from a hand-written list, and returns an empty list — not a
    fabricated entry — when the agent cannot be resolved;
  * the run-history route 503s on the memory backend rather than reporting an
    empty page, and rejects a naive (non-timezone-aware) ``before``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from skylize.app.governance.authority import GovernanceDenied
from skylize.app.orchestrator.orchestrator import (
    CREATIVE_WORKFLOW_NAME,
    Orchestrator,
)
from skylize.contracts.base import AgentContract, ToolGrant
from skylize.contracts.registry import AgentNotRegistered, AgentRegistry
from skylize.dal.workflow_runs import (
    TERMINAL_RUN_STATUSES,
    VALID_RUN_STATUSES,
    WorkflowRunRow,
    WorkflowRunsDAL,
)
from skylize.edge.routes.workflows import (
    _CREATIVE_AGENT_ID,
    list_workflow_runs,
    list_workflows,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingRunWriter:
    """A WorkflowRunWriter that records every call and never raises."""

    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []

    async def start_run(self, **kwargs: object) -> None:
        self.started.append(kwargs)

    async def finish_run(self, **kwargs: object) -> None:
        self.finished.append(kwargs)


class _RaisingRunWriter:
    """A WorkflowRunWriter that raises on every call — proves best-effort."""

    async def start_run(self, **kwargs: object) -> None:
        raise RuntimeError("db is down")

    async def finish_run(self, **kwargs: object) -> None:
        raise RuntimeError("db is down")


class _FakeAuthority:
    """Denies every agent at the governance gate (stage 2), never reaching
    mint/graph. That is enough surface to prove run-row bookkeeping without
    constructing a real signed GovernanceToken or running the LangGraph."""

    public_key = None

    async def assert_active(self, agent_id: str, org_id: str) -> None:
        raise GovernanceDenied(f"{agent_id} is not active")

    def live_state_checker(self, org_id: str):
        def _checker(*_args: object, **_kwargs: object) -> object:
            return None

        return _checker

    async def mint(self, contract: AgentContract, *, org_id: str, correlation_id):
        raise AssertionError("mint must not be reached when the gate denies")

    async def record_violation(self, **kwargs: object) -> None:
        return None


class _FakeAudit:
    async def record(self, **kwargs: object) -> None:
        return None


class _FakeBus:
    async def publish(self, event: object) -> None:
        return None


class _FakeRunner:
    """Never actually invoked in these tests — the resolve/gate/validate-input
    paths all return before the graph runs."""


def _contract(agent_id: str = "hook_generator_agent") -> AgentContract:
    return AgentContract(
        agent_id=agent_id,
        agent_role="Hook Generator",
        authority_level="worker",
        department="creative",
        input_schema="skylize.schemas.agents.creative.HookRequestIn",
        output_schema="skylize.schemas.agents.creative.HookResponseOut",
        allowed_tools=[ToolGrant(tool_id="llm.generate", purpose="generate hooks")],
        max_token_budget=4096,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode="fail_closed",
        memory_read_access=[],
        memory_write_access=[],
    )


class _Container:
    def __init__(self, orchestrator: object, dal: object | None) -> None:
        self.orchestrator = orchestrator
        self.workflow_runs_dal = dal


class _Ctx:
    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.correlation_id = uuid.uuid4()


class _FakeDb:
    """Minimal tenant_session fake for WorkflowRunsDAL unit tests."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.bound_orgs: list[str] = []

    def tenant_session(self, org_id: str):
        db = self

        class _Session:
            async def __aenter__(self_inner):
                db.bound_orgs.append(org_id)
                return db

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

        return _Session()

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((query, args))

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        return []


# ---------------------------------------------------------------------------
# Orchestrator wiring — best-effort recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_agent_denied_at_gate_still_starts_and_finishes_the_row() -> None:
    writer = _RecordingRunWriter()
    orch = Orchestrator(
        registry=AgentRegistry([_contract()]),
        authority=_FakeAuthority(),
        audit=_FakeAudit(),
        bus=_FakeBus(),
        runner=_FakeRunner(),
        run_writer=writer,
    )
    result = await orch.invoke(
        "hook_generator_agent", {}, org_id="org-1"
    )
    assert result.status == "denied"
    assert writer.started, "start_run must be called before the graph runs"
    assert writer.started[0]["org_id"] == "org-1"
    assert writer.started[0]["workflow_name"] == CREATIVE_WORKFLOW_NAME
    assert writer.started[0]["agent_id"] == "hook_generator_agent"
    assert writer.finished, "finish_run must be called on every terminal path"
    assert writer.finished[0]["run_id"] == result.correlation_id
    assert writer.finished[0]["status"] == "denied"
    assert writer.finished[0]["failure_stage"] == "gate"


@pytest.mark.asyncio
async def test_denied_run_on_unresolved_agent_still_writes_start_and_finish() -> None:
    writer = _RecordingRunWriter()
    orch = Orchestrator(
        registry=AgentRegistry([]),
        authority=_FakeAuthority(),
        audit=_FakeAudit(),
        bus=_FakeBus(),
        runner=_FakeRunner(),
        run_writer=writer,
    )
    result = await orch.invoke("does_not_exist", {}, org_id="org-1")
    assert result.status == "denied"
    assert writer.started, "a denied run must still be visible as a run"
    assert writer.finished[0]["status"] == "denied"
    assert writer.finished[0]["failure_stage"] == "resolve"


@pytest.mark.asyncio
async def test_run_writer_failure_never_breaks_execution() -> None:
    """The load-bearing resilience guarantee: a raising writer must not change
    the outcome or propagate out of invoke."""
    orch = Orchestrator(
        registry=AgentRegistry([]),
        authority=_FakeAuthority(),
        audit=_FakeAudit(),
        bus=_FakeBus(),
        runner=_FakeRunner(),
        run_writer=_RaisingRunWriter(),
    )
    result = await orch.invoke("does_not_exist", {}, org_id="org-1")
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_none_writer_behaves_exactly_as_before() -> None:
    """The memory backend: no writer configured, no run row, no error."""
    orch = Orchestrator(
        registry=AgentRegistry([]),
        authority=_FakeAuthority(),
        audit=_FakeAudit(),
        bus=_FakeBus(),
        runner=_FakeRunner(),
        run_writer=None,
    )
    result = await orch.invoke("does_not_exist", {}, org_id="org-1")
    assert result.status == "denied"


# ---------------------------------------------------------------------------
# Catalogue route — derived, not hand-written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalogue_has_exactly_one_derived_entry() -> None:
    registry = AgentRegistry([_contract()])
    orch = Orchestrator(
        registry=registry, authority=_FakeAuthority(), audit=_FakeAudit(),
        bus=_FakeBus(), runner=_FakeRunner(), run_writer=None,
    )
    resp = await list_workflows(ctx=_Ctx(), container=_Container(orch, None))
    assert len(resp.workflows) == 1
    entry = resp.workflows[0]
    assert entry.agent_id == _CREATIVE_AGENT_ID
    assert entry.name == CREATIVE_WORKFLOW_NAME
    assert entry.trigger_path == "/api/v1/workflows/creative"
    # Derived from the registered contract, not authored in the route.
    contract = registry.resolve(_CREATIVE_AGENT_ID)
    assert entry.agent_role == contract.agent_role
    assert entry.department == contract.department
    assert resp.stage_progress_supported is False


@pytest.mark.asyncio
async def test_catalogue_is_empty_when_the_agent_is_not_registered() -> None:
    """Fail closed, not fabricated: no agent to run means no entry to show."""
    registry = AgentRegistry([])
    with pytest.raises(AgentNotRegistered):
        registry.resolve(_CREATIVE_AGENT_ID)
    orch = Orchestrator(
        registry=registry, authority=_FakeAuthority(), audit=_FakeAudit(),
        bus=_FakeBus(), runner=_FakeRunner(), run_writer=None,
    )
    resp = await list_workflows(ctx=_Ctx(), container=_Container(orch, None))
    assert resp.workflows == []


# ---------------------------------------------------------------------------
# Run-history route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_route_503_without_the_postgres_backend() -> None:
    orch = Orchestrator(
        registry=AgentRegistry([]), authority=_FakeAuthority(), audit=_FakeAudit(),
        bus=_FakeBus(), runner=_FakeRunner(), run_writer=None,
    )
    with pytest.raises(HTTPException) as excinfo:
        await list_workflow_runs(ctx=_Ctx(), container=_Container(orch, None))
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_runs_route_rejects_naive_before() -> None:
    orch = Orchestrator(
        registry=AgentRegistry([]), authority=_FakeAuthority(), audit=_FakeAudit(),
        bus=_FakeBus(), runner=_FakeRunner(), run_writer=None,
    )
    dal = WorkflowRunsDAL(_FakeDb())
    with pytest.raises(HTTPException) as excinfo:
        await list_workflow_runs(
            before=datetime(2026, 1, 1),  # no tzinfo
            ctx=_Ctx(), container=_Container(orch, dal),
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_runs_route_maps_dal_rows() -> None:
    orch = Orchestrator(
        registry=AgentRegistry([]), authority=_FakeAuthority(), audit=_FakeAudit(),
        bus=_FakeBus(), runner=_FakeRunner(), run_writer=None,
    )

    class _StubDal:
        async def list_runs(self, org_id: str, *, limit: int, before: object) -> list[WorkflowRunRow]:
            return [
                WorkflowRunRow(
                    run_id=uuid.uuid4(), org_id=org_id, workflow_name="creative",
                    agent_id="hook_generator_agent", status="completed",
                    correlation_id=uuid.uuid4(),
                    started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
                    failure_stage=None, reason=None,
                )
            ]

    resp = await list_workflow_runs(ctx=_Ctx(), container=_Container(orch, _StubDal()))
    assert len(resp.runs) == 1
    assert resp.runs[0].status == "completed"
    assert resp.next_before is None  # fewer rows than limit


# ---------------------------------------------------------------------------
# WorkflowRunsDAL — validation and query shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_run_rejects_non_terminal_status() -> None:
    dal = WorkflowRunsDAL(_FakeDb())
    with pytest.raises(ValueError, match="status must be one of"):
        await dal.finish_run(run_id=uuid.uuid4(), org_id="org-1", status="running")


@pytest.mark.asyncio
async def test_finish_run_only_transitions_from_running() -> None:
    db = _FakeDb()
    dal = WorkflowRunsDAL(db)
    await dal.finish_run(run_id=uuid.uuid4(), org_id="org-1", status="completed")
    query, _args = db.executed[0]
    assert "WHERE run_id = $1 AND org_id = $2 AND status = 'running'" in query


@pytest.mark.asyncio
async def test_start_run_is_idempotent_on_conflict() -> None:
    db = _FakeDb()
    dal = WorkflowRunsDAL(db)
    await dal.start_run(
        run_id=uuid.uuid4(), org_id="org-1", workflow_name="creative",
        agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
    )
    query, _args = db.executed[0]
    assert "ON CONFLICT (run_id) DO NOTHING" in query


@pytest.mark.asyncio
async def test_list_runs_is_bound_to_the_callers_org() -> None:
    db = _FakeDb()
    dal = WorkflowRunsDAL(db)
    await dal.list_runs("org-7", limit=10)
    assert db.bound_orgs == ["org-7"]


def test_status_vocabulary_matches_orchestrator_result_values() -> None:
    """WorkflowResult.status produces exactly these three terminal values, plus
    this table's own 'running' for the open interval."""
    assert TERMINAL_RUN_STATUSES == {"completed", "denied", "failed"}
    assert VALID_RUN_STATUSES == TERMINAL_RUN_STATUSES | {"running"}
