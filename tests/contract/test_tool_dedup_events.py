"""
Contract gate for the tool-dedup + convergence events.

Decision (closed taxonomy, event_driven_architecture.md §5): there is no `tool`
event category. `tool.dedup_skipped` is recorded as `audit.action_recorded` with
`action_type="tool.dedup_skipped"`; `governance.convergence_failure` is recorded
as `audit.action_recorded` (the audit mirror) alongside a
`governance.circuit_breaker_tripped` with `trip_reason="convergence"`. This test
proves those event shapes are registered, round-trip through the registry, and
that redelivering a dedup-skipped audit by `event_id` does not double-audit.
"""

from __future__ import annotations

from uuid import uuid4

from skylize.schemas.events import EVENT_REGISTRY
from skylize.schemas.events.audit import AuditActionRecorded
from skylize.schemas.events.governance import GovernanceCircuitBreakerTripped

ORG = "org_test"


def _round_trip(event):
    """Serialize an event and re-resolve it via the registry, asserting identity."""
    model = EVENT_REGISTRY[event.type]
    restored = model.model_validate_json(event.model_dump_json())
    assert restored == event
    return restored


# ---------------------------------------------------------------------------
# Registry membership
# ---------------------------------------------------------------------------

def test_reused_event_types_are_registered() -> None:
    # Dedup + convergence reuse existing, registered types — no new taxonomy.
    assert "audit.action_recorded" in EVENT_REGISTRY
    assert "governance.circuit_breaker_tripped" in EVENT_REGISTRY


def test_no_phantom_tool_category_was_added() -> None:
    # Guard the closed taxonomy: nothing named tool.* leaked into the registry.
    assert not any(t.startswith("tool.") for t in EVENT_REGISTRY)


# ---------------------------------------------------------------------------
# tool.dedup_skipped  (as an audit action)
# ---------------------------------------------------------------------------

def test_dedup_skipped_audit_event_round_trips() -> None:
    event = AuditActionRecorded(
        tenant_id=ORG,
        partition_key="agent:hook_generator_agent",
        department="audit",
        source_agent_id="hook_generator_agent",
        correlation_id=uuid4(),
        payload=AuditActionRecorded.Payload(
            action_type="tool.dedup_skipped",
            inputs_hash="deadbeef",  # the exec fingerprint
            result="success",
            result_reason="served cached result; dispatch suppressed",
        ),
    )
    restored = _round_trip(event)
    assert restored.payload.action_type == "tool.dedup_skipped"
    assert restored.category.value == "audit"


# ---------------------------------------------------------------------------
# governance.convergence_failure  (audit action + breaker event)
# ---------------------------------------------------------------------------

def test_convergence_failure_audit_event_round_trips() -> None:
    event = AuditActionRecorded(
        tenant_id=ORG,
        partition_key="agent:hook_generator_agent",
        department="audit",
        source_agent_id="hook_generator_agent",
        correlation_id=uuid4(),
        payload=AuditActionRecorded.Payload(
            action_type="governance.convergence_failure",
            result="escalated",
            result_reason="escalation_path=['cmo', 'ceo', 'human_owner']",
        ),
    )
    restored = _round_trip(event)
    assert restored.payload.action_type == "governance.convergence_failure"
    assert restored.payload.result == "escalated"


def test_convergence_breaker_event_carries_convergence_reason() -> None:
    event = GovernanceCircuitBreakerTripped(
        tenant_id=ORG,
        partition_key="agent:hook_generator_agent",
        department="governance",
        source_agent_id="hook_generator_agent",
        correlation_id=uuid4(),
        payload=GovernanceCircuitBreakerTripped.Payload(
            agent_id="hook_generator_agent",
            trip_reason="convergence: repeated action abc123 in llm.generate",
            trip_count=2,
        ),
    )
    restored = _round_trip(event)
    assert restored.payload.trip_reason.startswith("convergence")


# ---------------------------------------------------------------------------
# Idempotency on event_id (at-least-once redelivery)
# ---------------------------------------------------------------------------

def test_redelivered_dedup_audit_does_not_double_audit() -> None:
    """An idempotent consumer keyed on event_id processes a redelivery once."""
    event = AuditActionRecorded(
        tenant_id=ORG,
        partition_key="agent:hook_generator_agent",
        department="audit",
        correlation_id=uuid4(),
        payload=AuditActionRecorded.Payload(
            action_type="tool.dedup_skipped", result="success",
        ),
    )

    seen: set = set()
    applied = 0

    def consume(e) -> None:
        nonlocal applied
        if e.event_id in seen:  # at-least-once delivery → guard on event_id
            return
        seen.add(e.event_id)
        applied += 1

    # Same event delivered twice (its event_id is stable across redelivery).
    redelivered = AuditActionRecorded.model_validate_json(event.model_dump_json())
    assert redelivered.event_id == event.event_id
    consume(event)
    consume(redelivered)

    assert applied == 1  # processed exactly once despite two deliveries
