"""Wire-parity tests: the outbox relay emits the CANONICAL bus envelope.

These prove the fix for the wire break where OutboxPoller XADD'd flattened stream
fields (plus a synthetic ``event_type``) that ``RedisEventBus._decode`` cannot read:
the decoder reads a single ``event`` field holding the whole envelope JSON, so every
OPA-relayed event decoded to ``None`` and was DLQ'd. The relay now emits exactly what
``RedisEventBus.publish`` emits for the inline engine.

The properties asserted (per the fix brief):
  * round-trip: an OPA decision relayed through the outbox is decoded by
    ``RedisEventBus._decode`` into an object FIELD-BY-FIELD equivalent to the source;
  * parity: an inline-published event and an OPA-relayed event of the same logical
    type decode to the SAME shape (same stream fields, same decoded model);
  * event_id survives the round trip intact;
  * regression guard: reintroducing a flattened field fails decode.

``RedisEventBus._decode`` is a ``@staticmethod`` and needs no Redis, so these run as
unit tests. The relay's real ``_publish_row`` is exercised with a mocked redis whose
``xadd`` captures the exact fields that would go on the wire — so we test the shipped
encode path, not a re-implementation of it.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from skylize.decision_engine.outbox_poller import _ENVELOPE_FIELD, OutboxPoller
from skylize.events.redis_adapter import RedisEventBus
from skylize.schemas.events.decision import (
    DecisionApproved,
    DecisionDeferredToHuman,
    DecisionRejected,
)

# Fixed ids so equivalence assertions are exact, never time/uuid-dependent.
_CORR = UUID("11111111-1111-1111-1111-111111111111")
_DEC = UUID("22222222-2222-2222-2222-222222222222")
_EVENT = UUID("33333333-3333-3333-3333-333333333333")
_HITL = UUID("44444444-4444-4444-4444-444444444444")


def _approved() -> DecisionApproved:
    """A canonical DecisionApproved envelope, built exactly as the publisher does."""
    return DecisionApproved(
        event_id=_EVENT,
        tenant_id="tenant-a",
        partition_key="dec-1",
        department="decision",
        correlation_id=_CORR,
        payload=DecisionApproved.Payload(
            decision_id=_DEC,
            action_kind="launch_campaign",
            approved_scope={"action_kind": "launch_campaign"},
        ),
    )


def _rejected() -> DecisionRejected:
    return DecisionRejected(
        event_id=_EVENT,
        tenant_id="tenant-a",
        partition_key="dec-1",
        department="decision",
        correlation_id=_CORR,
        payload=DecisionRejected.Payload(
            decision_id=_DEC,
            action_kind="launch_campaign",
            stage_rejected_at="authority",
            reasons=["over budget"],
            policy_version=None,
        ),
    )


def _deferred() -> DecisionDeferredToHuman:
    return DecisionDeferredToHuman(
        event_id=_EVENT,
        tenant_id="tenant-a",
        partition_key="dec-1",
        department="decision",
        correlation_id=_CORR,
        payload=DecisionDeferredToHuman.Payload(
            decision_id=_DEC,
            hitl_id=_HITL,
            trigger_reason="low_confidence",
            routed_to="hitl_queue",
        ),
    )


def _outbox_row(event, *, db_id: int = 1) -> dict:
    """A decision_outbox row as the publisher writes it: the full canonical envelope
    stored (as a JSON string, the JSONB round-trip) in the ``payload`` column."""
    return {
        "outbox_row_id": "1700000000000-0001",
        "stream_key": f"evt:{event.tenant_id}:{event.department}",
        "tenant_id": event.tenant_id,
        "id": db_id,
        "payload": json.dumps(event.model_dump(mode="json"), default=str),
        "event_type": event.type,
        "retry_count": 0,
    }


def _poller_capturing_xadd() -> tuple[OutboxPoller, AsyncMock]:
    """A poller wired to a mock redis; returns (poller, redis) so the test can read
    ``redis.xadd.call_args`` — the exact fields the relay puts on the wire."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1700000000000-5")

    db = MagicMock()

    @asynccontextmanager
    async def _admin_session():
        yield conn

    db.admin_session = _admin_session
    poller = OutboxPoller(
        db=db, redis=redis, settings=MagicMock(),
        poll_interval_seconds=0.01, batch_size=50, max_retry_count=3,
    )
    return poller, redis


async def _relayed_fields(event) -> dict[str, str]:
    """Run the SHIPPED relay path for ``event`` and return the stream fields XADD'd."""
    poller, redis = _poller_capturing_xadd()
    await poller._publish_row(_outbox_row(event))
    redis.xadd.assert_awaited_once()
    stream_key, fields = redis.xadd.call_args.args
    assert stream_key == f"evt:{event.tenant_id}:{event.department}"
    return fields


# ---------------------------------------------------------------------------
# Round-trip: relay -> _decode reproduces the source event field-by-field.
# ---------------------------------------------------------------------------

async def test_round_trip_field_by_field_equivalence():
    for build in (_approved, _rejected, _deferred):
        source = build()
        fields = await _relayed_fields(source)

        decoded = RedisEventBus._decode(fields)
        assert decoded is not None, f"{source.type} did not decode — wire break"

        # Field-by-field equivalence, not merely "decode succeeded". model_dump is the
        # full envelope (identity, routing, provenance, timing, payload) — comparing
        # the dumps compares every field including the nested payload.
        assert decoded.model_dump(mode="json") == source.model_dump(mode="json"), (
            f"{source.type} did not round-trip to an equivalent object"
        )
        # And the decoded object is the correct, registry-resolved concrete type.
        assert type(decoded) is type(source)


# ---------------------------------------------------------------------------
# Parity: an inline-published event and an OPA-relayed one of the same logical
# type produce the SAME stream fields and decode to the SAME shape.
# ---------------------------------------------------------------------------

async def test_parity_inline_vs_relayed_same_shape():
    event = _approved()

    # Inline path: RedisEventBus.publish writes {_FIELD: event.model_dump_json()}.
    # Reproduce that field dict exactly (publish() itself needs a live redis).
    inline_fields = {_ENVELOPE_FIELD: event.model_dump_json()}

    # OPA path: the relay's actual output.
    relayed_fields = await _relayed_fields(event)

    # Same field name and same single field — no flattening, no synthetic event_type.
    assert set(relayed_fields) == set(inline_fields) == {_ENVELOPE_FIELD}

    # The two envelope JSON strings parse to the same object (key order is not part
    # of the contract; the decoded model is).
    assert json.loads(relayed_fields[_ENVELOPE_FIELD]) == json.loads(
        inline_fields[_ENVELOPE_FIELD]
    )

    inline_decoded = RedisEventBus._decode(inline_fields)
    relayed_decoded = RedisEventBus._decode(relayed_fields)
    assert inline_decoded is not None and relayed_decoded is not None
    assert inline_decoded.model_dump(mode="json") == relayed_decoded.model_dump(
        mode="json"
    )


# ---------------------------------------------------------------------------
# The relay names the wire field exactly what the decoder reads. If either side's
# constant drifts, parity silently breaks — pin them together.
# ---------------------------------------------------------------------------

def test_relay_field_name_matches_decoder_contract():
    from skylize.events import redis_adapter

    assert _ENVELOPE_FIELD == redis_adapter._FIELD == "event"


# ---------------------------------------------------------------------------
# event_id survives the round trip intact.
# ---------------------------------------------------------------------------

async def test_event_id_survives_round_trip():
    source = _approved()
    fields = await _relayed_fields(source)

    decoded = RedisEventBus._decode(fields)
    assert decoded is not None
    # event_id is load-bearing: ADR-referenced at-least-once delivery makes consumers
    # dedupe on it, and the server-generated stream id delegated duplicate protection
    # entirely to consumer idempotency. It must arrive unchanged.
    assert decoded.event_id == source.event_id == _EVENT


# ---------------------------------------------------------------------------
# Regression guard: reintroducing a flattened field breaks decode. This is the
# exact pre-fix shape — top-level envelope keys as separate stream fields, no
# ``event`` field — and it MUST fail to decode, or the fork is back.
# ---------------------------------------------------------------------------

def test_flattened_fields_fail_to_decode_regression_guard():
    source = _approved()
    envelope = source.model_dump(mode="json")

    # The old encode: flatten every top-level key into its own stream field and add
    # a synthetic event_type. No ``event`` field at all.
    flattened = {k: str(v) for k, v in envelope.items()}
    flattened["event_type"] = source.type

    assert _ENVELOPE_FIELD not in flattened, (
        "test setup wrong: a flattened row must NOT carry the canonical field"
    )
    assert RedisEventBus._decode(flattened) is None, (
        "a flattened row decoded successfully — the wire fork has been reintroduced"
    )


def test_extra_field_alongside_envelope_fails_to_decode():
    """Even WITH the canonical ``event`` field, a stray sibling field (e.g. the old
    ``event_type``) does not corrupt decode of the envelope itself — the decoder reads
    only ``event`` — but a flattened envelope key leaking INTO the JSON would, because
    the envelope is extra='forbid'. Prove the forbid wall holds."""
    source = _approved()
    envelope = source.model_dump(mode="json")
    envelope["event_type"] = source.type  # a field with no home in BaseEvent

    poisoned = {_ENVELOPE_FIELD: json.dumps(envelope)}
    assert RedisEventBus._decode(poisoned) is None, (
        "an envelope carrying an extra top-level key must be rejected (extra=forbid)"
    )
