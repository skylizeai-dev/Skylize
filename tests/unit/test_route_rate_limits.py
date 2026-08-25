"""Rate limits on the routes that spend money and the routes that mint credentials.

`POST /agents/execute` is the only route that calls a paid provider and had no
limit at all. `/auth/register|login|refresh` had none either, which is what makes
credential stuffing on login and org-id probing on register cheap — and the
org-id probe is the residual enumeration surface left by the registration
refusal, so this limit is part of that fix, not decoration.

The two limits use DIFFERENT dependencies on purpose: `enforce_rate_limit`
resolves `get_context` and so requires an authenticated caller, which the /auth
routes by definition do not have. See `enforce_anonymous_rate_limit`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.edge.gateway import create_app
from skylize.edge.rate_limit import RateLimiter

_OWNER = {"X-Dev-Org": "org_rl", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_EXECUTE_BODY = {"agent_id": "hook_generator_agent", "input": {}}


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", "test-secret")
    config_module._settings = None
    yield
    config_module._settings = None


@pytest.fixture()
def client() -> Any:
    with TestClient(create_app()) as c:
        yield c


def _throttle(client: TestClient, per_minute: int) -> None:
    """Replace the app's limiter with a tight one for this test."""
    client.app.state.rate_limiter = RateLimiter(per_minute)  # type: ignore[attr-defined]


# ── /agents/execute ──────────────────────────────────────────────────────────

def test_execute_is_rate_limited(client: TestClient) -> None:
    _throttle(client, 1)
    first = client.post("/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_OWNER)
    second = client.post("/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_OWNER)

    # The first is answered by the route (422 here: empty input fails the agent's
    # schema). What matters is that it was NOT throttled and the second was.
    assert first.status_code != 429, first.text
    assert second.status_code == 429, second.text


def test_execute_limit_is_per_org(client: TestClient) -> None:
    """One tenant exhausting its budget must not refuse another's calls."""
    _throttle(client, 1)
    client.post("/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_OWNER)
    other = client.post(
        "/api/v1/agents/execute",
        json=_EXECUTE_BODY,
        headers={"X-Dev-Org": "org_other", "X-Dev-User": "u1", "X-Dev-Roles": "owner"},
    )
    assert other.status_code != 429, other.text


def test_execute_still_rejects_an_unauthorized_caller_under_the_limit(
    client: TestClient,
) -> None:
    """The limiter must not displace the role check — a viewer is still 403, and
    the code still says why."""
    resp = client.post(
        "/api/v1/agents/execute",
        json=_EXECUTE_BODY,
        headers={"X-Dev-Org": "org_rl", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "authorization_failed"


# ── /auth/* (unauthenticated) ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/auth/register",
            {"org_id": "org_probe", "email": "a@example.com", "password": "hunter2pw"},
        ),
        ("/api/v1/auth/login", {"email": "a@example.com", "password": "hunter2pw"}),
        ("/api/v1/auth/refresh", {"refresh_token": "not-a-real-token"}),
    ],
)
def test_auth_routes_are_rate_limited(
    client: TestClient, path: str, body: dict[str, Any]
) -> None:
    _throttle(client, 1)
    first = client.post(path, json=body)
    second = client.post(path, json=body)

    assert first.status_code != 429, first.text
    assert second.status_code == 429, second.text


def test_auth_routes_are_still_reachable_without_authentication(
    client: TestClient,
) -> None:
    """The regression this guards: enforce_rate_limit resolves get_context, so
    using IT here would 401 every registration and make the first credential
    unobtainable."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_fresh", "email": "fresh@example.com", "password": "hunter2pw"},
    )
    assert resp.status_code == 201, resp.text


def test_the_anonymous_bucket_cannot_collide_with_an_org_bucket(
    client: TestClient,
) -> None:
    """Anonymous keys are prefixed, so a caller cannot burn an org's budget (or
    borrow it) by arriving from an address that happens to match an org_id."""
    _throttle(client, 1)
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "testclient", "email": "a@example.com", "password": "hunter2pw"},
    )
    # The peer address under TestClient is "testclient"; an org of the same name
    # must still have its own full budget.
    resp = client.post(
        "/api/v1/agents/execute",
        json=_EXECUTE_BODY,
        headers={"X-Dev-Org": "testclient", "X-Dev-User": "u1", "X-Dev-Roles": "owner"},
    )
    assert resp.status_code != 429, resp.text
