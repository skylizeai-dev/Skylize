"""The autonomous run's authority ceiling: the human's grants bound the agent.

WHY THIS FILE EXISTS SEPARATELY FROM `test_execute_on_behalf_of_signing_parity`.
That module proves the ceiling at the `_principal_scope_for` seam for the COWORK
shape, where a human is sitting there. This one pins the same property for the
shape where nobody is: `session_kind="autonomous"`, the pilot agent, a principal
resolved from `escalation_path` rather than chosen by a caller.

The distinction matters because the autonomous shape is the one where a silent
widening would never be noticed. A cowork user who loses a tool sees the agent
refuse in front of them; a scheduled run that quietly acquired a tool its
attributed human never held would show up as nothing at all.

THE THREE BEHAVIOURS, and they are deliberately different from one another:

  * PARTIAL intersection -> the run proceeds NARROWED to the intersection. This
    is the normal case, not a degradation: one contract is driven on behalf of
    humans holding different grants, and the answer is the token each of them is
    entitled to (app/agents/execution.py:541-549).
  * EMPTY intersection -> REFUSED (`AuthorityExceeded`). A zero-tool token is not
    a degraded run, it is an unauthorized one (execution.py:551-552).
  * EXCESS ever requested at the MINT -> REFUSED, never trimmed, naming the
    offending scopes (app/principal/authority.py:169-172). The narrowing above
    happens at the CALLER, which asks for the ceiling; the mint's own gate is
    what makes "ask for more" impossible rather than merely unusual.

And the refusal must be VISIBLE: the runner turns it into a `failed` journal row
flagged `requires_attention`, so it lands in the attributed human's brief instead
of vanishing into a log nobody reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from skylize.app.agents.execution import AgentExecutionService
from skylize.app.autonomy.pilot import PILOT_AGENT_ID, pilot_input
from skylize.app.autonomy.principal import AutonomousPrincipalResolver
from skylize.app.autonomy.runner import (
    KIND_FAILED,
    AutonomousRunRequest,
    AutonomousRunService,
)
from skylize.app.principal.authority import resolve_effective_scope
from skylize.app.principal.errors import AuthorityExceeded
from skylize.app.principal.models import ActorKind, Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.contracts.mvp import ALL_MVP_CONTRACTS
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry
from skylize.dal.ports import UserRow

ORG = "org_autonomy_ceiling"
OWNER_ID = uuid.UUID("9f1c0d22-1111-4b44-8c33-aaaabbbbcccc")
PRINCIPAL = str(OWNER_ID)
REGISTRY = AgentRegistry(ALL_MVP_CONTRACTS)
PILOT = MVP_REGISTRY.resolve(PILOT_AGENT_ID)

#: fraud_detection_agent's manifest. Asserted rather than hard-coded downstream,
#: so a contract edit fails HERE with a clear message instead of quietly making
#: every intersection below vacuous.
MANIFEST = ("llm.generate", "memory.search")


def test_the_pilot_manifest_is_what_these_cases_assume() -> None:
    assert tuple(t.tool_id for t in PILOT.allowed_tools) == MANIFEST


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

def _authority(*scopes: str) -> PrincipalAuthorityService:
    """A principal holding exactly `scopes`, at the HIGHEST authority level.

    `executive` deliberately: it removes the level axis from these cases, so a
    refusal below can only be about SCOPE. If a high level could rescue a missing
    grant, that is precisely the bug worth failing on.
    """
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(
            principal_id=PRINCIPAL,
            org_id=ORG,
            display_name="Owner",
            authority_level="executive",
        )
    )
    for s in scopes:
        repo.add_grant(
            org_id=ORG,
            principal_id=PRINCIPAL,
            grant=Grant(
                scope=s,
                source=GrantSource.POSITION,
                valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ),
        )
    return PrincipalAuthorityService(repo)


def _execution(authority: PrincipalAuthorityService) -> AgentExecutionService:
    return AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=MagicMock(),
        deliverables=MagicMock(),
        principal_authority=authority,
    )


async def _scope_for(authority: PrincipalAuthorityService) -> list[str] | None:
    return await _execution(authority)._principal_scope_for(
        PILOT, org_id=ORG, principal_id=PRINCIPAL
    )


# --------------------------------------------------------------------------- #
# 1. The ceiling itself
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_human_holding_the_whole_manifest_gets_the_whole_manifest() -> None:
    """The anchor. Without it, a bug that refused EVERYTHING would look like a
    pass on the two narrowing cases below."""
    assert await _scope_for(_authority(*MANIFEST)) == sorted(MANIFEST)


@pytest.mark.asyncio
async def test_a_manifest_beyond_the_human_narrows_to_the_intersection() -> None:
    """Partial overlap is the NORMAL case and must not refuse.

    The agent's manifest exceeds the human's authority here -- it wants
    `memory.search`, which this human has never been granted -- and the run
    proceeds with exactly what the human can delegate, never the manifest.
    """
    scope = await _scope_for(_authority("llm.generate"))
    assert scope == ["llm.generate"]
    assert "memory.search" not in (scope or [])


@pytest.mark.asyncio
async def test_a_grant_outside_the_manifest_never_widens_the_run() -> None:
    """The ceiling is an INTERSECTION, not a union. A human who can issue refunds
    does not thereby let a fraud agent issue them."""
    scope = await _scope_for(_authority("llm.generate", "stripe.refund"))
    assert scope == ["llm.generate"]
    assert "stripe.refund" not in (scope or [])


@pytest.mark.asyncio
async def test_an_empty_intersection_refuses_instead_of_running_toolless() -> None:
    """THE safety-critical case. The human holds neither manifest tool, so there
    is no token to mint that both serves the agent and stays inside the human's
    authority -- and the answer is a refusal, not a zero-tool run."""
    with pytest.raises(AuthorityExceeded) as ei:
        await _scope_for(_authority("stripe.refund"))
    # The refusal NAMES what was refused; a denial nobody can debug gets relaxed.
    assert sorted(ei.value.excess) == sorted(MANIFEST)
    assert ei.value.principal_id == PRINCIPAL


# --------------------------------------------------------------------------- #
# 2. The mint's own gate: excess is refused, never trimmed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_mint_refuses_an_excess_rather_than_silently_trimming_it() -> None:
    """The caller above asks for the ceiling, so the mint normally sees no excess.
    This is what happens if anything ever asks for MORE -- a bug or an attack.

    `resolve_effective_scope` is the function `mint()` calls before signing
    (app/governance/authority.py:439-443), so this is the real gate, not a
    restatement of it.
    """
    authority = _authority("llm.generate")
    snapshot = await authority.snapshot_for(
        org_id=ORG, principal_id=PRINCIPAL, at=datetime.now(timezone.utc)
    )
    with pytest.raises(AuthorityExceeded) as ei:
        resolve_effective_scope(
            requested=list(MANIFEST),  # asks for BOTH
            contract_tools=list(MANIFEST),
            snapshot=snapshot,  # human holds only llm.generate
        )
    assert ei.value.excess == ["memory.search"]
    # Not trimmed to the survivable subset and waved through.
    assert "llm.generate" not in ei.value.excess


# --------------------------------------------------------------------------- #
# 3. A refused autonomous run is VISIBLE, not dropped
# --------------------------------------------------------------------------- #

class _Journal:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> int:
        self.rows.append(kwargs)
        return len(self.rows)


class _RefusingExecution:
    """Stands in for the real execute() at the moment its ceiling check fails."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise AuthorityExceeded(
            requested=sorted(MANIFEST),
            ceiling=[],
            excess=sorted(MANIFEST),
            principal_id=PRINCIPAL,
        )


class _Users:
    async def list_by_org(self, org_id: str) -> list[UserRow]:
        return [
            UserRow(
                user_id=OWNER_ID,
                org_id=ORG,
                email="owner@example.test",
                password_hash="x",
                roles=["owner"],
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
        ]


def _runner(journal: _Journal, execution: _RefusingExecution) -> AutonomousRunService:
    return AutonomousRunService(
        registry=REGISTRY,
        execution=execution,  # type: ignore[arg-type]
        resolver=AutonomousPrincipalResolver(_Users()),  # type: ignore[arg-type]
        journal=journal,  # type: ignore[arg-type]
        cost_source=None,
    )


def _request() -> AutonomousRunRequest:
    return AutonomousRunRequest(
        org_id=ORG,
        agent_id=PILOT_AGENT_ID,
        input_data=pilot_input(),
        trigger="schedule:ceiling-test",
    )


@pytest.mark.asyncio
async def test_a_refused_autonomous_run_lands_in_the_human_brief() -> None:
    """Refusal must be LOUD. The run is attributed to the resolved owner, marked
    `requires_attention`, and names the authority failure -- one row, no silent
    success and no silent drop."""
    journal, execution = _Journal(), _RefusingExecution()
    outcome = await _runner(journal, execution).run(_request())

    assert outcome.status == "failed"
    assert outcome.requires_attention is True
    assert any("AuthorityExceeded" in r for r in outcome.reasons)
    # Attributed to the HUMAN at the end of escalation_path, not to the agent.
    assert outcome.principal_id == PRINCIPAL

    assert len(journal.rows) == 1
    row = journal.rows[0]
    assert row["kind"] == KIND_FAILED
    assert row["requires_attention"] is True
    assert row["principal_id"] == PRINCIPAL
    assert row["actor_kind"] is ActorKind.AGENT_AUTONOMOUS
    assert row["detail"]["session_kind"] == "autonomous"
    assert any("AuthorityExceeded" in r for r in row["detail"]["reasons"])


@pytest.mark.asyncio
async def test_the_refusal_is_not_retried_into_a_second_run() -> None:
    """One refused firing is ONE attempt and ONE row. A refusal that retried
    would spend the ceiling check over and over and multiply the brief entry."""
    journal, execution = _Journal(), _RefusingExecution()
    await _runner(journal, execution).run(_request())
    assert execution.calls == 1
    assert len(journal.rows) == 1
