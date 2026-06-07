"""Audit service: event mirror + append-only row + PII-safe hashing."""

from __future__ import annotations

from uuid import uuid4

from skylize.app.audit.service import AuditService, hash_payload
from skylize.dal.memory import InMemoryAuditRepository
from skylize.events.memory_bus import InMemoryEventBus


def test_hash_payload_is_deterministic_and_none_safe() -> None:
    assert hash_payload(None) is None
    a = hash_payload({"b": 1, "a": 2})
    b = hash_payload({"a": 2, "b": 1})  # key order independent
    assert a == b
    assert len(a) == 64  # sha-256 hex


async def test_record_writes_event_and_row_with_hashes() -> None:
    bus = InMemoryEventBus()
    repo = InMemoryAuditRepository()
    audit = AuditService(bus, repo)
    corr = uuid4()

    event_id = await audit.record(
        org_id="org_1", correlation_id=corr, action_type="orchestrator.run",
        result="success", source_agent_id="hook_generator_agent",
        authority_level="worker", inputs={"x": 1}, outputs={"y": 2},
    )

    mirrored = bus.published_of_type("audit.action_recorded")
    assert len(mirrored) == 1
    assert len(repo.rows) == 1
    row = repo.rows[0]
    assert row.event_id == event_id
    assert row.result == "success"
    assert row.inputs_hash and row.outputs_hash  # hashed, not raw
    assert row.inputs_hash != "1"
