"""Autonomous run mechanism — principal resolution, attention, fail-safe, pilot gate.

No database and no Temporal server: these cover the DECISIONS the mechanism
makes. The live-Postgres proof that a real triggered run produces a real journal
row a real brief returns lives in
tests/integration/test_autonomous_run_pg.py.

What is deliberately asserted here:
  * the principal is the human at the END of `escalation_path`, resolved through
    migration 0020's derivation, and NEVER substituted;
  * `requires_attention` is contract-driven -- dropping a trigger from a contract
    disables its rule with no edit to the rule table;
  * every terminal state writes exactly one journal row, including failures;
  * the pilot gate refuses agents 2..23.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from skylize.app.agents.execution import AgentDeferredToHuman, AgentGovernanceRejected
from skylize.app.autonomy.attention import evaluate_attention
from skylize.app.autonomy.errors import ContractNotAutonomous, PrincipalUnresolvable
from skylize.app.autonomy.pilot import (
    PILOT_AGENT_ID,
    PILOT_AGENT_IDS,
    assert_pilot_agent,
    pilot_input,
)
from skylize.app.autonomy.principal import (
    AutonomousPrincipalResolver,
    terminal_human_role,
)
from skylize.app.autonomy.runner import (
    KIND_COMPLETED,
    KIND_DEFERRED,
    KIND_FAILED,
    KIND_REJECTED,
    AutonomousRunRequest,
    AutonomousRunService,
)
from skylize.app.autonomy.triggers import run_on_event, run_scheduled
from skylize.app.principal.models import ActorKind
from skylize.contracts.mvp import ALL_MVP_CONTRACTS
from skylize.contracts.registry import AgentRegistry
from skylize.dal.ports import UserRow

REGISTRY = AgentRegistry(ALL_MVP_CONTRACTS)
PILOT = REGISTRY.resolve(PILOT_AGENT_ID)
ORG = "org_test"
OWNER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def _user(*, roles: list[str], active: bool = True, uid: uuid.UUID = OWNER_ID) -> UserRow:
    return UserRow(
        user_id=uid,
        org_id=ORG,
        email=f"{uid}@example.test",
        password_hash="x",
        roles=roles,
        is_active=active,
        created_at=datetime.now(timezone.utc),
    )


class FakeUsers:
    def __init__(self, rows: list[UserRow]) -> None:
        self._rows = rows

    async def list_by_org(self, org_id: str) -> list[UserRow]:
        return [r for r in self._rows if r.org_id == org_id]


class FakeJournal:
    """Stands in for WorkJournal.record, capturing every row appended."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self._fail = fail

    async def record(self, **kwargs: Any) -> int:
        if self._fail:
            raise RuntimeError("journal is down")
        self.rows.append(kwargs)
        return len(self.rows)


class FakeVerdict:
    def __init__(self, outcome: str, confidence: float) -> None:
        self.outcome = outcome
        self.confidence = confidence


class FakeRow:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class FakeExecution:
    """Drives execute() to each terminal state and records what it was passed."""

    def __init__(self, *, raises: Exception | None = None, output: Any = None) -> None:
        self._raises = raises
        self._output = output
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> FakeRow:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        sink = kwargs.get("output_sink")
        if sink is not None and self._output is not None:
            sink(self._output)
        return FakeRow()


class FakeCosts:
    def __init__(self, minor: int = 0, *, fail: bool = False) -> None:
        self._minor = minor
        self._fail = fail

    async def run_total_minor(self, org_id: str, correlation_id: uuid.UUID) -> int:
        if self._fail:
            raise RuntimeError("ledger unreachable")
        return self._minor


def _service(
    *,
    users: FakeUsers | None = None,
    execution: FakeExecution | None = None,
    journal: FakeJournal | None = None,
    costs: FakeCosts | None = None,
) -> tuple[AutonomousRunService, FakeJournal, FakeExecution]:
    j = journal or FakeJournal()
    e = execution or FakeExecution(output=FakeVerdict("allow", 0.95))
    svc = AutonomousRunService(
        registry=REGISTRY,
        execution=e,  # type: ignore[arg-type]
        resolver=AutonomousPrincipalResolver(
            users or FakeUsers([_user(roles=["owner"])])  # type: ignore[arg-type]
        ),
        journal=j,  # type: ignore[arg-type]
        cost_source=costs,
    )
    return svc, j, e


def _request(**kw: Any) -> AutonomousRunRequest:
    return AutonomousRunRequest(
        org_id=ORG,
        agent_id=PILOT_AGENT_ID,
        input_data=pilot_input(),
        trigger="schedule:test",
        **kw,
    )


# --------------------------------------------------------------------------- #
# Principal resolution
# --------------------------------------------------------------------------- #

def test_every_live_contract_chain_ends_at_human_owner() -> None:
    """The invariant the whole attribution model rests on, asserted over all 23."""
    for contract in ALL_MVP_CONTRACTS:
        assert terminal_human_role(contract) == "human_owner", contract.agent_id


@pytest.mark.asyncio
async def test_resolves_to_the_org_owner_account() -> None:
    resolver = AutonomousPrincipalResolver(
        FakeUsers([_user(roles=["viewer"], uid=uuid.uuid4()), _user(roles=["owner"])])  # type: ignore[arg-type]
    )
    assert await resolver.resolve(org_id=ORG, contract=PILOT) == str(OWNER_ID)


@pytest.mark.asyncio
async def test_refuses_when_org_has_no_owner() -> None:
    """The identity gap must STOP the run, never fall back to another user."""
    resolver = AutonomousPrincipalResolver(
        FakeUsers([_user(roles=["admin"], uid=uuid.uuid4())])  # type: ignore[arg-type]
    )
    with pytest.raises(PrincipalUnresolvable):
        await resolver.resolve(org_id=ORG, contract=PILOT)


@pytest.mark.asyncio
async def test_refuses_when_the_owner_is_deactivated() -> None:
    resolver = AutonomousPrincipalResolver(
        FakeUsers([_user(roles=["owner"], active=False)])  # type: ignore[arg-type]
    )
    with pytest.raises(PrincipalUnresolvable):
        await resolver.resolve(org_id=ORG, contract=PILOT)


@pytest.mark.asyncio
async def test_refuses_a_sandbox_contract() -> None:
    """cowork_agent is lifecycle_status='sandbox' -- never part of the fleet."""
    sandbox = REGISTRY.resolve("cowork_agent")
    assert sandbox.lifecycle_status == "sandbox"
    resolver = AutonomousPrincipalResolver(FakeUsers([_user(roles=["owner"])]))  # type: ignore[arg-type]
    with pytest.raises(ContractNotAutonomous):
        await resolver.resolve(org_id=ORG, contract=sandbox)


def test_refuses_a_chain_that_does_not_end_at_a_human() -> None:
    broken = PILOT.model_copy(update={"escalation_path": ["chief_security_officer"]})
    with pytest.raises(ContractNotAutonomous):
        terminal_human_role(broken)


# --------------------------------------------------------------------------- #
# requires_attention is contract-driven, not hardcoded
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("outcome", "confidence", "expected"),
    [
        ("allow", 0.95, False),   # clean pass -> done_while_away only
        ("review", 0.95, True),   # SECURITY_SEVERITY_HIGH
        ("reject", 0.99, True),   # SECURITY_SEVERITY_HIGH
        ("allow", 0.40, True),    # LOW_CONFIDENCE_IRREVERSIBLE
    ],
)
def test_attention_reads_the_runs_real_output(
    outcome: str, confidence: float, expected: bool
) -> None:
    fired, reasons = evaluate_attention(PILOT, FakeVerdict(outcome, confidence))
    assert fired is expected
    assert bool(reasons) is expected


def test_dropping_a_trigger_from_the_contract_disables_its_rule() -> None:
    """THE anti-hardcoding property: the contract decides which boundaries exist.

    Same output, same rule table; only `human_in_loop_triggers` changes.
    """
    flagged = FakeVerdict("review", 0.99)
    assert evaluate_attention(PILOT, flagged)[0] is True

    without = PILOT.model_copy(update={"human_in_loop_triggers": []})
    assert evaluate_attention(without, flagged)[0] is False


def test_attention_is_inert_for_an_uncalibrated_agent() -> None:
    """An agent with no rules contributes no signal rather than a false clean."""
    assert evaluate_attention(REGISTRY.resolve("ad_copy_agent"), object()) == (False, [])


# --------------------------------------------------------------------------- #
# The four terminal states each write exactly one journal row
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_completed_clean_run_writes_an_unflagged_row() -> None:
    svc, journal, execution = _service()
    outcome = await svc.run(_request())

    assert outcome.status == "completed"
    assert outcome.requires_attention is False
    assert len(journal.rows) == 1
    row = journal.rows[0]
    assert row["kind"] == KIND_COMPLETED
    assert row["actor_kind"] is ActorKind.AGENT_AUTONOMOUS
    assert row["actor_id"] == PILOT_AGENT_ID
    assert row["principal_id"] == str(OWNER_ID)
    assert row["requires_attention"] is False

    # The principal was attached BEFORE dispatch, and the run declared itself
    # autonomous to the mint.
    call = execution.calls[0]
    assert call["on_behalf_of_principal"] == str(OWNER_ID)
    assert call["session_kind"] == "autonomous"
    assert call["correlation_id"] == outcome.correlation_id


@pytest.mark.asyncio
async def test_completed_run_at_a_boundary_is_flagged() -> None:
    svc, journal, _ = _service(
        execution=FakeExecution(output=FakeVerdict("review", 0.99))
    )
    outcome = await svc.run(_request())

    assert outcome.status == "completed"
    assert outcome.requires_attention is True
    assert journal.rows[0]["requires_attention"] is True
    assert any("security_severity_high" in r for r in outcome.reasons)


@pytest.mark.asyncio
async def test_deferred_run_writes_a_flagged_row_carrying_the_hitl_id() -> None:
    hitl_id = uuid.uuid4()
    svc, journal, _ = _service(
        execution=FakeExecution(
            raises=AgentDeferredToHuman(hitl_id=hitl_id, reason="trigger present")
        )
    )
    outcome = await svc.run(_request())

    assert outcome.status == "deferred"
    assert outcome.hitl_id == hitl_id
    assert journal.rows[0]["kind"] == KIND_DEFERRED
    assert journal.rows[0]["requires_attention"] is True
    assert journal.rows[0]["detail"]["hitl_id"] == str(hitl_id)


@pytest.mark.asyncio
async def test_rejected_run_writes_a_flagged_row() -> None:
    svc, journal, _ = _service(
        execution=FakeExecution(raises=AgentGovernanceRejected("policy says no"))
    )
    outcome = await svc.run(_request())
    assert outcome.status == "rejected"
    assert journal.rows[0]["kind"] == KIND_REJECTED
    assert journal.rows[0]["requires_attention"] is True


@pytest.mark.asyncio
async def test_a_failed_run_does_not_vanish() -> None:
    """THE fail-safe requirement: an unexpected error still reaches the human."""
    svc, journal, _ = _service(
        execution=FakeExecution(raises=RuntimeError("provider exploded"))
    )
    outcome = await svc.run(_request())

    assert outcome.status == "failed"
    assert outcome.requires_attention is True
    assert len(journal.rows) == 1
    assert journal.rows[0]["kind"] == KIND_FAILED
    assert "provider exploded" in journal.rows[0]["detail"]["reasons"][0]


@pytest.mark.asyncio
async def test_an_unresolvable_principal_stops_the_run_before_dispatch() -> None:
    """No principal means no row is POSSIBLE, so it must raise -- and must not
    have spent anything first."""
    svc, journal, execution = _service(users=FakeUsers([_user(roles=["admin"])]))
    with pytest.raises(PrincipalUnresolvable):
        await svc.run(_request())
    assert execution.calls == []
    assert journal.rows == []


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_real_cost_is_threaded_onto_the_row() -> None:
    svc, journal, _ = _service(costs=FakeCosts(137))
    outcome = await svc.run(_request())
    assert outcome.cost_minor == 137
    assert journal.rows[0]["cost_minor"] == 137


@pytest.mark.asyncio
async def test_a_failed_cost_read_does_not_lose_the_row() -> None:
    """An under-reported cost is recoverable from the ledger; a dropped row is not."""
    svc, journal, _ = _service(costs=FakeCosts(fail=True))
    outcome = await svc.run(_request())
    assert outcome.status == "completed"
    assert outcome.cost_minor == 0
    assert len(journal.rows) == 1


@pytest.mark.asyncio
async def test_negative_net_cost_is_clamped_not_raised() -> None:
    """cost_minor is CHECK (>= 0) in migration 0019; a reversal-heavy run clamps."""
    svc, journal, _ = _service(costs=FakeCosts(-5))
    outcome = await svc.run(_request())
    assert outcome.cost_minor == 0
    assert journal.rows[0]["cost_minor"] == 0


@pytest.mark.asyncio
async def test_a_journal_write_failure_is_reported_not_raised() -> None:
    """The run already happened; re-raising would make Temporal retry paid work."""
    svc, _, _ = _service(journal=FakeJournal(fail=True))
    outcome = await svc.run(_request())
    assert outcome.status == "completed"
    assert outcome.journal_seq is None


# --------------------------------------------------------------------------- #
# The pilot exit gate
# --------------------------------------------------------------------------- #

def test_exactly_one_agent_is_in_the_pilot() -> None:
    assert PILOT_AGENT_IDS == frozenset({PILOT_AGENT_ID})
    assert len(ALL_MVP_CONTRACTS) == 23


def test_every_other_agent_is_refused() -> None:
    others = [c.agent_id for c in ALL_MVP_CONTRACTS if c.agent_id != PILOT_AGENT_ID]
    assert len(others) == 22
    for agent_id in others:
        with pytest.raises(ContractNotAutonomous):
            assert_pilot_agent(agent_id)


@pytest.mark.asyncio
async def test_both_trigger_shapes_enforce_the_gate_and_share_the_runner() -> None:
    svc, journal, _ = _service()

    scheduled = await run_scheduled(
        svc, org_id=ORG, agent_id=PILOT_AGENT_ID, schedule_id="hourly"
    )
    evented = await run_on_event(
        svc,
        org_id=ORG,
        agent_id=PILOT_AGENT_ID,
        event_name="signal.observed",
        input_data=pilot_input(),
    )
    assert scheduled.status == evented.status == "completed"
    assert journal.rows[0]["detail"]["trigger"] == "schedule:hourly"
    assert journal.rows[1]["detail"]["trigger"] == "event:signal.observed"

    with pytest.raises(ContractNotAutonomous):
        await run_scheduled(svc, org_id=ORG, agent_id="ad_copy_agent", schedule_id="x")
