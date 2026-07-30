"""The 403 response contract: three causes, three machine-readable codes.

POST /api/v1/agents/execute can refuse with 403 for three completely different
reasons, and before `edge/errors.py` the ONLY discriminator was a message
prefix. These tests pin the structural discriminator AND pin that the human
message did not move, because an existing consumer reads `detail` as a string
(website/src/lib/skylize/client.ts:48-51).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from skylize.app.agents.execution import AgentGovernanceRejected
from skylize.app.governance import GovernanceDenied
from skylize.edge.errors import CodedHTTPException, ErrorCode, install_error_handlers
from skylize.edge.gateway import create_app

_OWNER_HEADERS = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_VIEWER_HEADERS = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"}

_VALID_INPUT = {
    "brand_name": "Acme",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
    "tone": "energetic",
}

_EXECUTE_BODY = {"agent_id": "hook_generator_agent", "input": _VALID_INPUT}


@pytest.fixture()
def client() -> Any:
    with TestClient(create_app()) as c:
        yield c


def _force_execute_failure(client: TestClient, exc: Exception) -> Any:
    """Make the execute service raise `exc`, then POST /agents/execute."""
    container = client.app.state.container  # type: ignore[attr-defined]
    original = container.agent_execution.execute
    container.agent_execution.execute = AsyncMock(side_effect=exc)
    try:
        return client.post(
            "/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_OWNER_HEADERS
        )
    finally:
        container.agent_execution.execute = original


# ── the three 403 causes carry three distinct codes ──────────────────────────

def test_decision_reject_403_carries_decision_rejected_code(client: TestClient) -> None:
    resp = _force_execute_failure(
        client, AgentGovernanceRejected("spend over ceiling")
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "decision_rejected"


def test_governance_denied_403_carries_governance_denied_code(
    client: TestClient,
) -> None:
    resp = _force_execute_failure(client, GovernanceDenied("tenant kill switch engaged"))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "governance_denied"


def test_authorization_failure_403_carries_authorization_failed_code(
    client: TestClient,
) -> None:
    # Never reaches the handler: require_any_role_or_user refuses the caller.
    resp = client.post(
        "/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_VIEWER_HEADERS
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "authorization_failed"


def test_the_three_403_codes_are_pairwise_distinct(client: TestClient) -> None:
    codes = {
        _force_execute_failure(client, AgentGovernanceRejected("r")).json()["code"],
        _force_execute_failure(client, GovernanceDenied("agent suspended")).json()["code"],
        client.post(
            "/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_VIEWER_HEADERS
        ).json()["code"],
    }
    assert len(codes) == 3, codes


# ── `detail` is unchanged for existing consumers ─────────────────────────────

def test_detail_is_still_the_same_string_for_every_coded_403(
    client: TestClient,
) -> None:
    """Existing consumers read `detail` as a str; that must still hold, with the
    same prefixes the messages carried before the code was introduced."""
    rejected = _force_execute_failure(
        client, AgentGovernanceRejected("spend over ceiling")
    ).json()
    denied = _force_execute_failure(
        client, GovernanceDenied("tenant kill switch engaged")
    ).json()
    unauthorized = client.post(
        "/api/v1/agents/execute", json=_EXECUTE_BODY, headers=_VIEWER_HEADERS
    ).json()

    for body in (rejected, denied, unauthorized):
        assert isinstance(body["detail"], str)
        assert body["detail"]

    assert rejected["detail"] == "decision rejected: spend over ceiling"
    assert denied["detail"] == "governance denied: tenant kill switch engaged"
    assert unauthorized["detail"] == (
        "requires one of roles: ['admin', 'operator', 'owner']"
    )


def test_uncoded_http_exception_body_is_unchanged(client: TestClient) -> None:
    """A route that still raises a plain HTTPException keeps FastAPI's exact
    default shape — exactly `detail`, no `code` key. Registering the coded
    handler for the SUBCLASS only is what guarantees this."""
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "does_not_exist_agent", "input": {}},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 404
    assert set(resp.json()) == {"detail"}
    assert isinstance(resp.json()["detail"], str)


# ── the vocabulary is closed ─────────────────────────────────────────────────

def test_error_code_vocabulary_is_a_closed_enum() -> None:
    """A closed set, not free strings: a client may switch on it exhaustively."""
    assert {c.value for c in ErrorCode} == {
        "decision_rejected",
        "governance_denied",
        "authorization_failed",
        "org_not_available",
    }
    with pytest.raises(ValueError):
        ErrorCode("not_a_real_code")


def test_coded_exception_needs_a_member_not_a_bare_string() -> None:
    """`code` is typed as ErrorCode; passing a foreign string cannot become a
    valid member, so the wire vocabulary cannot be widened by a raise site."""
    with pytest.raises(ValueError):
        CodedHTTPException(403, "x", code=ErrorCode("invented"))


# ── handler mechanics ────────────────────────────────────────────────────────

def test_handler_forwards_headers_and_keeps_status() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/coded")
    async def coded() -> None:
        raise CodedHTTPException(
            403,
            "nope",
            code=ErrorCode.AUTHORIZATION_FAILED,
            headers={"X-Trace": str(uuid4())},
        )

    @app.get("/plain")
    async def plain() -> None:
        raise HTTPException(status_code=403, detail="nope")

    with TestClient(app) as c:
        coded_resp = c.get("/coded")
        plain_resp = c.get("/plain")

    assert coded_resp.status_code == 403
    assert "X-Trace" in coded_resp.headers
    assert coded_resp.json() == {"detail": "nope", "code": "authorization_failed"}
    # The two differ by exactly the added key.
    assert plain_resp.json() == {"detail": "nope"}
