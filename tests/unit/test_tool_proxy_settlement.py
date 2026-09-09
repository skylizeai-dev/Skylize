"""Settlement accounting on the tool-call path: what the hold commits FOR.

`tests/unit/test_tool_proxy_spend.py` proves a hold is placed and settled. This
file proves it settles for the RIGHT AMOUNT — the distinction that matters when a
tool's actual spend can come in below what it reserved (a partially-approved
refund, a short-filled order).

The property under test: `ToolProxy` settles a hold with the amount the tool
reports through its declared `actual_amount_field`, falls back to the full
reservation only when the tool declares no such field, and — when a tool declares
one but reports something unusable — settles the full reservation while recording
WHY on the audit row. Never a silent fallback.

Ledger consistency after a partial settle (does the unspent difference return to
the ceiling?) is asserted here against the fake and, against real SQL where it
actually counts, in tests/integration/test_tool_proxy_spend_pg.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.app.principal.spend import SpendLedger
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolContext, ToolDefinition
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

from .test_tool_proxy_spend import FakeSpendRepo

ORG = "org_test"
PRINCIPAL = "devon"
AGENT = "settlement_test_agent"

#: Declares `actual_amount_field` — spends at most what it reserved.
PARTIAL_TOOL = "test.partial_spend"
#: Spend-capable with NO `actual_amount_field`. The shape every shipped connector
#: would take today; its behaviour must not move.
FULL_TOOL = "test.full_spend"


class _SpendIn(BaseModel):
    amount_minor: int


class _PartialOut(BaseModel):
    """`settled_minor` is what the provider actually moved, which is the whole
    point: it is NOT assumed equal to the requested `amount_minor`."""

    settled_minor: int
    ok: bool = True


class _FullOut(BaseModel):
    ok: bool = True


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="Settlement test",
        authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[
            ToolGrant(tool_id=PARTIAL_TOOL, purpose="test"),
            ToolGrant(tool_id=FULL_TOOL, purpose="test"),
        ],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _registry(*, reports: object) -> ToolRegistry:
    """`reports` is what the partial tool puts in `settled_minor`. Deliberately
    untyped so a test can hand back a value the field's own validation would
    normally stop — the proxy must not trust the output either way."""

    async def partial_handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        out = _PartialOut(settled_minor=0)
        # Bypasses pydantic on purpose: a handler returning a hand-built or
        # third-party model is exactly the case the proxy's own guards exist for.
        object.__setattr__(out, "settled_minor", reports)
        return out

    async def full_handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _FullOut()

    return ToolRegistry([
        ToolDefinition(
            tool_id=PARTIAL_TOOL, name="Partial", description="may spend less",
            input_schema=_SpendIn, output_schema=_PartialOut, category="integration",
            handler=partial_handler,
            spend={
                "currency": "USD",
                "amount_field": "amount_minor",
                "actual_amount_field": "settled_minor",
            },
        ),
        ToolDefinition(
            tool_id=FULL_TOOL, name="Full", description="always spends the ask",
            input_schema=_SpendIn, output_schema=_FullOut, category="integration",
            handler=full_handler,
            spend={"currency": "USD", "amount_field": "amount_minor"},
        ),
    ])


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(
            principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
            authority_level="manager",
        )
    )
    for scope in (PARTIAL_TOOL, FULL_TOOL):
        repo.add_grant(
            org_id=ORG, principal_id=PRINCIPAL,
            grant=Grant(
                scope=scope, source=GrantSource.POSITION,
                valid_from=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        )
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
    )
    return authority, bus, audit


async def _invoke(tool_id: str, *, reserve: int, reports: object, ceiling: int = 100_000):
    authority, bus, audit = _authority()
    spend_repo = FakeSpendRepo(ceiling_minor=ceiling)
    proxy = ToolProxy(
        registry=_registry(reports=reports), audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=SpendLedger(spend_repo),
    )
    contract = _contract()
    corr = uuid4()
    token = await authority.mint(
        contract, org_id=ORG, correlation_id=corr, on_behalf_of_principal=PRINCIPAL
    )
    await proxy.invoke(
        tool_id=tool_id, input_data={"amount_minor": reserve},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    return spend_repo, bus


def _success_reasons(bus) -> list[str | None]:
    return [
        e.payload.result_reason
        for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked" and e.payload.result == "success"
    ]


# --------------------------------------------------------------------------- #
# The fix: settle for what the tool actually spent
# --------------------------------------------------------------------------- #


async def test_reserving_more_than_spent_commits_only_the_actual() -> None:
    """THE BUG THIS FILE EXISTS FOR. Reserve 10000, spend 6000 — the ledger must
    record 6000. Committing the reservation would book 4000 of spend that never
    happened and bind the org's ceiling against it."""
    repo, _ = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=6_000)

    assert repo.spent_minor == 6_000, "only the amount that actually moved is spend"
    assert [amount for _, amount in repo.commits] == [6_000]


async def test_the_unspent_difference_returns_to_the_ceiling() -> None:
    """Ledger consistency after a partial settle. Committing less than the hold
    must FREE the difference, not merely record a smaller number — otherwise every
    later ceiling check for this org is computed against spend that never
    happened."""
    repo, _ = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=6_000, ceiling=10_000)

    assert repo.reserved_minor == 0, "the hold must not survive the settle"
    assert repo.spent_minor == 6_000
    available = repo.ceiling_minor - repo.spent_minor - repo.reserved_minor
    assert available == 4_000, "the unspent 4000 must be spendable again"


async def test_spending_exactly_the_reservation_still_commits_in_full() -> None:
    repo, _ = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=10_000)

    assert repo.spent_minor == 10_000
    assert repo.reserved_minor == 0


async def test_a_tool_reporting_zero_spend_commits_zero() -> None:
    """A refund the provider declined outright still ran, so the hold settles —
    for nothing. Releasing instead would be indistinguishable from a crash on the
    audit trail."""
    repo, _ = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=0)

    assert repo.spent_minor == 0
    assert repo.reserved_minor == 0, "settled, not left held"
    assert len(repo.commits) == 1, "a zero-cost settle is still a settle"


# --------------------------------------------------------------------------- #
# Regression: tools that declare no actual-amount field
# --------------------------------------------------------------------------- #


async def test_a_tool_without_the_field_commits_the_full_reservation() -> None:
    """Every spend-capable tool shipped before this change. Its settlement must be
    byte-for-byte what it was: the reservation, in full."""
    repo, bus = await _invoke(FULL_TOOL, reserve=7_500, reports=None)

    assert repo.spent_minor == 7_500
    assert repo.reserved_minor == 0
    assert _success_reasons(bus) == [None], "an ordinary settle flags nothing"


# --------------------------------------------------------------------------- #
# Fail-safe: a declared field carrying an unusable value
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reported",
    [
        pytest.param(-1, id="negative"),
        pytest.param(True, id="bool-is-not-one-cent"),
        pytest.param("6000", id="string"),
        pytest.param(6_000.5, id="float"),
        pytest.param(None, id="missing"),
    ],
)
async def test_an_unusable_actual_settles_the_full_reservation_and_says_so(
    reported: object,
) -> None:
    """The action ALREADY RAN — the money moved before the proxy saw this value.
    Settling low would hand back budget a completed action consumed and let the
    org spend it twice, so the conservative direction is the reservation. What
    must never happen is settling it QUIETLY."""
    repo, bus = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=reported)

    assert repo.spent_minor == 10_000, "fall back to the reservation, not to zero"
    reasons = _success_reasons(bus)
    assert len(reasons) == 1 and reasons[0] is not None
    assert "settled the full reservation" in reasons[0]
    assert PARTIAL_TOOL in reasons[0]


async def test_reporting_more_than_reserved_is_flagged_not_absorbed() -> None:
    """Over-spend "is a policy event, not a rounding detail" (SpendLedger.commit).
    The repository's LEAST() clamp would produce the same number silently; the
    proxy must leave a reason behind instead."""
    repo, bus = await _invoke(PARTIAL_TOOL, reserve=10_000, reports=25_000)

    assert repo.spent_minor == 10_000, "never settle above the approved hold"
    reasons = _success_reasons(bus)
    assert reasons[0] is not None and "must go back through reserve" in reasons[0]


# --------------------------------------------------------------------------- #
# The declaration is checked before a tool can ever run
# --------------------------------------------------------------------------- #


def test_declaring_an_actual_field_absent_from_the_output_schema_is_rejected() -> None:
    """A typo here would send the proxy back to committing the reservation —
    silently over-committing exactly the spend this field exists to correct. So it
    fails at construction, where a human is still looking."""

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _PartialOut(settled_minor=0)

    with pytest.raises(ValidationError, match="settled_micros"):
        ToolDefinition(
            tool_id="test.typo", name="Typo", description="typo in the declaration",
            input_schema=_SpendIn, output_schema=_PartialOut, category="integration",
            handler=handler,
            spend={
                "currency": "USD",
                "amount_field": "amount_minor",
                "actual_amount_field": "settled_micros",  # not on _PartialOut
            },
        )


def test_declaring_an_amount_field_absent_from_the_input_schema_is_rejected() -> None:
    """The reservation half of the same guard, which had no check at all before."""

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _FullOut()

    with pytest.raises(ValidationError, match="cents"):
        ToolDefinition(
            tool_id="test.typo2", name="Typo2", description="typo on the input side",
            input_schema=_SpendIn, output_schema=_FullOut, category="integration",
            handler=handler,
            spend={"currency": "USD", "amount_field": "cents"},  # not on _SpendIn
        )


def test_a_tool_with_no_spend_profile_is_unconstrained() -> None:
    """The guard must not start demanding schema fields from the ~all tools that
    move no money."""

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _FullOut()

    tool = ToolDefinition(
        tool_id="test.free", name="Free", description="moves no money",
        input_schema=_SpendIn, output_schema=_FullOut, category="compute",
        handler=handler,
    )
    assert tool.spend is None
