"""DecisionEvaluator: the six evaluation stages, HITL triggers, conflicts.

All stages are deterministic and free of I/O beyond the injected capital ledger,
so each case is a plain construct-proposal → evaluate → assert."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from skylize.app.decision_engine.evaluator import (
    STAGE_AUTHORITY,
    STAGE_CONFLICT,
    STAGE_POLICY,
    STAGE_SECURITY,
    DecisionEvaluator,
    _ProposalRecord,
)
from skylize.app.decision_engine.events import DecisionProposal, SecurityVerdict
from skylize.contracts.base import HumanInLoopTrigger
from skylize.schemas.agents.safety import SafetyVerdictOut
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryCapitalRepository
from skylize.dal.ports import BudgetCeiling

ORG = "org_test"
CORR = uuid4()
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=10)


def _evaluator(capital: InMemoryCapitalRepository | None = None) -> DecisionEvaluator:
    return DecisionEvaluator(
        registry=MVP_REGISTRY, capital=capital or InMemoryCapitalRepository()
    )


def make_proposal(
    *,
    agent: str,
    action_kind: str = "creative.review",
    external: bool = False,
    spend: int | None = None,
    scope: str = "creative",
    partition: str = "brief:1",
    corr=CORR,
    occurred: datetime = T0,
    metadata: dict | None = None,
    org: str = ORG,
    security: SecurityVerdict | None = None,
) -> DecisionProposal:
    pid = uuid4()
    return DecisionProposal(
        proposal_id=pid,
        correlation_id=corr,
        partition_key=partition,
        org_id=org,
        department="creative",
        proposing_agent_id=agent,
        action_kind=action_kind,
        requires_external_launch=external,
        spend_minor_units=spend,
        currency="USD" if spend is not None else None,
        capital_scope=scope,
        occurred_at=occurred,
        source_event_id=pid,
        source_type="test",
        security_verdict=security,
        metadata=metadata or {},
    )


def _reject(reason: str = "policy breach") -> SecurityVerdict:
    return SecurityVerdict(
        source_agent_id="content_safety_agent", reject=True, severity="high", reason=reason
    )


# -- stage 1: authority -----------------------------------------------------
async def test_unknown_agent_fails_closed_rejected() -> None:
    result = await _evaluator().evaluate(make_proposal(agent="ghost_agent"))
    assert result.outcome == "rejected"
    assert result.stage_failed_at == STAGE_AUTHORITY


async def test_worker_external_launch_exceeds_authority_defers() -> None:
    # A worker cannot launch an external campaign → deferred up the chain.
    result = await _evaluator().evaluate(
        make_proposal(agent="hook_generator_agent", external=True)
    )
    assert result.outcome == "deferred_to_human"
    assert result.stage_failed_at == STAGE_AUTHORITY
    assert result.hitl_trigger == HumanInLoopTrigger.AUTHORITY_EXCEEDED.value
    assert result.routed_to  # routed along escalation_path


# -- stage 2: policy --------------------------------------------------------
async def test_worker_spend_rejected_by_policy() -> None:
    result = await _evaluator().evaluate(
        make_proposal(
            agent="hook_generator_agent",
            action_kind="sales.budget_reallocation",
            spend=1_000,
            scope="growth",
        )
    )
    assert result.outcome == "rejected"
    assert result.stage_failed_at == STAGE_POLICY
    assert any("spend_requires_director" in r for r in result.reasons)


async def test_unknown_action_class_rejected_never_guessed() -> None:
    result = await _evaluator().evaluate(
        make_proposal(agent="director_growth", action_kind="sales.mystery_action")
    )
    assert result.outcome == "rejected"
    assert result.stage_failed_at == STAGE_POLICY
    assert any("unknown_action_class" in r for r in result.reasons)


async def test_brand_safety_blocked_rejected() -> None:
    result = await _evaluator().evaluate(
        make_proposal(agent="copy_director", metadata={"brand_safety": "blocked"})
    )
    assert result.outcome == "rejected"
    assert result.stage_failed_at == STAGE_POLICY


# -- stage 3: scoring -------------------------------------------------------
async def test_score_is_deterministic_and_bounded() -> None:
    ev = _evaluator()
    p = make_proposal(agent="copy_director", partition="brief:score")
    r1 = await ev.evaluate(p)
    # A fresh evaluator (no remembered incumbent) yields the identical score.
    r2 = await _evaluator().evaluate(p)
    assert r1.score is not None and r2.score is not None
    assert 0 <= r1.score.value <= 100
    assert r1.score.value == r2.score.value
    assert set(r1.score.components) == {"authority_weight", "policy_pass", "budget_headroom"}


# -- stage 4: capital -------------------------------------------------------
async def test_spend_over_ceiling_defers() -> None:
    cap = InMemoryCapitalRepository()
    cap.set_ceiling(BudgetCeiling(ORG, "growth", ceiling_minor_units=5_000, committed_minor_units=0))
    result = await _evaluator(cap).evaluate(
        make_proposal(
            agent="director_growth",
            action_kind="sales.budget_reallocation",
            spend=10_000,
            scope="growth",
        )
    )
    assert result.outcome == "deferred_to_human"
    assert result.hitl_trigger == HumanInLoopTrigger.SPEND_OVER_CEILING.value


async def test_spend_without_configured_ceiling_fails_closed() -> None:
    result = await _evaluator().evaluate(
        make_proposal(
            agent="director_growth",
            action_kind="sales.budget_reallocation",
            spend=100,
            scope="growth",
        )
    )
    assert result.outcome == "deferred_to_human"
    assert any("no_budget_ceiling_configured" in r for r in result.reasons)


async def test_spend_within_ceiling_approved() -> None:
    cap = InMemoryCapitalRepository()
    cap.set_ceiling(BudgetCeiling(ORG, "growth", ceiling_minor_units=5_000, committed_minor_units=0))
    result = await _evaluator(cap).evaluate(
        make_proposal(
            agent="director_growth",
            action_kind="sales.budget_reallocation",
            spend=1_000,
            scope="growth",
            partition="realloc:1",
        )
    )
    assert result.outcome == "approved"
    assert result.score is not None


# -- stage 5: conflict resolution ------------------------------------------
async def test_conflict_resolved_by_authority_challenger_wins() -> None:
    ev = _evaluator()
    incumbent = make_proposal(agent="hook_generator_agent", partition="brief:c1", occurred=T0)
    challenger = make_proposal(agent="copy_director", partition="brief:c1", occurred=T1)
    await ev.evaluate(incumbent)  # worker, internal → approved + remembered
    result = await ev.evaluate(challenger)
    assert result.outcome == "approved"
    assert result.conflicts and result.conflicts[0].rule_applied == "authority"
    assert result.conflicts[0].winning_proposal_id == challenger.proposal_id


async def test_conflict_lost_to_higher_authority_rejected() -> None:
    ev = _evaluator()
    incumbent = make_proposal(agent="copy_director", partition="brief:c2", occurred=T0)
    challenger = make_proposal(agent="hook_generator_agent", partition="brief:c2", occurred=T1)
    await ev.evaluate(incumbent)  # director → approved + remembered
    result = await ev.evaluate(challenger)
    assert result.outcome == "rejected"
    assert result.stage_failed_at == STAGE_CONFLICT
    assert result.conflicts[0].rule_applied == "authority"
    assert result.conflicts[0].winning_proposal_id == incumbent.proposal_id


async def test_conflict_resolved_by_recency_when_authority_equal() -> None:
    ev = _evaluator()
    incumbent = make_proposal(agent="copy_director", partition="brief:c3", occurred=T0)
    challenger = make_proposal(agent="art_director", partition="brief:c3", occurred=T1)
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "approved"
    assert result.conflicts[0].rule_applied == "recency"
    assert result.conflicts[0].winning_proposal_id == challenger.proposal_id


async def test_conflict_unresolvable_escalates_to_human() -> None:
    ev = _evaluator()
    incumbent = make_proposal(agent="copy_director", partition="brief:c4", occurred=T0)
    challenger = make_proposal(agent="art_director", partition="brief:c4", occurred=T0)
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "deferred_to_human"
    assert result.conflicts[0].rule_applied == "escalated"
    assert result.conflicts[0].winning_proposal_id is None


# -- stage 0: absolute safety veto (dedicated stage) -----------------------
async def test_security_veto_no_rival_still_blocks() -> None:
    # The core of the dedicated stage: a reject=True proposal with NO rival on
    # the partition is still blocked. The conflict-scoped rule would never fire
    # here (no incumbent) — the absolute stage does.
    ev = _evaluator()
    result = await ev.evaluate(
        make_proposal(agent="copy_director", partition="brief:v0", security=_reject())
    )
    assert result.outcome == "deferred_to_human"
    assert result.stage_failed_at == STAGE_SECURITY
    assert result.stages_completed == [STAGE_SECURITY]
    assert not result.conflicts  # never reached conflict resolution
    assert any("safety_veto" in r for r in result.reasons)


async def test_security_veto_defers_to_human_with_routing() -> None:
    result = await _evaluator().evaluate(
        make_proposal(agent="copy_director", partition="brief:vr", security=_reject("bad"))
    )
    assert result.outcome == "deferred_to_human"
    assert result.hitl_trigger == HumanInLoopTrigger.SECURITY_SEVERITY_HIGH.value
    assert result.routed_to  # escalated up the chain to a human


async def test_security_veto_beats_authority_before_conflict() -> None:
    # Challenger has HIGHER authority (director vs worker) and would win the
    # conflict on authority — but the absolute veto stage fires first, ahead of
    # conflict resolution, so it is deferred regardless.
    ev = _evaluator()
    incumbent = make_proposal(agent="hook_generator_agent", partition="brief:v1", occurred=T0)
    challenger = make_proposal(
        agent="copy_director", partition="brief:v1", occurred=T1, security=_reject()
    )
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "deferred_to_human"
    assert result.stage_failed_at == STAGE_SECURITY
    assert not result.conflicts  # short-circuited before conflict resolution


async def test_security_veto_beats_recency_before_conflict() -> None:
    # Equal authority, challenger is newer (would win on recency) — vetoed first.
    ev = _evaluator()
    incumbent = make_proposal(agent="copy_director", partition="brief:v2", occurred=T0)
    challenger = make_proposal(
        agent="art_director", partition="brief:v2", occurred=T1, security=_reject()
    )
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "deferred_to_human"
    assert result.stage_failed_at == STAGE_SECURITY


def test_resolve_secondary_guard_vetoes_in_conflict() -> None:
    # Defense-in-depth: even called directly (bypassing the stage ordering), the
    # conflict resolver refuses to let a safety-rejected proposal win.
    ev = _evaluator()
    contract = MVP_REGISTRY.resolve("copy_director")
    challenger = make_proposal(agent="copy_director", occurred=T1, security=_reject())
    incumbent = _ProposalRecord(
        proposal_id=uuid4(),
        agent_id="hook_generator_agent",
        authority_level="worker",
        occurred_at=T0,
    )
    winner, rule = ev._resolve(challenger, contract, incumbent)
    assert rule == "safety_veto"
    assert winner == incumbent.proposal_id


async def test_non_rejecting_verdict_does_not_veto() -> None:
    # A verdict that is present but does NOT reject is not a veto: normal
    # authority resolution applies and the higher-authority challenger wins.
    ev = _evaluator()
    clean = SecurityVerdict(
        source_agent_id="content_safety_agent", reject=False, severity="low", reason=""
    )
    incumbent = make_proposal(agent="hook_generator_agent", partition="brief:v3", occurred=T0)
    challenger = make_proposal(
        agent="copy_director", partition="brief:v3", occurred=T1, security=clean
    )
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "approved"
    assert result.conflicts[0].rule_applied == "authority"
    assert result.conflicts[0].winning_proposal_id == challenger.proposal_id


async def test_absent_verdict_does_not_auto_reject() -> None:
    # No verdict must NOT be read as a reject: the higher-authority challenger
    # still wins on authority exactly as it would without the carrier.
    ev = _evaluator()
    incumbent = make_proposal(agent="hook_generator_agent", partition="brief:v4", occurred=T0)
    challenger = make_proposal(agent="copy_director", partition="brief:v4", occurred=T1)
    assert challenger.security_verdict is None
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "approved"
    assert result.conflicts[0].rule_applied == "authority"


async def test_absent_verdict_does_not_auto_allow() -> None:
    # No verdict must NOT rescue a lower-authority challenger: it still loses on
    # authority. Absence is "no safety signal", not an implicit clearance.
    ev = _evaluator()
    incumbent = make_proposal(agent="copy_director", partition="brief:v5", occurred=T0)
    challenger = make_proposal(agent="hook_generator_agent", partition="brief:v5", occurred=T1)
    assert challenger.security_verdict is None
    await ev.evaluate(incumbent)
    result = await ev.evaluate(challenger)
    assert result.outcome == "rejected"
    assert result.conflicts[0].rule_applied == "authority"
    assert result.conflicts[0].winning_proposal_id == incumbent.proposal_id


# -- SecurityVerdict.from_safety_verdict mapping ---------------------------
def test_from_safety_verdict_maps_unsafe_to_reject() -> None:
    verdict = SecurityVerdict.from_safety_verdict(
        SafetyVerdictOut(run_id="r1", safe=False, severity="critical", findings=["a", "b"]),
        source_agent_id="content_safety_agent",
    )
    assert verdict.reject is True
    assert verdict.severity == "critical"
    assert verdict.reason == "a; b"
    assert verdict.source_agent_id == "content_safety_agent"


def test_from_safety_verdict_safe_is_not_reject() -> None:
    verdict = SecurityVerdict.from_safety_verdict(
        SafetyVerdictOut(run_id="r2", safe=True, severity="none", findings=[]),
        source_agent_id="content_safety_agent",
    )
    assert verdict.reject is False
    assert verdict.reason == ""


# -- stage 6: HITL gate -----------------------------------------------------
async def test_hitl_first_external_launch_defers() -> None:
    # vp_creative may launch (vp >= director), but FIRST_EXTERNAL_LAUNCH triggers HITL.
    result = await _evaluator().evaluate(
        make_proposal(agent="vp_creative", external=True, partition="brief:h1")
    )
    assert result.outcome == "deferred_to_human"
    assert result.hitl_trigger == HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH.value


async def test_hitl_brand_legal_sensitive_defers() -> None:
    result = await _evaluator().evaluate(
        make_proposal(
            agent="copy_director",
            partition="brief:h2",
            metadata={"brand_sensitive": True},
        )
    )
    assert result.outcome == "deferred_to_human"
    assert result.hitl_trigger == HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE.value


# -- happy path: all six stages pass ---------------------------------------
async def test_all_stages_pass_approved() -> None:
    result = await _evaluator().evaluate(
        make_proposal(agent="copy_director", partition="brief:ok")
    )
    assert result.outcome == "approved"
    assert result.stages_completed == [
        "authority_check",
        "opa_policy",
        "scoring",
        "capital_allocation",
        "conflict_resolution",
        "hitl_gate",
    ]
    assert result.policy_version
