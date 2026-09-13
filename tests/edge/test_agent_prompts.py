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


# ---------------------------------------------------------------------------
# Constant-time key comparison (hmac.compare_digest)
#
# The swap from `!=` to `hmac.compare_digest` must not change ANY outcome: a
# matching key is still 200-eligible, a non-matching key is still 401. These
# pin both branches so a future refactor of the comparison cannot silently
# invert them.
# ---------------------------------------------------------------------------


def test_constant_time_compare_matching_key_still_200(client: TestClient) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "presented",
    [
        VALID_KEY[:-1],              # correct prefix, one char short
        VALID_KEY + "x",             # correct prefix, one char long
        "x" + VALID_KEY[1:],         # same length, differs at char 0
        VALID_KEY[:-1] + "X",        # same length, differs at last char
        "",                          # empty
        # Non-ASCII, sent as raw bytes because httpx refuses a non-ASCII str
        # header. Starlette latin-1-decodes it, so the route sees a str with a
        # codepoint > 127 -- which `hmac.compare_digest` rejects with TypeError
        # unless both sides are encoded first. Must be 401, never 500.
        b"\xe9" + VALID_KEY[1:].encode(),
    ],
)
def test_constant_time_compare_non_matching_key_still_401(
    client: TestClient, presented: str | bytes
) -> None:
    r = client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": presented},
    )
    assert r.status_code == 401


def test_route_uses_compare_digest_not_plain_equality() -> None:
    """The comparison is timing-safe at the source level, not just in outcome."""
    import inspect

    from skylize.edge.routes import agent_prompts

    src = inspect.getsource(agent_prompts._verify_api_key)
    assert "hmac.compare_digest" in src
    assert "x_skylize_api_key != expected" not in src


# ---------------------------------------------------------------------------
# Rate limiting (enforce_anonymous_rate_limit)
#
# Proven by actually exhausting the window and observing a real 429, not by
# asserting the dependency is present in the route's signature.
# ---------------------------------------------------------------------------


@pytest.fixture()
def limited_client(monkeypatch) -> TestClient:
    """Gateway with the rate limit squeezed to 3/min so a 429 is reachable.

    The limiter's clock is frozen: `RateLimiter` uses a fixed 60s window
    (rate_limit.py:20), so a real wall clock crossing a minute boundary
    mid-test would reset the count and the request expected to be 429 would
    return 200. Freezing keeps every request inside one window.
    """
    monkeypatch.setenv("SKYLIZE_N8N_API_KEY", VALID_KEY)
    monkeypatch.setenv("SKYLIZE_RATE_LIMIT_PER_MINUTE", "3")
    import skylize.config as _cfg
    monkeypatch.setattr(_cfg, "_settings", None)

    import types

    import skylize.edge.rate_limit as _rl
    # Rebind only the NAME `time` inside rate_limit -- patching the real
    # `time.time` would freeze the clock process-wide for the whole test.
    monkeypatch.setattr(_rl, "time", types.SimpleNamespace(time=lambda: 1_700_000_000.0))

    with TestClient(create_app()) as c:
        yield c


def test_rate_limit_returns_429_after_limit_exhausted(limited_client: TestClient) -> None:
    for _ in range(3):
        r = limited_client.get(
            "/api/v1/agent-prompts/sdr_outreach_agent",
            headers={"X-Skylize-API-Key": VALID_KEY},
        )
        assert r.status_code == 200
    r = limited_client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": VALID_KEY},
    )
    assert r.status_code == 429
    assert r.json()["detail"] == "rate limit exceeded"


def test_rate_limit_runs_before_auth(limited_client: TestClient) -> None:
    """Unauthenticated floods are limited too -- the limiter precedes the key check.

    Three bad-key requests exhaust the peer's window; the fourth is 429, not
    401, proving the limiter runs first and an attacker cannot burn the route
    for free by omitting the key.
    """
    for _ in range(3):
        r = limited_client.get(
            "/api/v1/agent-prompts/sdr_outreach_agent",
            headers={"X-Skylize-API-Key": "wrong"},
        )
        assert r.status_code == 401
    r = limited_client.get(
        "/api/v1/agent-prompts/sdr_outreach_agent",
        headers={"X-Skylize-API-Key": "wrong"},
    )
    assert r.status_code == 429
