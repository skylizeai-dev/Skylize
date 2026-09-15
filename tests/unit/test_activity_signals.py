"""The signal the scheduled sweep submits: counts in, `ActivitySignalIn` out.

These pin the MAPPING, which is what lands on a governance record. The SQL that
produces the counts is exercised against real Postgres in
tests/integration/test_activity_signal_source.py -- counting rows is precisely
the thing a fake repository cannot prove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skylize.app.autonomy.pilot import PILOT_SWEEP_INPUT
from skylize.app.autonomy.signals import (
    SIGNAL_KIND_AUDIT_WINDOW,
    harvest_activity_signal,
    signal_from_counts,
)
from skylize.dal.activity_signals import AuditWindowCounts

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _counts(**over: object) -> AuditWindowCounts:
    base: dict[str, object] = dict(
        org_id="org_a",
        since=NOW - timedelta(hours=1),
        until=NOW,
        total=10,
        by_result={"success": 6, "denied": 3, "escalated": 0, "failed": 1},
        distinct_action_types=4,
        distinct_agents=2,
    )
    base.update(over)
    return AuditWindowCounts(**base)  # type: ignore[arg-type]


def test_the_signal_carries_the_counted_window_not_a_descriptor() -> None:
    sig = signal_from_counts(_counts())
    assert sig["entity_id"] == "org_a"
    assert sig["signal_kind"] == SIGNAL_KIND_AUDIT_WINDOW
    f = sig["features"]
    assert f["total_actions"] == 10.0
    assert f["denied"] == 3.0
    assert f["failed"] == 1.0
    assert f["denial_rate"] == pytest.approx(0.3)
    assert f["window_seconds"] == 3600.0


def test_every_feature_is_a_float_because_the_schema_says_so() -> None:
    """`ActivitySignalIn.features` is `dict[str, float]`; an int would be coerced
    silently by pydantic, so the mismatch would only show up as a type error far
    from here."""
    for name, value in signal_from_counts(_counts()).items():
        if name == "features":
            assert all(isinstance(v, float) for v in value.values()), value


def test_an_empty_window_is_zeroes_and_never_a_divide_by_zero() -> None:
    """A quiet hour is a real observation. It must not raise and must not report
    a NaN rate on a governance record."""
    sig = signal_from_counts(
        _counts(total=0, by_result={}, distinct_action_types=0, distinct_agents=0)
    )
    assert sig["features"]["total_actions"] == 0.0
    assert sig["features"]["denial_rate"] == 0.0
    assert sig["features"]["failure_rate"] == 0.0


def test_the_harvested_kind_differs_from_the_fallback_descriptor() -> None:
    """The whole point of the two kinds: a reader of a governance record can tell
    counted evidence from a bare review request without guessing."""
    assert SIGNAL_KIND_AUDIT_WINDOW != PILOT_SWEEP_INPUT["signal_kind"]


async def test_no_source_falls_back_rather_than_inventing_numbers() -> None:
    assert await harvest_activity_signal(None, org_id="org_a", now=NOW) is None


async def test_a_failing_read_falls_back_instead_of_killing_the_sweep() -> None:
    """FAIL-OPEN on the INPUT. The agent stays FAIL_CLOSED about its verdict; a
    sweep that cannot read its window should still run and say so."""

    class Broken:
        async def read_window(self, **_: object) -> AuditWindowCounts:
            raise RuntimeError("database is down")

    assert await harvest_activity_signal(Broken(), org_id="org_a", now=NOW) is None


async def test_a_working_source_produces_the_counted_signal() -> None:
    class Source:
        async def read_window(
            self, *, org_id: str, since: datetime, until: datetime
        ) -> AuditWindowCounts:
            return _counts(org_id=org_id, since=since, until=until)

    sig = await harvest_activity_signal(Source(), org_id="org_b", now=NOW)
    assert sig is not None
    assert sig["entity_id"] == "org_b"
    assert sig["signal_kind"] == SIGNAL_KIND_AUDIT_WINDOW
    assert sig["features"]["denied"] == 3.0


def test_the_signal_validates_against_the_agents_declared_input_schema() -> None:
    """The contract names this schema; a payload the agent's own schema rejects
    would fail at execution, not here."""
    from skylize.schemas.agents.security import ActivitySignalIn

    parsed = ActivitySignalIn.model_validate(signal_from_counts(_counts()))
    assert parsed.entity_id == "org_a"
    assert parsed.features["denial_rate"] == pytest.approx(0.3)
