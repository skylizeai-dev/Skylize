"""The parameter that must change NOTHING when it is absent.

`AgentExecutionService.execute()` gained a keyword-only `on_behalf_of_principal`.
The governing constraint for that change is:

    when the parameter is ABSENT, every existing caller must behave
    BYTE-IDENTICALLY, INCLUDING THE TOKEN SIGNING INPUT.

Two independent things have to hold for that, and this module proves both rather
than asserting the intent:

  1. execute() must request NO scope override, so `mint` takes its own
     `scope is None` default branch (app/governance/authority.py:296-298) and
     derives the manifest exactly as it did before. That is
     `_principal_scope_for` returning None.
  2. the signing payload must be the frozen eleven-key v1.0 dict, with
     `token_version` and `on_behalf_of` ABSENT — not present-and-null. A key
     whose value is null still changes the bytes, and every token already in
     flight is signed over exactly those eleven keys
     (contracts/token.py:76-93).

The per-employee direction is covered too, because "absent changes nothing" is
only meaningful alongside "present changes exactly the intended thing".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from skylize.app.agents.execution import AgentExecutionService
from skylize.app.principal.errors import AuthorityExceeded, AuthorityUnavailable
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.contracts.base import OnBehalfOf
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import canonical_signing_bytes

ORG = "org_parity"
PRINCIPAL = "devon"

#: The v1.0 payload. FROZEN — see contracts/token.py:76-93. If this set ever needs
#: editing, every token this platform has ever signed has just been invalidated.
_V10_KEYS = {
    "token_id",
    "agent_id",
    "authority_level",
    "department",
    "delegation_chain",
    "scope",
    "max_token_budget",
    "max_execution_time_seconds",
    "issued_at",
    "expires_at",
    "nonce",
}


def _base_fields() -> dict[str, object]:
    now = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    return {
        "token_id": uuid4(),
        "agent_id": "cowork_agent",
        "authority_level": "worker",
        "department": "cowork",
        "delegation_chain": ["cowork_agent"],
        "scope": ["llm.generate", "memory.search"],
        "max_token_budget": 40_000,
        "max_execution_time_seconds": 120,
        "issued_at": now,
        "expires_at": now + timedelta(minutes=5),
        "nonce": "deadbeef",
    }


def _service(principal_authority: object | None = None) -> AgentExecutionService:
    return AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=MagicMock(),
        deliverables=MagicMock(),
        principal_authority=principal_authority,  # type: ignore[arg-type]
    )


# ── 1. The signing input is untouched when the parameter is absent ───────────

def test_v10_bytes_are_identical_whether_or_not_the_defaults_are_passed() -> None:
    """The literal byte-identity claim.

    execute() now always forwards `scope=` and `on_behalf_of_principal=` to mint.
    With no principal both are None -- which must produce the exact same bytes as
    not passing them at all, or every token in flight changes.
    """
    fields = _base_fields()
    implicit = canonical_signing_bytes(**fields)  # type: ignore[arg-type]
    explicit = canonical_signing_bytes(
        **fields,  # type: ignore[arg-type]
        token_version="1.0",
        on_behalf_of=None,
    )
    assert implicit == explicit


def test_v10_payload_carries_exactly_the_eleven_frozen_keys() -> None:
    """`token_version` / `on_behalf_of` are ABSENT, not present-and-null."""
    payload = json.loads(canonical_signing_bytes(**_base_fields()))  # type: ignore[arg-type]
    assert set(payload) == _V10_KEYS
    assert "token_version" not in payload
    assert "on_behalf_of" not in payload


def test_v11_adds_exactly_two_keys_and_nothing_else() -> None:
    """The contrast case: presence changes exactly the intended thing."""
    payload = json.loads(
        canonical_signing_bytes(
            **_base_fields(),  # type: ignore[arg-type]
            token_version="1.1",
            on_behalf_of=OnBehalfOf(
                principal_id=PRINCIPAL,
                authority_fingerprint="a" * 64,
                session_kind="cowork",
            ),
        )
    )
    assert set(payload) - _V10_KEYS == {"token_version", "on_behalf_of"}
    assert payload["on_behalf_of"]["principal_id"] == PRINCIPAL


# ── 2. execute() requests no scope override when there is no principal ───────

@pytest.mark.asyncio
async def test_no_principal_requests_no_scope_so_mint_keeps_its_default() -> None:
    contract = MVP_REGISTRY.resolve("cowork_agent")
    scope = await _service()._principal_scope_for(
        contract, org_id=ORG, principal_id=None
    )
    assert scope is None


@pytest.mark.asyncio
async def test_no_principal_needs_no_provider_at_all() -> None:
    """The autonomous shape must not even consult the principal provider.

    Built without one (every pre-existing construction site, including
    bootstrap's), an ordinary execute() still has to work.
    """
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    assert (
        await _service(principal_authority=None)._principal_scope_for(
            contract, org_id=ORG, principal_id=None
        )
        is None
    )


# ── 3. The per-employee shape narrows, and fails closed ──────────────────────

def _provider(*scopes: str, suspended: bool = False) -> PrincipalAuthorityService:
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(
            principal_id=PRINCIPAL,
            org_id=ORG,
            display_name="Devon",
            authority_level="executive",  # deliberately the HIGHEST level
            suspended_at=(
                datetime(2026, 1, 1, tzinfo=timezone.utc) if suspended else None
            ),
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


@pytest.mark.asyncio
async def test_intersection_is_requested_never_the_full_manifest() -> None:
    """A principal holding ONE of the two manifest tools gets exactly that one.

    Requesting the full manifest instead would make the agent unusable by anyone
    not personally holding every tool in it -- the trap
    app/cowork/session.py:101-114 documents.
    """
    contract = MVP_REGISTRY.resolve("cowork_agent")
    assert [t.tool_id for t in contract.allowed_tools] == [
        "llm.generate",
        "memory.search",
    ]
    scope = await _service(_provider("llm.generate"))._principal_scope_for(
        contract, org_id=ORG, principal_id=PRINCIPAL
    )
    assert scope == ["llm.generate"]


@pytest.mark.asyncio
async def test_a_scope_the_principal_lacks_is_never_added_by_the_agent() -> None:
    """The whole security property, at this seam: grants OUTSIDE the manifest do
    not widen the run, and manifest tools the human lacks do not appear."""
    contract = MVP_REGISTRY.resolve("cowork_agent")
    scope = await _service(
        _provider("llm.generate", "stripe.refund")
    )._principal_scope_for(contract, org_id=ORG, principal_id=PRINCIPAL)
    assert scope == ["llm.generate"]
    assert "stripe.refund" not in (scope or [])
    assert "memory.search" not in (scope or [])


@pytest.mark.asyncio
async def test_an_executive_principal_does_not_raise_the_contract_level() -> None:
    """Q4: two independent ceilings. The human's org position is `executive`;
    the contract is `worker`. The position must not touch the token's level.

    Authority level is sourced ONLY from the contract at mint
    (app/governance/authority.py:317), and the `on_behalf_of` claim carries no
    level at all (contracts/token.py:120-124) -- so the two axes cannot meet.
    """
    contract = MVP_REGISTRY.resolve("cowork_agent")
    assert contract.authority_level == "worker"
    scope = await _service(_provider("llm.generate"))._principal_scope_for(
        contract, org_id=ORG, principal_id=PRINCIPAL
    )
    # The compiled authority influences SCOPE and nothing else.
    assert scope == ["llm.generate"]
    assert contract.authority_level == "worker"


@pytest.mark.asyncio
async def test_empty_intersection_refuses_rather_than_running_toolless() -> None:
    contract = MVP_REGISTRY.resolve("cowork_agent")
    with pytest.raises(AuthorityExceeded):
        await _service(_provider("stripe.refund"))._principal_scope_for(
            contract, org_id=ORG, principal_id=PRINCIPAL
        )


@pytest.mark.asyncio
async def test_no_provider_plus_a_principal_fails_closed() -> None:
    """Never an ungated principal token: absence of the provider is a denial."""
    contract = MVP_REGISTRY.resolve("cowork_agent")
    with pytest.raises(AuthorityUnavailable):
        await _service(principal_authority=None)._principal_scope_for(
            contract, org_id=ORG, principal_id=PRINCIPAL
        )
