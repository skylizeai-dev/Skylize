"""Integration test: OPAClient against a LIVE local OPA server.

Marked ``integration`` and skipped unless ``SKYLIZE_TEST_OPA_URL`` is set
(mirrors the Postgres/Redis integration convention in pyproject.toml). Bring the
server up with the placeholder bundle first, then run:

    docker compose -f infra/docker-compose.yml up -d opa
    SKYLIZE_TEST_OPA_URL=http://localhost:8181 \
        python -m pytest tests/decision_engine/test_opa_client_integration.py -q

The placeholder policy bundle (``policy/``) is FAIL-CLOSED: it denies everything
until real per-class policy content is authored. These tests therefore assert
DENY, never allow — a live ``allow=true`` here would mean the skeleton
accidentally approved something, which must never happen.

SCOPE — what these do NOT cover. Both tests exercise the HAPPY transport path: a
server that answers 200 with a parseable body. The fail-closed branches that
matter most under failure — timeout (opa_client.py:108), unreachable (:112),
non-200 (:126), malformed body (:133), non-object envelope (:144), non-dict
result (:153) — are covered only by unit tests using mock transports
(test_opa_client.py). No fail-closed path in this client has ever been exercised
against a real OPA process, because no OPA server has been stood up. Treat that
as untested-against-reality, not as proven.
"""
from __future__ import annotations

import os

import pytest

from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.opa_client import OPAClient

from .conftest import make_decision_context

_OPA_URL = os.environ.get("SKYLIZE_TEST_OPA_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _OPA_URL,
        reason="SKYLIZE_TEST_OPA_URL not set — requires a live OPA server "
        "(docker compose -f infra/docker-compose.yml up -d opa)",
    ),
]


def _settings(policy_path: str) -> DecisionEngineSettings:
    """Build settings pointing OPAClient at the live server under test."""
    return DecisionEngineSettings(
        opa_url=_OPA_URL,  # type: ignore[arg-type]  # guarded by skipif above
        opa_policy_path=policy_path,
        opa_timeout_seconds=2.0,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        database_url="postgresql://test:test@localhost/test",
    )


async def test_live_opa_reachable_and_fail_closed_on_default_path() -> None:
    """Shipped default path (skylize/decision/allow): the live server denies.

    Proves the client can reach a real OPA process AND that the placeholder
    bundle is fail-closed on the exact path config.py ships as the default.
    """
    client = OPAClient(_settings("skylize/decision/allow"))
    try:
        result = await client.evaluate(make_decision_context())
    finally:
        await client.close()

    # Real deny from a live OPA process — not a mock. Must never be True.
    assert result.allow is False


async def test_live_opa_returns_real_deny_object_on_package_path() -> None:
    """Aggregate package doc (skylize/decision): real {allow: false, deny_reasons:[...]}.

    Querying the package document (not the allow leaf) returns an object the
    client parses into a non-empty deny-reason list — proof this is a genuine
    fail-closed evaluation carrying reasons, not an empty/absent response.
    """
    client = OPAClient(_settings("skylize/decision"))
    try:
        result = await client.evaluate(make_decision_context())
    finally:
        await client.close()

    assert result.allow is False
    assert result.deny_reasons, "expected placeholder deny_reasons from the live bundle"
    assert any("PLACEHOLDER" in reason for reason in result.deny_reasons)
