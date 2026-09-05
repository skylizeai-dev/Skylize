"""The auto-hook: a breached ceiling proposes a containment with nobody asking.

The properties that matter here are as much about what the hook does NOT do as
what it does. It must not delay the spend denial, must not swallow it, must not
fire on an unverified signal, and must not flood the queue when a runaway agent
breaches on every call.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest

from skylize.app.principal.errors import CeilingExceeded, EnvelopeNotFound
from skylize.app.gcp.trigger import SpendCeilingContainmentTrigger
from skylize.dal.gcp_wif import InMemoryGcpWifRepository
from skylize.tools.base import (
    ToolSpendDeferredToHuman,
    ToolSpendHardDenied,
    ToolSpendProfile,
    ToolSpendUnavailable,
)
from skylize.tools.proxy import ToolProxy


class _Recorder:
    """Stands in for SpendCeilingContainmentTrigger, recording proposals."""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list[dict] = []
        self._boom = boom

    async def propose_containment(self, **kwargs):
        self.calls.append(kwargs)
        if self._boom:
            raise RuntimeError("proposal exploded")
        from skylize.app.gcp.trigger import ContainmentProposal

        return ContainmentProposal(
            status="proposed", detail="queued", hitl_id=uuid.uuid4()
        )


class _Ledger:
    """A spend ledger whose `reserve` raises whatever the test needs."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def reserve(self, **_kw):
        raise self._exc


class _Input:
    amount_minor = 500


class _OnBehalfOf:
    principal_id = "person_alice"


class _Token:
    token_id = uuid.uuid4()
    token_version = "1.1"
    on_behalf_of = _OnBehalfOf()


class _Tool:
    tool_id = "integration.some_spend_tool"
    spend = ToolSpendProfile(currency="USD", amount_field="amount_minor")


class _Contract:
    agent_id = "some_agent"
    department = "growth"


def _proxy(ledger, trigger=None) -> ToolProxy:
    proxy = ToolProxy.__new__(ToolProxy)
    proxy._spend_ledger = ledger          # type: ignore[attr-defined]
    proxy._containment = trigger          # type: ignore[attr-defined]
    proxy._containment_tasks = set()      # type: ignore[attr-defined]

    async def _noop(**_kw):
        return None

    proxy._audit_call = _noop             # type: ignore[attr-defined]
    return proxy


async def _reserve(proxy):
    return await ToolProxy._reserve_spend(
        proxy, tool=_Tool(), validated_input=_Input(),  # type: ignore[arg-type]
        contract=_Contract(), org_id="org_a",           # type: ignore[arg-type]
        correlation_id=uuid.uuid4(), governance_token=_Token(),  # type: ignore[arg-type]
    )


async def _drain(proxy) -> None:
    """Await the fire-and-forget tasks so assertions are deterministic."""
    while proxy._containment_tasks:
        await asyncio.gather(*list(proxy._containment_tasks), return_exceptions=True)


# ---------------------------------------------------------------------------
# It fires
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("defer", "expected"),
    [(True, ToolSpendDeferredToHuman), (False, ToolSpendHardDenied)],
)
@pytest.mark.asyncio
async def test_both_ceiling_dispositions_propose_a_containment(
    defer: bool, expected: type
) -> None:
    """`hard_deny` and `defer_to_human` differ in what happens to THIS TOOL CALL.

    The ceiling was breached either way, and that is the fact a containment
    responds to - so both fire.
    """
    trigger = _Recorder()
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=defer)), trigger)

    with pytest.raises(expected):
        await _reserve(proxy)
    await _drain(proxy)

    assert len(trigger.calls) == 1
    assert trigger.calls[0]["org_id"] == "org_a"
    assert trigger.calls[0]["user_id"] == "person_alice"
    assert "spend ceiling breached" in trigger.calls[0]["breach_reason"]


@pytest.mark.asyncio
async def test_the_breach_reason_names_the_tool_that_breached() -> None:
    """A reviewer approving a VM stop should be able to see what caused it."""
    trigger = _Recorder()
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), trigger)
    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)
    await _drain(proxy)
    assert "integration.some_spend_tool" in trigger.calls[0]["breach_reason"]


# ---------------------------------------------------------------------------
# It does NOT fire on an unverified signal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_envelope_not_found_does_not_propose_a_containment() -> None:
    """"We could not check" is not "the customer is overspending".

    Acting on an unverified signal is how a safety control starts stopping
    healthy machines. Same asymmetry `ToolCredentialUnavailable` draws against a
    revocation.
    """
    trigger = _Recorder()
    proxy = _proxy(_Ledger(EnvelopeNotFound("no envelope")), trigger)
    with pytest.raises(ToolSpendUnavailable):
        await _reserve(proxy)
    await _drain(proxy)
    assert trigger.calls == []


@pytest.mark.asyncio
async def test_a_missing_ledger_does_not_propose_a_containment() -> None:
    """A process with no ledger cannot know a ceiling was breached."""
    trigger = _Recorder()
    proxy = _proxy(None, trigger)
    with pytest.raises(ToolSpendUnavailable):
        await _reserve(proxy)
    await _drain(proxy)
    assert trigger.calls == []


# ---------------------------------------------------------------------------
# It never damages the denial
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_spend_denial_is_raised_even_if_the_proposal_explodes() -> None:
    """The caller is owed its denial regardless.

    A containment proposal that could not be created must not also destroy the
    error the caller needs to receive - the tool call still failed, and that is
    the fact the agent loop must act on.
    """
    trigger = _Recorder(boom=True)
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), trigger)

    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)
    await _drain(proxy)  # the task failed; the done-callback logged it


@pytest.mark.asyncio
async def test_the_denial_does_not_wait_for_the_proposal() -> None:
    """SCHEDULED, not awaited.

    The proposal is still pending when the denial has already been raised, which
    is the observable form of "the failing call is not delayed by containment
    bookkeeping".
    """
    started = asyncio.Event()
    release = asyncio.Event()

    class _Slow(_Recorder):
        async def propose_containment(self, **kwargs):
            started.set()
            await release.wait()
            return await super().propose_containment(**kwargs)

    trigger = _Slow()
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), trigger)

    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)

    # The denial has already surfaced while the proposal is still in flight.
    await asyncio.wait_for(started.wait(), timeout=1)
    assert proxy._containment_tasks, "the proposal was awaited inline"
    release.set()
    await _drain(proxy)
    assert len(trigger.calls) == 1


@pytest.mark.asyncio
async def test_no_trigger_wired_is_simply_a_no_op() -> None:
    """A deployment without GCP must be byte-identical to before this hook."""
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), None)
    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)
    assert proxy._containment_tasks == set()


@pytest.mark.asyncio
async def test_the_task_is_strongly_referenced_until_it_finishes() -> None:
    """asyncio holds only a WEAK reference to a running task.

    Without the proxy's own set, a fire-and-forget containment could be collected
    mid-flight and vanish with no error anywhere.
    """
    release = asyncio.Event()

    class _Slow(_Recorder):
        async def propose_containment(self, **kwargs):
            await release.wait()
            return await super().propose_containment(**kwargs)

    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), _Slow())
    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)

    assert len(proxy._containment_tasks) == 1
    release.set()
    await _drain(proxy)
    assert proxy._containment_tasks == set(), "the task was not released when done"


# ---------------------------------------------------------------------------
# Burst suppression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_burst_of_breaches_queues_exactly_one_containment() -> None:
    """A runaway agent breaches on EVERY subsequent call.

    Without the cooldown the reviewer would get one identical "stop this VM"
    approval per breach, at exactly the moment they need to make one decision
    quickly.
    """
    repo = InMemoryGcpWifRepository()

    class _Execution:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, **_kw):
            self.calls += 1
            from skylize.app.agents.execution import AgentDeferredToHuman

            raise AgentDeferredToHuman(hitl_id=uuid.uuid4(), reason="deferred")

    execution = _Execution()
    trigger = SpendCeilingContainmentTrigger(wif_repo=repo, execution=execution)

    from datetime import datetime, timezone

    from skylize.dal.gcp_wif import GcpWifConnectionRow, GcpWifTargetRow

    now = datetime.now(timezone.utc)
    row = GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id="org_a", label="", issuer_slug="s" * 26,
        gcp_project_id="p", gcp_project_number="1",
        workload_identity_pool_id="pool", workload_identity_provider_id="prov",
        audience="aud", access_mode="direct", service_account_email=None,
        signing_key_id="k", jwks_delivery="served", expected_jwks_key_id=None,
        connection_state="valid", state_reason=None, last_probe_at=None,
        last_probe_result=None, last_success_at=None, created_at=now, updated_at=now,
    )
    await repo.insert(row)
    await repo.add_target(GcpWifTargetRow(
        target_id=uuid.uuid4(), conn_id=row.conn_id, org_id="org_a",
        gcp_project_id="p", zone="z", instance_name="vm", enabled=True,
        created_at=now,
    ))

    results = [
        await trigger.propose_containment(
            org_id="org_a", breach_reason="breach", user_id="u1")
        for _ in range(5)
    ]

    assert results[0].status == "proposed"
    assert [r.status for r in results[1:]] == ["already_proposed"] * 4
    assert execution.calls == 1, "the burst queued more than one approval"


@pytest.mark.asyncio
async def test_a_failed_proposal_does_not_start_the_cooldown() -> None:
    """Only an ACCEPTED proposal suppresses the next one.

    A breach that found no federation should not stop the next breach from
    proposing once an operator has connected one.
    """
    repo = InMemoryGcpWifRepository()

    class _Execution:
        async def execute(self, **_kw):  # pragma: no cover - never reached
            raise AssertionError("should not execute without a connection")

    trigger = SpendCeilingContainmentTrigger(
        wif_repo=repo, execution=_Execution(), cooldown=timedelta(minutes=5),
    )
    first = await trigger.propose_containment(
        org_id="org_a", breach_reason="b", user_id="u1")
    second = await trigger.propose_containment(
        org_id="org_a", breach_reason="b", user_id="u1")

    assert first.status == "no_connection"
    assert second.status == "no_connection", (
        "a failed proposal started the cooldown and hid the next breach"
    )


@pytest.mark.asyncio
async def test_the_cooldown_is_per_org() -> None:
    """One org's containment must not suppress another's."""
    repo = InMemoryGcpWifRepository()
    trigger = SpendCeilingContainmentTrigger(wif_repo=repo, execution=object())
    trigger._last_proposed[("org_a", "")] = __import__("time").monotonic()
    assert trigger._within_cooldown("org_a", "") is True
    assert trigger._within_cooldown("org_b", "") is False


@pytest.mark.asyncio
async def test_shutdown_drains_in_flight_proposals() -> None:
    """A proposal scheduled moments before shutdown must not be lost silently.

    Without the drain the task is orphaned mid-write, and a containment that
    never reached the queue looks exactly like a breach that never happened.
    """
    release = asyncio.Event()
    finished: list[str] = []

    class _Slow(_Recorder):
        async def propose_containment(self, **kwargs):
            await release.wait()
            finished.append("done")
            return await super().propose_containment(**kwargs)

    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), _Slow())
    with pytest.raises(ToolSpendDeferredToHuman):
        await _reserve(proxy)

    assert finished == [], "the proposal completed before the drain was needed"
    release.set()
    await ToolProxy.drain_containment_tasks(proxy)
    assert finished == ["done"], "shutdown did not wait for the proposal"
    assert proxy._containment_tasks == set()


@pytest.mark.asyncio
async def test_draining_with_nothing_in_flight_is_a_no_op() -> None:
    """Shutdown must not hang on a proxy that never scheduled anything."""
    proxy = _proxy(_Ledger(CeilingExceeded("over", defer_to_human=True)), None)
    await asyncio.wait_for(ToolProxy.drain_containment_tasks(proxy), timeout=1)
