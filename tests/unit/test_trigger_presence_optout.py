"""`defers_on_trigger_presence` — the opt-out, and the 21 contracts it must not move.

Stage 2.5 (`_decide_agent_execution`) used to have exactly three outcomes, keyed
only on which triggers a contract DECLARES:

    FIRST_EXTERNAL_LAUNCH present -> deferred_to_human
    no triggers at all            -> approved
    any other trigger present     -> deferred_to_human

`defers_on_trigger_presence` adds a fourth path that can only ever turn the THIRD
case into `approved`, and only for a contract that opts in to it. This module
proves that claim two ways rather than asserting it:

  1. `_reference_outcome_before_the_field` reimplements the pre-change rule as a
     pure function of the contract. Every registered contract except
     `cowork_agent` must still produce exactly that outcome from the REAL
     evaluator. That is the byte-identical check -- not "looks unchanged", but
     "matches an independent restatement of the old rule".
  2. The opt-out is proven to be genuinely inert unless set: every contract
     except `cowork_agent` carries the default `True`.

Why this matters more than a normal regression test: the field changes a
GOVERNANCE verdict. A silent flip on any other contract would turn a decision
that used to reach a human into one that does not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from skylize.app.decision_engine.evaluator import DecisionEvaluator
from skylize.app.decision_engine.events import (
    AGENT_EXECUTE_ACTION_KIND,
    DecisionProposal,
)
from skylize.contracts.base import AgentContract, HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryCapitalRepository

ORG = "org_optout"
OPTED_OUT = "cowork_agent"


def _evaluator() -> DecisionEvaluator:
    return DecisionEvaluator(registry=MVP_REGISTRY, capital=InMemoryCapitalRepository())


def _execute_proposal(agent_id: str, department: str) -> DecisionProposal:
    """The shape `AgentExecutionService._build_execution_proposal` produces.

    No spend, no security verdict, empty metadata, requires_external_launch
    False -- which is precisely why stages 0/1/2 cannot fire for this vertical
    and stage 2.5 is the whole verdict.
    """
    corr = uuid4()
    return DecisionProposal(
        proposal_id=corr,
        correlation_id=corr,
        partition_key=f"agent_execute:{agent_id}:{corr}",
        org_id=ORG,
        department=department,
        proposing_agent_id=agent_id,
        action_kind=AGENT_EXECUTE_ACTION_KIND,
        requires_external_launch=False,
        occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        source_event_id=corr,
        source_type=AGENT_EXECUTE_ACTION_KIND,
        metadata={},
    )


def _reference_outcome_before_the_field(contract: AgentContract) -> str:
    """The stage-2.5 rule EXACTLY as it stood before `defers_on_trigger_presence`.

    Deliberately written from the rule, not from the current code, so it is an
    independent statement rather than a mirror of the implementation.
    """
    triggers = contract.human_in_loop_triggers
    if HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH in triggers:
        return "deferred_to_human"
    if not triggers:
        return "approved"
    return "deferred_to_human"


def _others() -> list[AgentContract]:
    return [c for c in MVP_REGISTRY.all() if c.agent_id != OPTED_OUT]


# ── 1. Nothing else moved ────────────────────────────────────────────────────

def test_exactly_one_contract_opts_out() -> None:
    opted = [c.agent_id for c in MVP_REGISTRY.all() if not c.defers_on_trigger_presence]
    assert opted == [OPTED_OUT]


def test_every_other_contract_keeps_the_default() -> None:
    """The field is additive with a True default, so absence of an explicit
    value must mean unchanged behaviour."""
    for contract in _others():
        assert contract.defers_on_trigger_presence is True, contract.agent_id


@pytest.mark.asyncio
async def test_every_other_contract_decides_exactly_as_before() -> None:
    """The byte-identical claim, measured against an independent restatement of
    the old rule -- across all 21 non-cowork contracts, both the ones that
    approve and the ones that defer."""
    others = _others()
    assert len(others) == 21, f"registry size changed: {len(others) + 1} contracts"

    for contract in others:
        result = await _evaluator().evaluate(
            _execute_proposal(contract.agent_id, contract.department)
        )
        assert result.outcome == _reference_outcome_before_the_field(contract), (
            f"{contract.agent_id} changed verdict"
        )


@pytest.mark.asyncio
async def test_the_defer_and_approve_populations_are_both_non_empty() -> None:
    """Guards the test above from passing vacuously: if every contract happened
    to approve, 'unchanged' would prove nothing about the defer branch."""
    deferring = [
        c for c in _others() if _reference_outcome_before_the_field(c) == "deferred_to_human"
    ]
    approving = [
        c for c in _others() if _reference_outcome_before_the_field(c) == "approved"
    ]
    assert len(deferring) == 12, [c.agent_id for c in deferring]
    assert len(approving) == 9, [c.agent_id for c in approving]


# ── 2. The opt-out does what it says, and no more ─────────────────────────────

@pytest.mark.asyncio
async def test_the_opted_out_contract_now_approves() -> None:
    contract = MVP_REGISTRY.resolve(OPTED_OUT)
    assert contract.human_in_loop_triggers, "the point is that it HAS triggers"
    result = await _evaluator().evaluate(
        _execute_proposal(OPTED_OUT, contract.department)
    )
    assert result.outcome == "approved"


@pytest.mark.asyncio
async def test_first_external_launch_still_defers_even_when_opted_out() -> None:
    """The opt-out is checked AFTER the FIRST_EXTERNAL_LAUNCH branch, so it can
    never suppress the external-publication defer. Proven on a contract that
    carries BOTH the trigger and the opt-out -- a combination no registered
    contract has, which is exactly why it needs constructing here."""
    base = MVP_REGISTRY.resolve(OPTED_OUT)
    hybrid = base.model_copy(
        update={
            "agent_id": "hybrid_external_launcher",
            "human_in_loop_triggers": [HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH],
            "defers_on_trigger_presence": False,
        }
    )
    registry = type(MVP_REGISTRY)([hybrid])
    evaluator = DecisionEvaluator(registry=registry, capital=InMemoryCapitalRepository())
    result = await evaluator.evaluate(
        _execute_proposal("hybrid_external_launcher", hybrid.department)
    )
    assert result.outcome == "deferred_to_human"
    assert result.hitl_trigger == HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH.value


@pytest.mark.asyncio
async def test_opting_out_with_no_triggers_is_indistinguishable_from_today() -> None:
    """The flag must not become a second way to express 'no triggers'."""
    base = MVP_REGISTRY.resolve("seo_keyword_agent")
    assert base.human_in_loop_triggers == []
    opted = base.model_copy(
        update={"agent_id": "seo_opted", "defers_on_trigger_presence": False}
    )
    registry = type(MVP_REGISTRY)([opted])
    evaluator = DecisionEvaluator(registry=registry, capital=InMemoryCapitalRepository())
    result = await evaluator.evaluate(_execute_proposal("seo_opted", opted.department))
    assert result.outcome == "approved"
