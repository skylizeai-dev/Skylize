"""Shared fixtures for decision_engine tests."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.models import (
    DecisionContext,
    DecisionOutcome,
    DecisionResult,
    EvaluationStepRecord,
    RiskBand,
    ScoringResult,
)


# ---------------------------------------------------------------------------
# Settings stub (no real env vars required)
# ---------------------------------------------------------------------------

@pytest.fixture()
def settings() -> DecisionEngineSettings:
    return DecisionEngineSettings(
        opa_url="http://opa:8181",
        opa_policy_path="skylize/decision",
        opa_timeout_seconds=2.0,
        redis_url="redis://localhost:6379",
        redis_consumer_group="cg:decision_engine",
        redis_consumer_name="test-consumer",
        redis_idle_time_ms=60000,
        redis_max_retries=3,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        langfuse_host="https://cloud.langfuse.com",
        database_url="postgresql://test:test@localhost/test",
        capital_reserve_floor_pct=0.15,
        hitl_expiry_hours=48,
    )


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(return_value=[])
    redis.xack = AsyncMock(return_value=1)
    redis.xadd = AsyncMock(return_value="1234567890123-0001")
    redis.set = AsyncMock(return_value=True)  # setnx via set(..., nx=True)
    redis.hincrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
    redis.xgroup_create = AsyncMock(return_value=True)
    return redis


# ---------------------------------------------------------------------------
# DB / asyncpg mock
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal asyncpg connection stub."""

    def __init__(self) -> None:
        self.execute = AsyncMock(return_value=None)
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self._executed: list[tuple[str, tuple]] = []

    async def _record_execute(self, sql: str, *args: Any) -> None:
        self._executed.append((sql, args))


@pytest.fixture()
def fake_conn() -> _FakeConn:
    return _FakeConn()


@pytest.fixture()
def mock_db(fake_conn: _FakeConn) -> MagicMock:
    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield fake_conn

    @asynccontextmanager
    async def _admin_session():
        yield fake_conn

    db.tenant_session = _tenant_session
    db.admin_session = _admin_session
    return db


# ---------------------------------------------------------------------------
# Factory fixtures
# ---------------------------------------------------------------------------

def make_decision_context(
    tenant_id: str = "tenant-abc",
    department: str = "creative",
    event_type: str = "creative.review_requested",
    payload: dict | None = None,
    event_id: str | None = None,
) -> DecisionContext:
    return DecisionContext(
        event_id=event_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        department=department,
        event_type=event_type,
        payload=payload or {},
        received_at=datetime.now(timezone.utc),
    )


def make_scoring_result(
    risk_score: float = 25.0,
    opp_score: float = 55.0,
    risk_band: RiskBand = RiskBand.LOW,
) -> ScoringResult:
    return ScoringResult(
        risk_score=risk_score,
        opportunity_score=opp_score,
        risk_band=risk_band,
        confidence=0.8,
        factors={},
    )


def make_decision_result(
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
    event_id: str | None = None,
    tenant_id: str = "tenant-abc",
    steps: list[EvaluationStepRecord] | None = None,
) -> DecisionResult:
    eid = event_id or str(uuid.uuid4())
    return DecisionResult(
        decision_id=str(uuid.uuid4()),
        event_id=eid,
        tenant_id=tenant_id,
        outcome=outcome,
        scoring=make_scoring_result(),
        capital=None,
        final_reason="test",
        steps=steps or [],
        evaluated_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def decision_context() -> DecisionContext:
    return make_decision_context()


@pytest.fixture()
def scoring_result() -> ScoringResult:
    return make_scoring_result()


@pytest.fixture()
def decision_result() -> DecisionResult:
    return make_decision_result()


# ---------------------------------------------------------------------------
# valid_decision_event_fields — matches what DecisionEngineConsumer decodes
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_decision_event_fields() -> dict[str, str]:
    """Flat Redis stream fields matching a creative.review_requested event."""
    return {
        "event": (
            '{"type":"creative.review_requested",'
            '"event_id":"' + str(uuid.uuid4()) + '",'
            '"tenant_id":"tenant-abc",'
            '"department":"creative",'
            '"partition_key":"brief:1",'
            '"correlation_id":"' + str(uuid.uuid4()) + '",'
            '"schema_version":"1.0",'
            '"category":"creative",'
            '"payload":{"brief_id":"' + str(uuid.uuid4()) + '",'
            '"asset_ids":["' + str(uuid.uuid4()) + '"],'
            '"proposed_action":"approve_internal",'
            '"proposed_spend_minor_units":null}}'
        )
    }
