"""Edge tests for the /api/v1/agent-prompts endpoint (n8n integration).

The endpoint validates X-Skylize-API-Key against SKYLIZE_N8N_API_KEY.  We
override the cached Settings singleton via monkeypatch + module-level cache
reset so each test class starts with a clean config.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skylize.contracts.mvp.sdr import sdr_outreach_agent
from skylize.edge.gateway import create_app

VALID_KEY = "test-n8n-api-key-12345"


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SKYLIZE_N8N_API_KEY", VALID_KEY)
    # Reset the settings singleton so the new env var is picked up.
    import skylize.config as _cfg
    monkeypatch.setattr(_cfg, "_settings", None)
    with TestClient(create_app()) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth: 401 cases
# ---------------------------------------------------------------------------


def test_missing_api_key_returns_401(client: TestClient) -> None:
    r = client.get("/api/v1/agent-prompts/sdr_outreach_agent")
    assert r.status_code == 401


def test_wrong_api_key_returns_401(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": "definitely-wrong"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Auth: unconfigured key → 503 fail-closed
# ---------------------------------------------------------------------------


def test_unconfigured_key_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("SKYLIZE_N8N_API_KEY", "")
    import skylize.config as _cfg
    monkeypatch.setattr(_cfg, "_settings", None)
    with TestClient(create_app()) as c:
        r = c.get(
            "/api/v1/agent-prompts/sdr_outreach_agent",
            headers={"X-Skylize-API-Key": "anything"},
        )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Happy path: valid key
# ---------------------------------------------------------------------------


def test_valid_key_returns_200(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.status_code == 200


def test_response_contains_agent_id(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.json()["agent_id"] == "sdr_outreach_agent"


def test_response_contains_system_prompt(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    body = r.json()
    assert "system_prompt" in body
    assert sdr_outreach_agent.agent_role in body["system_prompt"]


def test_max_tokens_matches_contract(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.json()["max_token_budget"] == sdr_outreach_agent.max_token_budget


def test_model_tier_worker_is_mini(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.json()["model_tier"] == "mini"


def test_authority_level_in_response(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.json()["authority_level"] == "worker"


def test_hitl_trigger_present_in_system_prompt(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert "first_external_launch" in r.json()["system_prompt"]


# ---------------------------------------------------------------------------
# 404: unknown agent
# ---------------------------------------------------------------------------


def test_unknown_agent_returns_404(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/does_not_exist",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Agency agent: smoke test (verifies both new agent families work through edge)
# ---------------------------------------------------------------------------


def test_agency_deliverable_drafter_returns_200(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/agency_deliverable_drafter",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"] == "agency_deliverable_drafter"
    assert body["model_tier"] == "mini"
    assert "brand_legal_sensitive" in body["system_prompt"]
