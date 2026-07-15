"""Tests for OPAClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from skylize.decision_engine.exceptions import OPAPolicyDenied
from skylize.decision_engine.opa_client import OPAClient

from .conftest import make_decision_context, make_scoring_result


def _client(settings) -> OPAClient:
    return OPAClient(settings)


def _mock_response(status: int, body: dict) -> AsyncMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    return resp


# ---------------------------------------------------------------------------
# allow=True → returns (True, [])
# ---------------------------------------------------------------------------

async def test_allow_true_returns_true_empty_reasons(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {"result": {"allow": True}})
        result = await client.evaluate(ctx)

    assert result.allow is True
    assert result.deny_reasons == []
    await client.close()


# ---------------------------------------------------------------------------
# allow=False, deny_reasons=[...] → returns (False, reasons)
# ---------------------------------------------------------------------------

async def test_allow_false_with_deny_reasons(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {
            "result": {"allow": False, "deny_reasons": ["budget_exceeded", "agent_suspended"]}
        })
        result = await client.evaluate(ctx)

    assert result.allow is False
    assert result.deny_reasons == ["budget_exceeded", "agent_suspended"]
    await client.close()


# ---------------------------------------------------------------------------
# HTTP 500 → OPAPolicyDenied raised
# ---------------------------------------------------------------------------

async def test_http_500_raises_opa_policy_denied(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(500, {})
        with pytest.raises(OPAPolicyDenied):
            await client.evaluate(ctx)
    await client.close()


# ---------------------------------------------------------------------------
# Timeout → OPAPolicyDenied raised (fail-closed)
# ---------------------------------------------------------------------------

async def test_timeout_raises_opa_policy_denied(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(OPAPolicyDenied) as exc_info:
            await client.evaluate(ctx)

    assert "timeout" in exc_info.value.denial_reason.lower()
    await client.close()


# ---------------------------------------------------------------------------
# ConnectError → one retry → then OPAPolicyDenied (fail-closed)
# ---------------------------------------------------------------------------

async def test_connect_error_retries_once_then_fails_closed(settings):
    client = _client(settings)
    ctx = make_decision_context()

    call_count = 0

    async def _mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("refused")

    with patch.object(client._client, "post", side_effect=_mock_post):
        with patch("skylize.decision_engine.opa_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(OPAPolicyDenied) as exc_info:
                await client.evaluate(ctx)

    assert call_count == 2  # initial + one retry
    assert "unreachable" in exc_info.value.denial_reason.lower()
    await client.close()


# ---------------------------------------------------------------------------
# Input document built correctly — no PII fields forwarded
# ---------------------------------------------------------------------------

async def test_input_document_excludes_pii_fields(settings):
    client = _client(settings)
    ctx = make_decision_context(
        payload={
            "action_kind": "launch",            # SAFE — should be included
            "email": "user@example.com",        # PII — must be excluded
            "phone": "555-1234",                # PII — must be excluded
            "amount": 1000,                     # SAFE — should be included
            "secret_key": "sk-abc",             # NOT in allowlist — excluded
        }
    )

    input_doc = client._build_input(ctx, None)

    # Safe keys forwarded
    assert "action_kind" in input_doc["payload"]
    assert "amount" in input_doc["payload"]

    # PII / unknown keys excluded
    assert "email" not in input_doc["payload"]
    assert "phone" not in input_doc["payload"]
    assert "secret_key" not in input_doc["payload"]


# ---------------------------------------------------------------------------
# Input document includes scoring fields when scoring_result provided
# ---------------------------------------------------------------------------

async def test_input_includes_scoring_when_provided(settings):
    client = _client(settings)
    ctx = make_decision_context()
    scoring = make_scoring_result(risk_score=42.0, opp_score=65.0)

    input_doc = client._build_input(ctx, scoring)

    assert input_doc["risk_score"] == 42.0
    assert input_doc["opportunity_score"] == 65.0
    assert input_doc["risk_band"] == scoring.risk_band.value


# ---------------------------------------------------------------------------
# Input document WITHOUT scoring — no risk_* keys injected
# ---------------------------------------------------------------------------

async def test_input_excludes_scoring_when_none(settings):
    client = _client(settings)
    ctx = make_decision_context()

    input_doc = client._build_input(ctx, None)

    assert "risk_score" not in input_doc
    assert "risk_band" not in input_doc


# ---------------------------------------------------------------------------
# deny field (alt key) is also accepted
# ---------------------------------------------------------------------------

async def test_deny_field_alias_accepted(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {
            "result": {"allow": False, "deny": ["policy_violation"]}
        })
        result = await client.evaluate(ctx)

    assert result.allow is False
    assert result.deny_reasons == ["policy_violation"]
    await client.close()


# ---------------------------------------------------------------------------
# Missing allow key defaults to deny (fail-closed)
# ---------------------------------------------------------------------------

async def test_missing_allow_key_defaults_to_deny(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {"result": {}})
        result = await client.evaluate(ctx)

    assert result.allow is False
    await client.close()


# ---------------------------------------------------------------------------
# require_human=True → surfaced regardless of allow
# ---------------------------------------------------------------------------

async def test_require_human_true_surfaced(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {
            "result": {"allow": True, "require_human": True, "policy_version": "v3"}
        })
        result = await client.evaluate(ctx)

    assert result.require_human is True
    await client.close()


# ---------------------------------------------------------------------------
# require_human absent → defaults to False (undefined Rego rule, not a gap)
# ---------------------------------------------------------------------------

async def test_require_human_absent_defaults_false(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {"result": {"allow": True}})
        result = await client.evaluate(ctx)

    assert result.require_human is False
    await client.close()


# ---------------------------------------------------------------------------
# policy_version round-trips when present
# ---------------------------------------------------------------------------

async def test_policy_version_present_round_trips(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {
            "result": {"allow": True, "policy_version": "spend-v2.1"}
        })
        result = await client.evaluate(ctx)

    assert result.policy_version == "spend-v2.1"
    await client.close()


# ---------------------------------------------------------------------------
# policy_version absent on a live allow → None, flagged (logged), not raised
# ---------------------------------------------------------------------------

async def test_policy_version_absent_on_allow_logs_warning(settings, caplog):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {"result": {"allow": True}})
        with caplog.at_level("WARNING", logger="skylize.decision_engine.opa_client"):
            result = await client.evaluate(ctx)

    assert result.allow is True
    assert result.policy_version is None
    assert any("policy_version" in message for message in caplog.messages)
    await client.close()


# ---------------------------------------------------------------------------
# Non-dict result (e.g. policy path misconfigured to a leaf boolean rule) →
# fail-closed, not a crash
# ---------------------------------------------------------------------------

async def test_non_dict_result_fails_closed(settings):
    client = _client(settings)
    ctx = make_decision_context()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(200, {"result": True})
        with pytest.raises(OPAPolicyDenied):
            await client.evaluate(ctx)
    await client.close()
