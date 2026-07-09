"""Contract gate tests for the four new MVP workers: SDR + agency ops."""

from __future__ import annotations

import pytest

from skylize.contracts.base import FailureMode, HumanInLoopTrigger
from skylize.contracts.mvp import ALL_MVP_CONTRACTS
from skylize.contracts.mvp.agency import agency_deliverable_drafter, agency_requirements_analyst
from skylize.contracts.mvp.sdr import lead_qualifier_agent, sdr_outreach_agent
from skylize.contracts.registry import resolve_model

_ALL_4 = [
    sdr_outreach_agent,
    lead_qualifier_agent,
    agency_requirements_analyst,
    agency_deliverable_drafter,
]
_ALL_4_IDS = {c.agent_id for c in _ALL_4}


# ---------------------------------------------------------------------------
# Registry membership
# ---------------------------------------------------------------------------


def test_all_4_in_mvp_contracts() -> None:
    registry_ids = {c.agent_id for c in ALL_MVP_CONTRACTS}
    assert _ALL_4_IDS.issubset(registry_ids)


def test_mvp_contracts_total_count() -> None:
    # 15 original + seo_keyword + cfo + 2 sdr + 2 agency = 21. Update when the
    # MVP set deliberately grows; this guards against accidental drops.
    assert len(ALL_MVP_CONTRACTS) == 21


# ---------------------------------------------------------------------------
# Common invariants (parametrized over all 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract", _ALL_4, ids=lambda c: c.agent_id)
def test_authority_level_worker(contract) -> None:
    assert contract.authority_level == "worker"


@pytest.mark.parametrize("contract", _ALL_4, ids=lambda c: c.agent_id)
def test_governance_token_required(contract) -> None:
    assert contract.governance_token_required is True


@pytest.mark.parametrize("contract", _ALL_4, ids=lambda c: c.agent_id)
def test_input_output_schema_resolves(contract) -> None:
    resolve_model(contract.input_schema)
    resolve_model(contract.output_schema)


@pytest.mark.parametrize("contract", _ALL_4, ids=lambda c: c.agent_id)
def test_escalation_path_ends_at_human_owner(contract) -> None:
    assert contract.escalation_path[-1] == "human_owner"


@pytest.mark.parametrize("contract", _ALL_4, ids=lambda c: c.agent_id)
def test_agent_id_is_snake_case_no_spaces(contract) -> None:
    assert contract.agent_id == contract.agent_id.lower()
    assert " " not in contract.agent_id


# ---------------------------------------------------------------------------
# SDR-specific assertions
# ---------------------------------------------------------------------------


def test_sdr_outreach_first_external_launch_trigger() -> None:
    assert HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH in sdr_outreach_agent.human_in_loop_triggers


def test_sdr_outreach_fallback_degraded() -> None:
    assert sdr_outreach_agent.failure_mode == FailureMode.FALLBACK_DEGRADED


def test_sdr_outreach_department_sales() -> None:
    assert sdr_outreach_agent.department == "sales"


def test_sdr_outreach_can_write_to_outreach_sent() -> None:
    assert "sales:outreach:sent" in sdr_outreach_agent.memory_write_access


def test_lead_qualifier_no_hitl_triggers() -> None:
    assert lead_qualifier_agent.human_in_loop_triggers == []


def test_lead_qualifier_fallback_degraded() -> None:
    assert lead_qualifier_agent.failure_mode == FailureMode.FALLBACK_DEGRADED


def test_lead_qualifier_reads_icp_namespace() -> None:
    assert any("icp" in ns for ns in lead_qualifier_agent.memory_read_access)


# ---------------------------------------------------------------------------
# Agency-specific assertions
# ---------------------------------------------------------------------------


def test_agency_deliverable_brand_legal_trigger() -> None:
    assert HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE in agency_deliverable_drafter.human_in_loop_triggers


def test_agency_requirements_analyst_retry_then_escalate() -> None:
    assert agency_requirements_analyst.failure_mode == FailureMode.RETRY_THEN_ESCALATE


def test_agency_requirements_analyst_no_hitl_triggers() -> None:
    assert agency_requirements_analyst.human_in_loop_triggers == []


def test_agency_deliverable_drafter_fallback_degraded() -> None:
    assert agency_deliverable_drafter.failure_mode == FailureMode.FALLBACK_DEGRADED


def test_agency_deliverable_drafter_department() -> None:
    assert agency_deliverable_drafter.department == "agency_ops"


def test_agency_requirements_analyst_writes_requirements() -> None:
    assert any("requirements" in ns for ns in agency_requirements_analyst.memory_write_access)


def test_agency_deliverable_drafter_reads_brand_voice() -> None:
    assert "brand:voice" in agency_deliverable_drafter.memory_read_access
