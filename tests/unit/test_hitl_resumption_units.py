"""The three small pieces the resumption mechanism rests on, proven in isolation.

The end-to-end behaviour lives in
`tests/integration/test_hitl_tool_turn_resumption_pg.py` against real Postgres.
What is here instead are the units whose *negative* cases are the interesting
ones, and which a live test would only ever exercise on the happy path.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from skylize.app.decision_engine.events import decision_id_for, hitl_id_for
from skylize.schemas.hitl import HitlResumptionPoint
from skylize.tools.base import ToolContext


def _turn(*ids: str) -> list[dict]:
    return [
        {"role": "user", "content": [{"kind": "text", "text": "do the thing"}]},
        {
            "role": "assistant",
            "content": [
                {"kind": "tool_use", "tool_use_id": i, "tool_name": "t", "tool_input": {}}
                for i in ids
            ],
        },
    ]


# ---------------------------------------------------------------------------
# The id discriminator
# ---------------------------------------------------------------------------

def test_no_discriminator_leaves_every_existing_id_byte_identical() -> None:
    """The compatibility guarantee. If this fails, ids already written to
    `decisions` and `hitl_queue` no longer round-trip and the whole approval
    chain silently points at rows that do not exist."""
    proposal_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    ns = hitl_id_for.__globals__["_DECISION_NS"]
    assert hitl_id_for(proposal_id) == uuid.uuid5(ns, f"hitl:{proposal_id}")
    assert decision_id_for(proposal_id) == uuid.uuid5(ns, f"decision:{proposal_id}")
    # Explicit None is the same as omitting it.
    assert hitl_id_for(proposal_id, discriminator=None) == hitl_id_for(proposal_id)


def test_discriminated_ids_are_distinct_per_suspension_and_from_each_other() -> None:
    proposal_id = uuid.uuid4()
    ids = {
        hitl_id_for(proposal_id),
        hitl_id_for(proposal_id, discriminator="turn:0"),
        hitl_id_for(proposal_id, discriminator="turn:1"),
        decision_id_for(proposal_id),
        decision_id_for(proposal_id, discriminator="turn:0"),
        decision_id_for(proposal_id, discriminator="turn:1"),
    }
    assert len(ids) == 6


# ---------------------------------------------------------------------------
# The snapshot's own integrity check
# ---------------------------------------------------------------------------

def test_a_snapshot_whose_ids_match_its_stored_turn_validates() -> None:
    point = HitlResumptionPoint(
        messages=_turn("toolu_a", "toolu_b"),
        pending_tool_use_ids=["toolu_a", "toolu_b"],
        iteration=2,
        tokens_used_so_far=1234,
    )
    assert point.pending_tool_use_ids == ["toolu_a", "toolu_b"]


@pytest.mark.parametrize(
    ("messages", "ids", "why"),
    [
        (_turn("toolu_a"), ["toolu_b"], "id does not name a block in the turn"),
        (_turn("toolu_a", "toolu_b"), ["toolu_a"], "a block would execute unreviewed"),
        (_turn("toolu_a", "toolu_b"), ["toolu_b", "toolu_a"], "dispatch order differs"),
    ],
)
def test_a_snapshot_that_disagrees_with_its_stored_turn_is_refused(
    messages: list[dict], ids: list[str], why: str
) -> None:
    """Each of these would let the resumed run dispatch something other than what
    the human approved, which is the one thing this mechanism exists to prevent."""
    with pytest.raises(ValidationError):
        HitlResumptionPoint(
            messages=messages, pending_tool_use_ids=ids,
            iteration=0, tokens_used_so_far=0,
        )


def test_a_snapshot_not_ending_on_the_assistant_turn_is_refused() -> None:
    messages = _turn("toolu_a")
    messages.append({"role": "user", "content": [{"kind": "text", "text": "x"}]})
    with pytest.raises(ValidationError):
        HitlResumptionPoint(
            messages=messages, pending_tool_use_ids=["toolu_a"],
            iteration=0, tokens_used_so_far=0,
        )


# ---------------------------------------------------------------------------
# replay_key: None everywhere except a resumed turn
# ---------------------------------------------------------------------------

def _ctx(**kw) -> ToolContext:
    base = dict(org_id="o", agent_id="a", correlation_id=uuid.uuid4())
    base.update(kw)
    return ToolContext(**base)  # type: ignore[arg-type]


def test_replay_key_is_none_on_every_path_but_a_resumed_turn() -> None:
    hitl_id = uuid.uuid4()
    # An ordinary run: no ticket, no block id, not a resumption.
    assert _ctx().replay_key() is None
    # A stage-2.5 approval: a ticket and a block id, but the block was FRESHLY
    # SAMPLED, so its id differs on the next retry and must not key a ledger row.
    assert _ctx(hitl_id=hitl_id, tool_use_id="toolu_a").replay_key() is None
    # A resumed run whose provider gave no block id.
    assert _ctx(hitl_id=hitl_id, is_hitl_resumption=True).replay_key() is None
    # A resumed turn with no ticket cannot happen, and is refused anyway.
    assert _ctx(tool_use_id="toolu_a", is_hitl_resumption=True).replay_key() is None


def test_replay_key_is_stable_per_call_and_distinct_across_calls() -> None:
    hitl_id, other_hitl = uuid.uuid4(), uuid.uuid4()

    def key(hid: uuid.UUID, tid: str) -> uuid.UUID | None:
        return _ctx(hitl_id=hid, tool_use_id=tid, is_hitl_resumption=True).replay_key()

    # Same approval, same block -> the SAME key on every retry. This is the
    # property a ledger keys on to avoid double-committing one approved action.
    assert key(hitl_id, "toolu_a") == key(hitl_id, "toolu_a")
    # Different blocks in one turn -> different keys, so the second call in a
    # multi-call turn is not swallowed as a duplicate of the first.
    assert key(hitl_id, "toolu_a") != key(hitl_id, "toolu_b")
    # Different approvals -> different keys, so a genuinely new decision executes.
    assert key(hitl_id, "toolu_a") != key(other_hitl, "toolu_a")
