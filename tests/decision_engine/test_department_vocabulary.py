"""ADR-0005 (Alternative A) — the department vocabulary is an explicit table.

These tests lock the three properties the ADR was accepted for:

1. A growth-department campaign proposal passes AUTHORITY (it was auto-rejected
   under the old `{category}.`-prefix derivation).
2. The subscription set and the AUTHORITY allow-list come from the SAME table,
   so transport and authority cannot drift.
3. Department ownership matches the agent contracts, which are the authority.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from skylize.contracts.mvp.growth import director_growth
from skylize.contracts.mvp.sdr import sdr_outreach_agent
from skylize.decision_engine import constants
from skylize.decision_engine.models import DecisionOutcome, OPAResult
from skylize.decision_engine.opa_client import OPAClient
from skylize.decision_engine.pipeline import EvaluationPipeline
from skylize.decision_engine.scoring import ScoringEngine

from .conftest import make_decision_context


def _pipeline(settings) -> EvaluationPipeline:
    opa = MagicMock(spec=OPAClient)
    opa.evaluate = AsyncMock(
        return_value=OPAResult(
            allow=True, require_human=False, deny_reasons=[], policy_version="test"
        )
    )
    capital = MagicMock()
    capital.extract_requested_amount = AsyncMock(return_value=None)
    return EvaluationPipeline(
        opa_client=opa,
        scoring_engine=ScoringEngine(settings),
        capital_dal=capital,
        settings=settings,
        event_bus=None,
    )


# ---------------------------------------------------------------------------
# GATE 1: the regression the ADR exists to fix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "event_type",
    ["sales.campaign_proposed", "sales.budget_reallocation_proposed"],
)
async def test_growth_department_spend_proposal_passes_authority(settings, event_type):
    """A `sales.`-category event stamped department="growth" must NOT be rejected.

    Under the prefix derivation ALLOWED_DEPARTMENTS was {"creative", "sales"},
    so every real spend proposal died at stage 1 with a governance-shaped audit
    trail claiming policy denied it.
    """
    pipeline = _pipeline(settings)
    ctx = make_decision_context(department="growth", event_type=event_type)

    result = await pipeline.evaluate(ctx)

    authority = result.steps[0]
    assert authority.stage.value == "AUTHORITY"
    assert authority.passed, authority.detail
    assert authority.detail["department_allowed"] is True
    assert result.outcome != DecisionOutcome.REJECTED


async def test_sales_department_campaign_proposal_is_rejected(settings):
    """The inverse: `sales` is the SDR channel and does not propose campaigns."""
    pipeline = _pipeline(settings)
    ctx = make_decision_context(
        department="sales", event_type="sales.campaign_proposed"
    )

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 1
    assert result.steps[0].detail["department_allowed"] is False
    assert "not served by the engine" in result.final_reason


# ---------------------------------------------------------------------------
# GATE 2: single source of truth — subscriptions and AUTHORITY share one table
# ---------------------------------------------------------------------------

def test_subscriptions_and_authority_derive_from_one_table():
    """Both projections come from ALLOWED_EVENT_TYPES_BY_DEPARTMENT, nothing else.

    They are no longer identical — `governance` is subscribed (the engine must
    hear a human's verdict) but not authorized (it never accepts a governance
    *proposal*). The invariant that matters is that neither is hand-maintained:
    both are computed from the one table, so a department cannot be authorized
    without being subscribed, and the gap is exactly the resume-only departments.
    """
    assert set(constants.SUBSCRIBED_DEPARTMENTS) == set(
        constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT
    )
    # Authority is a subset: you cannot decide for a channel you do not hear.
    assert set(constants.ALLOWED_DEPARTMENTS) <= set(constants.SUBSCRIBED_DEPARTMENTS)
    # ...and the difference is exactly the departments with no proposal types.
    resume_only = {
        department
        for department, types in constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT.items()
        if not (types - constants.RESUME_EVENT_TYPES)
    }
    assert (
        set(constants.SUBSCRIBED_DEPARTMENTS) - set(constants.ALLOWED_DEPARTMENTS)
        == resume_only
    )


def test_governance_is_subscribed_but_never_authority_for_a_proposal():
    """The coupling gate: adding `governance` must drive subscription AND authority.

    Subscription, so a `governance.human_approval_received` verdict reaches the
    engine at all; authority, so that same type can never be evaluated as a
    proposal. Both from the one table.
    """
    assert "governance" in constants.SUBSCRIBED_DEPARTMENTS
    assert "governance" in constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT
    assert (
        "governance.human_approval_received"
        in constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT["governance"]
    )
    # Not authorized to originate a decision.
    assert "governance" not in constants.ALLOWED_DEPARTMENTS
    assert constants.PROPOSAL_EVENT_TYPES_BY_DEPARTMENT["governance"] == frozenset()


def test_no_resume_type_is_ever_a_proposal_type():
    """A resume event must not be evaluable by the pipeline, in any department."""
    for department, types in constants.PROPOSAL_EVENT_TYPES_BY_DEPARTMENT.items():
        assert not (types & constants.RESUME_EVENT_TYPES), department


async def test_authority_rejects_a_resume_event_reaching_the_pipeline(settings):
    """Backstop: if a routing regression sends a verdict to the pipeline, REJECT.

    Evaluating it would let policy overturn a decision a human already made.
    """
    pipeline = _pipeline(settings)
    ctx = make_decision_context(
        department="governance", event_type="governance.human_approval_received"
    )

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 1
    assert result.steps[0].stage.value == "AUTHORITY"
    assert result.steps[0].detail["department_allowed"] is False


def test_subscribed_event_types_are_the_flattened_table():
    """No event type may be authorized without a department that subscribes."""
    flattened = {
        event_type
        for types in constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT.values()
        for event_type in types
    }
    assert set(constants.SUBSCRIBED_EVENT_TYPES) == flattened


def test_no_department_is_derived_from_an_event_type_prefix():
    """The bug in one assertion: category prefixes are not department names."""
    prefixes = {t.split(".", 1)[0] for t in constants.SUBSCRIBED_EVENT_TYPES}
    assert "sales" in prefixes
    assert "sales" not in constants.ALLOWED_DEPARTMENTS


# ---------------------------------------------------------------------------
# GATE 3: the table agrees with the agent contracts, the real authority
# ---------------------------------------------------------------------------

def test_table_matches_agent_contract_ownership():
    assert director_growth.department == "growth"
    assert "sales.campaign_proposed" in (
        constants.ALLOWED_EVENT_TYPES_BY_DEPARTMENT[director_growth.department]
    )

    # The SDR agents own department="sales" and never propose campaigns, so the
    # engine must not serve their channel at all.
    assert sdr_outreach_agent.department == "sales"
    assert sdr_outreach_agent.department not in constants.ALLOWED_DEPARTMENTS
