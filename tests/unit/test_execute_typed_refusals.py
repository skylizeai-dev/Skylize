"""POST /agents/execute: every refusal it can produce is typed, not a 500.

`OrgSpendCeilingExceeded` and `LLMModelNotPriced` were raised by the adapter and
caught NOWHERE in `src/`, so a customer's first call on a fresh org — the exact
moment the spend-ceiling gate fails closed on a missing row — returned HTTP 500.
A 500 says "we broke"; these are governed refusals and a provisioning gap, and
the difference is the whole product.

Each case pins the status AND the machine-readable code, because several of them
share a status (three distinct 403s on this route) and the code is the only thing
that tells a client which remedy applies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from skylize.adapters.llm.gateway import (
    LLMMalformedResponse,
    LLMModelNotPriced,
    LLMProviderUnavailable,
    LLMTimeout,
)
from skylize.adapters.llm.spend_ceiling import OrgSpendCeilingExceeded
from skylize.edge.gateway import create_app

_OWNER = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_BODY = {
    "agent_id": "hook_generator_agent",
    "input": {
        "brand_name": "Acme",
        "product_description": "A revolutionary widget",
        "target_audience": "startup founders",
    },
}


@pytest.fixture()
def client() -> Any:
    with TestClient(create_app()) as c:
        yield c


def _execute_raising(client: TestClient, exc: Exception) -> Any:
    container = client.app.state.container  # type: ignore[attr-defined]
    original = container.agent_execution.execute
    container.agent_execution.execute = AsyncMock(side_effect=exc)
    try:
        return client.post("/api/v1/agents/execute", json=_BODY, headers=_OWNER)
    finally:
        container.agent_execution.execute = original


def _ceiling_breached() -> OrgSpendCeilingExceeded:
    return OrgSpendCeilingExceeded(
        org_id="org_a",
        billing_period="2026-07",
        ceiling_micros=5_000_000,
        period_to_date_micros=4_900_000,
        estimated_micros=250_000,
        reason="estimated post-call spend exceeds the ceiling",
    )


def _no_ceiling_configured() -> OrgSpendCeilingExceeded:
    # ceiling_micros None is case (a): no row for (org, period). period_to_date
    # is None too and means NOT READ, not zero.
    return OrgSpendCeilingExceeded(
        org_id="org_a",
        billing_period="2026-07",
        ceiling_micros=None,
        period_to_date_micros=None,
        estimated_micros=250_000,
        reason="no spend ceiling configured",
    )


# ── spend ceiling: two causes, two remedies, two codes ───────────────────────

def test_ceiling_breached_is_403_not_500(client: TestClient) -> None:
    resp = _execute_raising(client, _ceiling_breached())
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "spend_ceiling_exceeded"


def test_ceiling_breached_surfaces_the_callers_own_figures(client: TestClient) -> None:
    detail = _execute_raising(client, _ceiling_breached()).json()["detail"]
    assert "2026-07" in detail        # which period is capped
    assert "5000000" in detail        # their ceiling
    assert "4900000" in detail        # their spend so far
    assert "250000" in detail         # what this call would have cost
    # The refusal happens before egress; say so, because "did I get billed?" is
    # the customer's first question.
    assert "nothing was charged" in detail


def test_missing_ceiling_is_a_distinct_code_from_a_breach(client: TestClient) -> None:
    """A brand-new org hits this on its first call. Telling them they are out of
    budget would be false — no ceiling has been provisioned yet."""
    resp = _execute_raising(client, _no_ceiling_configured())
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "spend_ceiling_not_configured"


def test_missing_ceiling_does_not_claim_the_org_has_spent_zero(client: TestClient) -> None:
    """period_to_date is None in this case and means NOT READ. Rendering it as 0
    would assert the org has spent nothing, which may be false."""
    detail = _execute_raising(client, _no_ceiling_configured()).json()["detail"]
    assert "spent so far" not in detail
    assert "None" not in detail
    assert "operator" in detail  # names who fixes it


def test_the_two_ceiling_codes_differ(client: TestClient) -> None:
    breached = _execute_raising(client, _ceiling_breached()).json()["code"]
    missing = _execute_raising(client, _no_ceiling_configured()).json()["code"]
    assert breached != missing


# ── model not priced: a provisioning fault, not the caller's ─────────────────

def test_model_not_priced_is_503_not_500(client: TestClient) -> None:
    resp = _execute_raising(
        client, LLMModelNotPriced("no model_pricing entry for 'claude-sonnet-4-6'")
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "model_not_priced"


def test_model_not_priced_does_not_echo_the_model_id(client: TestClient) -> None:
    """Which model backs an agent is our configuration, not the caller's; it
    stays in the chained exception for the log."""
    detail = _execute_raising(
        client, LLMModelNotPriced("no model_pricing entry for 'claude-sonnet-4-6'")
    ).json()["detail"]
    assert "claude" not in detail
    assert "nothing was charged" in detail


# ── upstream provider failures ───────────────────────────────────────────────

def test_provider_timeout_is_504(client: TestClient) -> None:
    resp = _execute_raising(client, LLMTimeout("timed out after 120s"))
    assert resp.status_code == 504, resp.text
    assert resp.json()["code"] == "provider_timeout"


@pytest.mark.parametrize(
    "exc",
    [
        LLMProviderUnavailable("connection refused"),
        LLMMalformedResponse("body is not a Message"),
    ],
)
def test_provider_failures_are_502(client: TestClient, exc: Exception) -> None:
    resp = _execute_raising(client, exc)
    assert resp.status_code == 502, resp.text
    assert resp.json()["code"] == "provider_unavailable"


def test_no_provider_failure_leaks_the_underlying_message(client: TestClient) -> None:
    """The chained exception can carry a provider URL or SDK internals; the
    response must not."""
    detail = _execute_raising(
        client, LLMProviderUnavailable("https://api.internal.example/v1 refused")
    ).json()["detail"]
    assert "internal.example" not in detail


# ── the route's 403s stay distinguishable ────────────────────────────────────

def test_every_403_on_this_route_carries_a_distinct_code(client: TestClient) -> None:
    from skylize.app.agents.execution import AgentGovernanceRejected
    from skylize.app.governance import GovernanceDenied

    codes = {
        _execute_raising(client, AgentGovernanceRejected("r")).json()["code"],
        _execute_raising(client, GovernanceDenied("tenant kill switch engaged")).json()["code"],
        _execute_raising(client, _ceiling_breached()).json()["code"],
        _execute_raising(client, _no_ceiling_configured()).json()["code"],
        # The authorization failure is the fifth 403 shape on this route.
        client.post(
            "/api/v1/agents/execute",
            json=_BODY,
            headers={"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"},
        ).json()["code"],
    }
    assert len(codes) == 5, codes
