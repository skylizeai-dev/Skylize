"""The fourth ToolProxy gate, and the ceiling-breach trigger.

Two halves of the same guarantee: nothing reaches a customer's infrastructure
without a live federation covering that exact machine (the gate), and nothing is
proposed against a federation already known to be broken (the trigger).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from skylize.app.gcp.trigger import SpendCeilingContainmentTrigger
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    InMemoryGcpWifRepository,
)
from skylize.tools.base import (
    ToolWifNotConnected,
    ToolWifTargetNotAllowed,
    ToolWifTrustBroken,
)

PROJECT, ZONE, INSTANCE = "cust-prod", "us-central1-a", "runaway"


def _row(org: str, state: str = "valid", reason: str | None = None):
    now = datetime.now(timezone.utc)
    return GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id=org, label="", issuer_slug="s" * 26,
        gcp_project_id=PROJECT, gcp_project_number="1",
        workload_identity_pool_id="pool", workload_identity_provider_id="prov",
        audience="//iam.googleapis.com/projects/1/locations/global/x/y",
        access_mode="direct", service_account_email=None,
        signing_key_id="k", jwks_delivery="served", expected_jwks_key_id=None,
        connection_state=state, state_reason=reason,  # type: ignore[arg-type]
        last_probe_at=None, last_probe_result=None, last_success_at=None,
        created_at=now, updated_at=now,
    )


def _target(row, instance: str = INSTANCE, enabled: bool = True):
    return GcpWifTargetRow(
        target_id=uuid.uuid4(), conn_id=row.conn_id, org_id=row.org_id,
        gcp_project_id=PROJECT, zone=ZONE, instance_name=instance,
        enabled=enabled, created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# The gate, exercised through its real implementation
# ---------------------------------------------------------------------------

class _Input:
    """Stands in for a validated tool input; the gate reads named attributes."""

    def __init__(self, project=PROJECT, zone=ZONE, instance=INSTANCE):
        self.project, self.zone, self.instance = project, zone, instance


async def _authorize(repo, org="org_a", inp=None):
    """Call the real `_authorize_wif` with the minimum surrounding scaffolding."""
    from skylize.tools.base import ToolWifProfile
    from skylize.tools.proxy import ToolProxy

    class _Tool:
        tool_id = "integration.gcp_stop_instance"
        wif = ToolWifProfile(
            label="", project_field="project", zone_field="zone",
            instance_field="instance",
        )

    class _Contract:
        agent_id = "infrastructure_executor"
        department = "security"

    proxy = ToolProxy.__new__(ToolProxy)
    proxy._wif_repo = repo  # type: ignore[attr-defined]

    async def _noop(**_kw):
        return None

    proxy._audit_call = _noop  # type: ignore[attr-defined]
    await ToolProxy._authorize_wif(
        proxy, tool=_Tool(), validated_input=inp or _Input(),  # type: ignore[arg-type]
        contract=_Contract(), org_id=org, correlation_id=uuid.uuid4(),  # type: ignore[arg-type]
        governance_token=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_gate_allows_an_enabled_target_on_a_valid_connection() -> None:
    repo = InMemoryGcpWifRepository()
    row = _row("org_a")
    await repo.insert(row)
    await repo.add_target(_target(row))
    await _authorize(repo)  # must not raise


@pytest.mark.asyncio
async def test_gate_refuses_when_the_org_has_no_federation() -> None:
    with pytest.raises(ToolWifNotConnected):
        await _authorize(InMemoryGcpWifRepository())


@pytest.mark.asyncio
async def test_gate_fails_closed_when_no_federation_store_is_wired() -> None:
    """A tool that mutates customer infrastructure must never dispatch because
    the CHECK was missing, which is the same posture the other three gates take
    on an absent dependency."""
    with pytest.raises(ToolWifNotConnected, match="refusing to act"):
        await _authorize(None)


@pytest.mark.parametrize(
    ("state", "reason"),
    [("revoked", "pool deleted"), ("misconfigured", "binding removed"),
     ("unverified", None)],
)
@pytest.mark.asyncio
async def test_gate_refuses_a_connection_the_probe_already_found_broken(
    state: str, reason: str | None
) -> None:
    """THE POINT OF THE PROBE EXISTING.

    A federation already known to be unusable is refused before anything is
    minted, so an urgent containment fails in milliseconds naming the remedy
    instead of after a round trip to Google.
    """
    repo = InMemoryGcpWifRepository()
    row = _row("org_a", state=state, reason=reason)
    await repo.insert(row)
    await repo.add_target(_target(row))
    with pytest.raises(ToolWifTrustBroken) as exc:
        await _authorize(repo)
    assert state in str(exc.value)


@pytest.mark.asyncio
async def test_gate_refuses_an_instance_that_is_not_on_the_allow_list() -> None:
    """Federating a project does NOT authorise every machine in it."""
    repo = InMemoryGcpWifRepository()
    row = _row("org_a")
    await repo.insert(row)
    await repo.add_target(_target(row, instance="some-other-vm"))
    with pytest.raises(ToolWifTargetNotAllowed):
        await _authorize(repo)


@pytest.mark.asyncio
async def test_gate_refuses_a_disabled_target() -> None:
    """Disabling is the reversible way to take a machine out of scope."""
    repo = InMemoryGcpWifRepository()
    row = _row("org_a")
    await repo.insert(row)
    await repo.add_target(_target(row, enabled=False))
    with pytest.raises(ToolWifTargetNotAllowed):
        await _authorize(repo)


@pytest.mark.asyncio
async def test_gate_does_not_let_one_org_reach_another_orgs_target() -> None:
    """Tenancy at the gate, independent of the database's RLS."""
    repo = InMemoryGcpWifRepository()
    row_a = _row("org_a")
    await repo.insert(row_a)
    await repo.add_target(_target(row_a))
    await repo.insert(_row("org_b"))
    with pytest.raises(ToolWifTargetNotAllowed):
        await _authorize(repo, org="org_b")


# ---------------------------------------------------------------------------
# The trigger
# ---------------------------------------------------------------------------

class _Execution:
    """Stands in for AgentExecutionService, recording what was proposed."""

    def __init__(self, *, raise_deferred: bool = True) -> None:
        self.calls: list[dict] = []
        self._raise_deferred = raise_deferred
        self.hitl_id = uuid.uuid4()

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_deferred:
            from skylize.app.agents.execution import AgentDeferredToHuman

            raise AgentDeferredToHuman(
                hitl_id=self.hitl_id, reason="external_publication"
            )
        return object()


async def _trigger(repo, execution):
    return SpendCeilingContainmentTrigger(wif_repo=repo, execution=execution)


@pytest.mark.asyncio
async def test_ceiling_breach_proposes_a_containment_that_awaits_a_human() -> None:
    """The happy path ends in a QUEUED DECISION, never a completed action."""
    repo = InMemoryGcpWifRepository()
    row = _row("org_a")
    await repo.insert(row)
    await repo.add_target(_target(row))
    execution = _Execution()

    result = await (await _trigger(repo, execution)).propose_containment(
        org_id="org_a", breach_reason="LLM ceiling exceeded", user_id="u1",
    )

    assert result.proposed is True
    assert result.hitl_id == execution.hitl_id
    assert (result.project, result.zone, result.instance) == (PROJECT, ZONE, INSTANCE)
    # It went through the ORDINARY execution path, on the executor contract.
    assert execution.calls[0]["agent_id"] == "infrastructure_executor"
    assert execution.calls[0]["input_data"]["instance"] == INSTANCE
    assert execution.calls[0]["input_data"]["reason"] == "LLM ceiling exceeded"


@pytest.mark.asyncio
async def test_no_federation_means_no_proposal_and_a_distinct_status() -> None:
    result = await (await _trigger(InMemoryGcpWifRepository(), _Execution())
                    ).propose_containment(
        org_id="org_a", breach_reason="x", user_id="u1")
    assert result.status == "no_connection"
    assert result.proposed is False


@pytest.mark.asyncio
async def test_a_broken_federation_is_not_proposed_against() -> None:
    """Queueing an approval that could only fail at click time is worse than
    telling the operator now that the federation is broken."""
    repo = InMemoryGcpWifRepository()
    row = _row("org_a", state="misconfigured", reason="IAM binding removed")
    await repo.insert(row)
    await repo.add_target(_target(row))
    execution = _Execution()

    result = await (await _trigger(repo, execution)).propose_containment(
        org_id="org_a", breach_reason="x", user_id="u1")

    assert result.status == "trust_not_valid"
    assert "IAM binding removed" in result.detail
    assert execution.calls == [], "a proposal was submitted against a broken trust"


@pytest.mark.asyncio
async def test_a_valid_federation_with_no_targets_proposes_nothing() -> None:
    repo = InMemoryGcpWifRepository()
    await repo.insert(_row("org_a"))
    execution = _Execution()
    result = await (await _trigger(repo, execution)).propose_containment(
        org_id="org_a", breach_reason="x", user_id="u1")
    assert result.status == "no_targets"
    assert execution.calls == []


@pytest.mark.asyncio
async def test_an_unexpected_auto_approval_is_reported_as_a_defect() -> None:
    """If the gate ever APPROVES instead of deferring, that is a configuration
    defect, not a success. Accepting it quietly would mean a customer's VM was
    stopped with no human verdict."""
    repo = InMemoryGcpWifRepository()
    row = _row("org_a")
    await repo.insert(row)
    await repo.add_target(_target(row))

    result = await (await _trigger(repo, _Execution(raise_deferred=False))
                    ).propose_containment(
        org_id="org_a", breach_reason="x", user_id="u1")

    assert result.status == "execution_unavailable"
    assert "without deferring to a human" in result.detail


@pytest.mark.asyncio
async def test_the_trigger_never_stops_anything_itself() -> None:
    """It proposes. The tool acts, and only after an approval.

    Asserted by CAPABILITY rather than by an exact attribute set: the trigger
    holds no signing key, no HTTP client and no executor, so there is nothing on
    it that could reach Google. An exact-set assertion was the first version and
    was wrong - it broke when the cooldown bookkeeping was added, which is a
    change that has nothing to do with the property being protected.
    """
    trigger = await _trigger(InMemoryGcpWifRepository(), _Execution())
    held = list(vars(trigger).values())

    from skylize.app.gcp.actions import GcpKillSwitchExecutor
    from skylize.app.gcp.keys import WifSigningKey

    assert not any(isinstance(v, (GcpKillSwitchExecutor, WifSigningKey)) for v in held)
    assert not any(hasattr(v, "stop_and_release") for v in held)
    # Nothing on the trigger can perform HTTP either.
    assert not any(hasattr(v, "post") or hasattr(v, "request") for v in held)
