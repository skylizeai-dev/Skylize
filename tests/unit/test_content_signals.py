"""The signal the content review submits: a deliverable in, `BrandCheckIn` out.

These pin the MAPPING and the SKIP, which are what reach a governance record and
what stop a run happening at all. The SQL that finds the un-reviewed row is
exercised against real Postgres in
tests/integration/test_content_signal_source_pg.py -- "has this already been
checked" is a join, and a fake repository cannot prove a join.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from skylize.app.autonomy.pilot import CONTENT_REVIEW_AGENT_ID, fallback_input_for
from skylize.app.autonomy.signals import (
    CONTENT_KIND_BY_PRODUCER,
    CONTENT_LOOKBACK,
    content_signal_from_row,
    harvest_content_signal,
    harvest_for,
)
from skylize.contracts.mvp import ALL_MVP_CONTRACTS
from skylize.contracts.registry import resolve_model
from skylize.dal.ports import UncheckedContentRow

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
DELIVERABLE_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _row(**over: object) -> UncheckedContentRow:
    base: dict[str, object] = dict(
        deliverable_id=DELIVERABLE_ID,
        org_id="org_a",
        producing_agent_id="ad_copy_agent",
        deliverable_type="ad_creative",
        title="Ad Copy Output",
        content_markdown="# Variants\n\n- Buy now, pay never.\n",
        created_at=NOW - timedelta(minutes=30),
        backlog=1,
    )
    base.update(over)
    return UncheckedContentRow(**base)  # type: ignore[arg-type]


class _Source:
    """Records the arguments it was called with, so the WINDOW can be asserted."""

    def __init__(self, result: UncheckedContentRow | None) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def read_oldest_unchecked(self, **kwargs: object) -> UncheckedContentRow | None:
        self.calls.append(kwargs)
        return self._result


class _Exploding:
    async def read_oldest_unchecked(self, **_: object) -> UncheckedContentRow | None:
        raise RuntimeError("deliverables unreachable")


# --------------------------------------------------------------------------- #
# The mapping
# --------------------------------------------------------------------------- #

def test_the_signal_is_the_real_deliverables_own_id_and_text() -> None:
    """Nothing is invented: both values come off the row.

    `brief_id` being the deliverable's OWN id is what makes the verdict joinable
    back to the thing it judged -- and is the same id the DAL reads back to know
    this deliverable has been handled.
    """
    signal = content_signal_from_row(_row())
    assert signal["brief_id"] == str(DELIVERABLE_ID)
    assert signal["content"] == "# Variants\n\n- Buy now, pay never.\n"
    assert signal["content_kind"] == "copy"


def test_it_validates_against_the_agents_declared_input_schema() -> None:
    """The mapping is checked against the CONTRACT, not against this test's idea
    of the shape. `BrandCheckIn` forbids extra fields, so an added key fails
    here rather than at 03:00 on a cadence."""
    contract = next(
        c for c in ALL_MVP_CONTRACTS if c.agent_id == CONTENT_REVIEW_AGENT_ID
    )
    model = resolve_model(contract.input_schema)
    parsed = model.model_validate(content_signal_from_row(_row()))
    assert str(parsed.brief_id) == str(DELIVERABLE_ID)
    assert parsed.content_kind == "copy"


@pytest.mark.parametrize(
    ("producer", "kind"),
    [
        ("hook_generator_agent", "hook"),
        ("ad_copy_agent", "copy"),
        ("caption_writer_agent", "caption"),
        ("script_writer_agent", "script"),
    ],
)
def test_content_kind_comes_from_the_producing_agent(producer: str, kind: str) -> None:
    signal = content_signal_from_row(_row(producing_agent_id=producer))
    assert signal["content_kind"] == kind


def test_the_producer_map_covers_exactly_the_documented_kinds() -> None:
    """`BrandCheckIn.content_kind` is documented 'hook'|'copy'|'caption'|'script'.

    Pinned so a future entry cannot quietly introduce a fifth vocabulary value
    that the agent's own prompt never mentions.
    """
    assert set(CONTENT_KIND_BY_PRODUCER.values()) == {
        "hook", "copy", "caption", "script",
    }


def test_near_miss_producers_are_excluded_rather_than_approximated() -> None:
    """`cta_optimizer_agent` and `copy_director` also emit copy.

    Calling their output 'copy' would be a defensible guess -- and a guess
    stamped onto a governance record. They are left out on purpose; this test is
    what makes that a decision rather than an oversight.
    """
    for near_miss in ("cta_optimizer_agent", "copy_director", "brand_guardian_agent"):
        assert near_miss not in CONTENT_KIND_BY_PRODUCER


def test_every_mapped_producer_is_a_real_registered_agent() -> None:
    """A typo in the map would silently select nothing forever."""
    known = {c.agent_id for c in ALL_MVP_CONTRACTS}
    assert set(CONTENT_KIND_BY_PRODUCER) <= known


# --------------------------------------------------------------------------- #
# The harvest, and the skip
# --------------------------------------------------------------------------- #

async def test_it_asks_only_for_the_mapped_producers_and_the_lookback() -> None:
    source = _Source(_row())
    await harvest_content_signal(
        source, org_id="org_a", reviewer_agent_id=CONTENT_REVIEW_AGENT_ID, now=NOW
    )
    call = source.calls[0]
    assert call["org_id"] == "org_a"
    assert sorted(call["producer_agent_ids"]) == sorted(CONTENT_KIND_BY_PRODUCER)
    # The reviewer excludes ITS OWN past verdicts, which is what stops the sweep
    # feeding the agent its own output.
    assert call["reviewer_agent_id"] == CONTENT_REVIEW_AGENT_ID
    assert call["until"] == NOW
    assert call["since"] == NOW - CONTENT_LOOKBACK


async def test_the_lookback_is_longer_than_the_cadence_so_a_backlog_drains() -> None:
    """A window equal to the cadence would abandon anything a firing could not
    reach before the next one. 24h >> 15min, deliberately."""
    assert CONTENT_LOOKBACK == timedelta(hours=24)


async def test_nothing_to_review_skips_rather_than_falling_back() -> None:
    """THE DIFFERENCE FROM THE FRAUD SWEEP, pinned.

    None here means SKIP. The caller must not substitute a descriptor: there is
    no fallback declared for this agent, and the fraud sweep's is shaped for a
    different schema entirely.
    """
    assert await harvest_content_signal(
        _Source(None), org_id="org_a",
        reviewer_agent_id=CONTENT_REVIEW_AGENT_ID, now=NOW,
    ) is None
    assert fallback_input_for(CONTENT_REVIEW_AGENT_ID) is None


async def test_no_source_wired_skips_and_does_not_raise() -> None:
    assert await harvest_content_signal(
        None, org_id="org_a",
        reviewer_agent_id=CONTENT_REVIEW_AGENT_ID, now=NOW,
    ) is None


async def test_a_failed_read_skips_and_does_not_crash_the_firing() -> None:
    """A database hiccup must not fail a Temporal activity into a retry storm."""
    assert await harvest_content_signal(
        _Exploding(), org_id="org_a",
        reviewer_agent_id=CONTENT_REVIEW_AGENT_ID, now=NOW,
    ) is None


async def test_a_backlog_is_reported_but_never_reaches_the_record() -> None:
    """One firing reviews one item; the count of what is waiting is logged.

    It is NOT put in the payload: `BrandCheckIn` forbids extra fields, and a
    number the agent was never asked about has no business on its input.
    """
    signal = await harvest_content_signal(
        _Source(_row(backlog=17)), org_id="org_a",
        reviewer_agent_id=CONTENT_REVIEW_AGENT_ID, now=NOW,
    )
    assert signal is not None
    assert set(signal) == {"brief_id", "content", "content_kind"}


# --------------------------------------------------------------------------- #
# The dispatch
# --------------------------------------------------------------------------- #

async def test_harvest_for_routes_each_agent_to_its_own_harvester() -> None:
    """The one agent-aware seam. A source wired under the wrong agent id must
    not be used by the other agent's harvester."""
    source = _Source(_row())
    signal = await harvest_for(
        CONTENT_REVIEW_AGENT_ID, source, org_id="org_a", now=NOW
    )
    assert signal is not None and "content_kind" in signal
    assert source.calls, "the content source must have been consulted"


async def test_harvest_for_returns_none_for_an_agent_with_no_harvester() -> None:
    """Not a crash and not a silent wrong-shape payload -- None, which the
    caller then resolves through `fallback_input_for`."""
    assert await harvest_for(
        "ad_copy_agent", _Source(_row()), org_id="org_a", now=NOW
    ) is None
