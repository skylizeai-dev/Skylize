"""Property tests for the outbox relay's delivery guarantees.

These assert *properties* of the relay, not code paths:

  1. same-millisecond burst: N decisions minted in one millisecond all reach the
     stream (the headline defect — fails against the pre-fix explicit-id relay);
  2. no row is marked published without a verified stream entry;
  3. a crash between XADD and the published_at stamp loses nothing and, via
     event_id dedupe, delivers exactly once;
  4. regression guard: an XADD error is never reclassified as publish success.

The tests run the REAL ``OutboxPoller`` against ``FakeStreamRedis``, a faithful
in-memory model of the one Redis contract the relay depends on: XADD id
monotonicity. That contract is what the old client-minted-id scheme violated.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError

from skylize.decision_engine.outbox_poller import OutboxPoller

# Real Redis 7/8 server wording when an explicit XADD id is <= the stream top.
REAL_MONOTONE_MSG = (
    "The ID specified in XADD is equal or smaller than the target stream top item"
)


class FakeStreamRedis:
    """Faithful in-memory model of the Redis Streams XADD id contract.

    Only the id semantics the relay depends on are modeled:
      * explicit id <= current stream top  -> ResponseError (real server wording)
      * explicit id  > current stream top  -> appended with that id
      * id == '*' (default)                -> server assigns a strictly-increasing
                                              id; always succeeds

    That is exactly enough to reproduce the same-millisecond collision that made
    the old explicit-id relay drop events, and to show the '*' relay cannot.
    ``down_streams`` simulates a stream whose XADD errors (e.g. Redis outage), so
    the "published implies on-stream" invariant can be tested against a failure.
    """

    def __init__(self, down_streams: tuple[str, ...] = ()) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self._down = set(down_streams)

    @staticmethod
    def _parse(sid: str) -> tuple[int, int]:
        ms, _, seq = sid.partition("-")
        return int(ms), (int(seq) if seq else 0)

    def _top(self, name: str) -> tuple[int, int]:
        entries = self.streams[name]
        return self._parse(entries[-1][0]) if entries else (0, 0)

    async def xadd(self, name, fields, id="*", **kwargs) -> str:  # noqa: A002 - redis-py kwarg name
        if name in self._down:
            raise ResponseError("stream unavailable")
        top = self._top(name)
        if id == "*" or id is None:
            new = (top[0], top[1] + 1) if self.streams[name] else (1, 0)
        else:
            cand = self._parse(id)
            if cand <= top:
                # Faithful to real Redis: the later same-ms row is REJECTED.
                raise ResponseError(REAL_MONOTONE_MSG)
            new = cand
        new_id = f"{new[0]}-{new[1]}"
        self.streams[name].append((new_id, dict(fields)))
        return new_id

    def entries(self, name: str) -> list[tuple[str, dict]]:
        return list(self.streams[name])


def _event_id_of(fields: dict) -> str:
    """Read event_id off a relayed stream entry.

    The relay emits the CANONICAL bus envelope — a single ``event`` field holding
    the whole envelope JSON (wire parity with the inline engine), not flattened
    top-level fields. ``event_id`` therefore lives INSIDE that envelope, exactly
    where RedisEventBus._decode and consumer-side dedupe read it. These delivery-
    property tests assert on ``event_id``, so they resolve it the same way a real
    consumer would rather than reaching for a flat field that no longer exists.
    """
    return str(json.loads(fields["event"])["event_id"])


def _poller(redis, rows, *, batch_size: int = 500, max_retry_count: int = 3):
    """Wire a real OutboxPoller to ``redis`` and a conn whose fetch yields ``rows``.

    Returns (poller, conn). ``conn.execute`` records every UPDATE so the tests can
    read back which rows were marked published / retried / failed.
    """
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock(return_value=None)

    db = MagicMock()

    @asynccontextmanager
    async def _admin_session():
        yield conn

    db.admin_session = _admin_session

    poller = OutboxPoller(
        db=db,
        redis=redis,
        settings=MagicMock(),
        poll_interval_seconds=0.01,
        batch_size=batch_size,
        max_retry_count=max_retry_count,
    )
    return poller, conn


def _row(*, db_id: int, event_id: str, stream_key: str, outbox_row_id: str):
    return {
        "outbox_row_id": outbox_row_id,
        "stream_key": stream_key,
        "tenant_id": stream_key.split(":")[1],
        "id": db_id,
        "payload": json.dumps({"event_id": event_id, "event_type": "decision.approved"}),
        "event_type": "decision.approved",
        "retry_count": 0,
    }


def _published_db_ids(conn) -> set:
    return {
        c.args[2]
        for c in conn.execute.call_args_list
        if "published_at" in c.args[0]
    }


# ---------------------------------------------------------------------------
# 1. HEADLINE: a same-millisecond burst of N decisions all reach the stream.
#    Fails on the pre-fix explicit-id relay (later same-ms rows collide and are
#    dropped); passes on the '*' relay (Redis assigns monotonic ids).
# ---------------------------------------------------------------------------

async def test_same_millisecond_burst_all_arrive_on_stream():
    N = 200
    ms = 1_700_000_000_000
    stream_key = "evt:tenant-a:decision"

    # All N rows minted in the SAME millisecond, with the publisher's actual
    # scheme (a non-monotonic 4-digit sequence). Here the sequences DESCEND with
    # created_at, so the explicit-id relay would reject every row after the first
    # — the worst case, and deterministic. Under '*' the sequence is irrelevant.
    event_ids = [str(uuid.uuid4()) for _ in range(N)]
    rows = [
        _row(
            db_id=i,
            event_id=event_ids[i],
            stream_key=stream_key,
            outbox_row_id=f"{ms}-{(N - i):04d}",
        )
        for i in range(N)
    ]

    redis = FakeStreamRedis()
    poller, conn = _poller(redis, rows)

    await poller._poll_and_publish()

    arrived = redis.entries(stream_key)
    # Property: no loss — all N events are on the stream.
    assert len(arrived) == N, f"expected {N} events on stream, got {len(arrived)}"
    assert {_event_id_of(f) for _, f in arrived} == set(event_ids)
    # And every one was marked published (published implies on-stream, see test 2).
    assert _published_db_ids(conn) == {r["id"] for r in rows}


# ---------------------------------------------------------------------------
# 2. No row is ever marked published without a verified stream entry.
#    One tenant's stream is down: its row must NOT be marked published while the
#    healthy rows are. Marked-published set == exactly the set on some stream.
# ---------------------------------------------------------------------------

async def test_no_row_marked_published_without_stream_entry():
    down_key = "evt:down:decision"
    ok_key = "evt:ok:decision"
    rows = [
        _row(db_id=1, event_id="e1", stream_key=ok_key, outbox_row_id="100-0001"),
        _row(db_id=2, event_id="e2", stream_key=down_key, outbox_row_id="100-0002"),
        _row(db_id=3, event_id="e3", stream_key=ok_key, outbox_row_id="100-0003"),
    ]

    redis = FakeStreamRedis(down_streams=(down_key,))
    poller, conn = _poller(redis, rows)

    await poller._poll_and_publish()

    on_stream_event_ids = {
        _event_id_of(f) for key in (ok_key, down_key) for _, f in redis.entries(key)
    }
    published = _published_db_ids(conn)

    # The down row reached no stream and must not be published.
    assert "e2" not in on_stream_event_ids
    assert 2 not in published
    # The healthy rows are both on the stream and both published.
    assert on_stream_event_ids == {"e1", "e3"}
    assert published == {1, 3}
    # Invariant, stated directly: every published row has a stream entry.
    id_to_event = {r["id"]: json.loads(r["payload"])["event_id"] for r in rows}
    for db_id in published:
        assert id_to_event[db_id] in on_stream_event_ids


# ---------------------------------------------------------------------------
# 3. Crash between XADD and the published_at stamp: no loss, and event_id dedupe
#    collapses the at-least-once re-relay to exactly one delivery.
# ---------------------------------------------------------------------------

async def test_crash_mid_publish_is_at_least_once_and_dedupe_collapses():
    stream_key = "evt:t:decision"
    event_id = str(uuid.uuid4())
    row = _row(db_id=1, event_id=event_id, stream_key=stream_key, outbox_row_id="100-0001")

    redis = FakeStreamRedis()
    poller, conn = _poller(redis, [row])

    # Simulate a crash AFTER XADD succeeds but BEFORE published_at commits, then
    # recovery: the first _mark_published raises, the second succeeds.
    real_mark = poller._mark_published
    calls = {"n": 0}

    async def flaky_mark(db_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after XADD, before published_at commit")
        await real_mark(db_id)

    poller._mark_published = flaky_mark  # type: ignore[method-assign]

    # Pass 1 — crashes during the stamp. The row stays unpublished.
    with pytest.raises(RuntimeError):
        await poller._poll_and_publish()
    assert _published_db_ids(conn) == set(), "row must not be published if the stamp failed"
    assert len(redis.entries(stream_key)) == 1, "event is on the stream — no loss"

    # Pass 2 — recovery re-relays the still-unpublished row and stamps it.
    await poller._poll_and_publish()
    assert 1 in _published_db_ids(conn)

    entries = redis.entries(stream_key)
    event_ids_on_stream = [_event_id_of(f) for _, f in entries]
    # At-least-once at the relay: a physical duplicate exists after recovery...
    assert len(entries) == 2
    # ...but every entry carries the SAME event_id, so the bus's consumer-side
    # dedupe (events/router.py:78 — `if event_id in self._seen`) yields exactly
    # one logical delivery: neither loss nor duplicate as observed downstream.
    assert set(event_ids_on_stream) == {event_id}

    seen: set[str] = set()
    delivered = 0
    for eid in event_ids_on_stream:  # mirrors EventRouter._dispatch dedupe
        if eid in seen:
            continue
        seen.add(eid)
        delivered += 1
    assert delivered == 1, "event_id dedupe must collapse the re-relay to one delivery"


# ---------------------------------------------------------------------------
# 4. Regression guard: reintroducing the error-misclassification fails the suite.
#    An XADD ResponseError — including the exact monotone-id wording the deleted
#    branch keyed on — must NEVER mark a row published.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "err_message",
    [
        "ID specified is equal to or smaller than the target ID",  # old constant
        REAL_MONOTONE_MSG,                                          # real Redis
        "some unrelated redis error",
    ],
)
async def test_xadd_error_is_never_reclassified_as_published(err_message):
    stream_key = "evt:t:decision"
    row = _row(db_id=1, event_id="e1", stream_key=stream_key, outbox_row_id="100-0001")

    redis = MagicMock()
    redis.xadd = AsyncMock(side_effect=ResponseError(err_message))
    poller, conn = _poller(redis, [row])

    await poller._poll_and_publish()

    for c in conn.execute.call_args_list:
        assert "published_at" not in c.args[0], (
            "an errored XADD must never mark the row published — that was the defect"
        )
    # It is retried instead (retry_count incremented, below max → not failed yet).
    assert any("retry_count" in c.args[0] for c in conn.execute.call_args_list)
