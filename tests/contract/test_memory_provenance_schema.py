"""
Contract tests for memory provenance: the schemas, the events, and the
dedup-aware write service behavior.

Covers:
  - ProvenanceEntry / MemoryWriteOutcome / MemoryWriteRequest validate + freeze.
  - memory.fact_recorded / memory.fact_reinforced are registered and round-trip.
  - First write -> MISS -> one row, one provenance entry, fact_recorded emitted.
  - Identical re-write -> HIT -> same row, two provenance entries, reinforced.
  - Concurrent identical writes -> one row, two provenance entries (collapse).
  - Different namespace with identical content -> distinct facts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from skylize.dal.ports import MemoryWritePort
from skylize.events.memory_bus import InMemoryEventBus
from skylize.memory.dedup import compute_fact_hash
from skylize.memory.repository import MemoryWriteService
from skylize.schemas.events import EVENT_REGISTRY
from skylize.schemas.events.memory import MemoryFactRecorded, MemoryFactReinforced
from skylize.schemas.memory import (
    MemoryWriteOutcome,
    MemoryWriteRequest,
    ProvenanceEntry,
)


# ---------------------------------------------------------------------------
# In-memory MemoryWritePort fake — models the real ON CONFLICT collapse.
# Keyed by (org_id, namespace, fact_hash); concurrent identical writes serialize
# through the per-key lock so two writers collapse to one row with two entries.
# ---------------------------------------------------------------------------

class FakeMemoryRepo(MemoryWritePort):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], dict] = {}
        self._lock = asyncio.Lock()

    async def upsert_fact(
        self,
        *,
        org_id: str,
        namespace: str,
        tier: str,
        fact_hash: str,
        content_text: str,
        provenance_entry: ProvenanceEntry,
        created_by_agent: str,
        half_life_seconds: float,
        reinforcement: float,
    ) -> MemoryWriteOutcome:
        key = (org_id, namespace, fact_hash)
        entry = provenance_entry.model_dump(mode="json")
        async with self._lock:
            row = self._rows.get(key)
            if row is None:
                row = {
                    "record_id": uuid4(),
                    "provenance": [entry],
                    "importance_score": reinforcement,
                }
                self._rows[key] = row
                created = True
            else:
                row["provenance"].append(entry)
                row["importance_score"] += reinforcement  # decay irrelevant here
                created = False
            return MemoryWriteOutcome(
                record_id=row["record_id"],
                fact_hash=fact_hash,
                namespace=namespace,
                created=created,
                provenance_count=len(row["provenance"]),
                importance_score=row["importance_score"],
            )

    async def get_fact(self, *, org_id, namespace, fact_hash):  # noqa: ANN001
        return self._rows.get((org_id, namespace, fact_hash))


def _request(content: str = "Spring campaign launched on Tuesday", **over) -> MemoryWriteRequest:
    base = dict(
        org_id="org_1",
        namespace="creative",
        tier="semantic",
        content_text=content,
        agent_id="hook_generator_agent",
        source_event_id=uuid4(),
        correlation_id=uuid4(),
    )
    base.update(over)
    return MemoryWriteRequest(**base)


# ---- schema contracts ------------------------------------------------------

def test_provenance_entry_is_frozen_and_strict() -> None:
    e = ProvenanceEntry(
        event_id=uuid4(),
        agent_id="a",
        ts=datetime.now(timezone.utc),
        source_correlation_id=uuid4(),
    )
    with pytest.raises(ValidationError):
        ProvenanceEntry(event_id=uuid4(), agent_id="a", ts=datetime.now(timezone.utc),
                        source_correlation_id=uuid4(), extra="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        e.agent_id = "b"  # type: ignore[misc]  # frozen model rejects mutation


def test_write_outcome_round_trips() -> None:
    o = MemoryWriteOutcome(
        record_id=uuid4(), fact_hash="0" * 64, namespace="creative",
        created=True, provenance_count=1, importance_score=1.0,
    )
    assert MemoryWriteOutcome.model_validate_json(o.model_dump_json()) == o


def test_write_request_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        _request(content="")


# ---- event registration ----------------------------------------------------

def test_memory_fact_events_registered() -> None:
    assert EVENT_REGISTRY["memory.fact_recorded"] is MemoryFactRecorded
    assert EVENT_REGISTRY["memory.fact_reinforced"] is MemoryFactReinforced


def test_fact_recorded_round_trips() -> None:
    ev = MemoryFactRecorded(
        tenant_id="org_1", partition_key="memory:creative:abc", department="memory",
        correlation_id=uuid4(),
        payload=MemoryFactRecorded.Payload(
            fact_id=uuid4(), fact_hash="a" * 64, namespace="creative",
            tier="semantic", agent_id="hook_generator_agent",
        ),
    )
    assert MemoryFactRecorded.model_validate_json(ev.model_dump_json()) == ev


# ---- write service behavior ------------------------------------------------

async def test_first_write_is_miss_and_emits_fact_recorded() -> None:
    bus, repo = InMemoryEventBus(), FakeMemoryRepo()
    svc = MemoryWriteService(repo, bus)
    req = _request()

    outcome = await svc.write(req)

    assert outcome.created is True
    assert outcome.provenance_count == 1
    assert outcome.fact_hash == compute_fact_hash(req.namespace, req.content_text)
    assert len(bus.published_of_type("memory.fact_recorded")) == 1
    assert len(bus.published_of_type("memory.fact_reinforced")) == 0
    # state-changing action emits an audit mirror with the correlation_id
    audits = bus.published_of_type("audit.action_recorded")
    assert len(audits) == 1
    assert audits[0].correlation_id == req.correlation_id


async def test_identical_rewrite_is_hit_and_appends_provenance() -> None:
    bus, repo = InMemoryEventBus(), FakeMemoryRepo()
    svc = MemoryWriteService(repo, bus)

    first = await svc.write(_request())
    second = await svc.write(_request())  # same content, new event/correlation ids

    assert first.record_id == second.record_id  # collapsed to one row
    assert second.created is False
    assert second.provenance_count == 2
    assert len(bus.published_of_type("memory.fact_recorded")) == 1
    assert len(bus.published_of_type("memory.fact_reinforced")) == 1


async def test_concurrent_identical_writes_collapse_to_one_row_two_entries() -> None:
    bus, repo = InMemoryEventBus(), FakeMemoryRepo()
    svc = MemoryWriteService(repo, bus)

    r1, r2 = await asyncio.gather(svc.write(_request()), svc.write(_request()))

    assert r1.record_id == r2.record_id  # one row
    created_flags = sorted([r1.created, r2.created])
    assert created_flags == [False, True]  # exactly one INSERT, one reinforce
    final = max((r1, r2), key=lambda o: o.provenance_count)
    assert final.provenance_count == 2  # both provenance entries retained


async def test_same_content_different_namespace_is_distinct_fact() -> None:
    bus, repo = InMemoryEventBus(), FakeMemoryRepo()
    svc = MemoryWriteService(repo, bus)

    a = await svc.write(_request(namespace="creative"))
    b = await svc.write(_request(namespace="sales"))

    assert a.record_id != b.record_id
    assert a.fact_hash != b.fact_hash
    assert len(bus.published_of_type("memory.fact_recorded")) == 2
