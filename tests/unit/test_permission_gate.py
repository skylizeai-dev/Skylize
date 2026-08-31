"""The elevated-action permission gate (org pre-authorization allow-list).

Covers the gate's pure matching logic and its denial asymmetry:
  * an empty allow-list denies (absence is never an implicit allow);
  * a link grant is not satisfiable by any address pattern;
  * a role above the org's maximum denies even when the recipient matches;
  * matching is exact string / bare-domain, never a glob or regex.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from skylize.app.permissions.gate import (
    PermissionDeniedError,
    PermissionGate,
    PermissionUnavailableError,
)
from skylize.dal.permission_grants import (
    InMemoryPermissionGrantRepository,
    PermissionGrantRow,
)

ORG = "org-perm-1"
ACTION = "drive.permissions.create"


def _row(pattern: str, *, role: str = "writer", link: bool = False, org: str = ORG):
    now = datetime.now(timezone.utc)
    return PermissionGrantRow(
        grant_id=uuid.uuid4(),
        org_id=org,
        action_class=ACTION,
        grantee_pattern=pattern,
        max_role=role,  # type: ignore[arg-type]
        allow_link_sharing=link,
        created_at=now,
        updated_at=now,
    )


async def _gate(*rows) -> PermissionGate:
    repo = InMemoryPermissionGrantRepository()
    for r in rows:
        await repo.insert(r)
    return PermissionGate(repo)


async def _authorize(gate, grantee: str, role: str = "reader"):
    return await gate.authorize(
        org_id=ORG, action_class=ACTION, grantee=grantee,
        role=role, link_sharing_sentinel="anyone",
    )


# ---------------------------------------------------------------------------
# Deny by default
# ---------------------------------------------------------------------------

async def test_empty_allow_list_denies() -> None:
    """The core stance: an org that pre-authorized nobody shares with nobody."""
    gate = await _gate()
    with pytest.raises(PermissionDeniedError, match="pre-authorized no recipient"):
        await _authorize(gate, "alice@example.com")


async def test_grant_for_a_different_action_class_does_not_authorize() -> None:
    repo = InMemoryPermissionGrantRepository()
    await repo.insert(replace(_row("example.com"), action_class="some.other.action"))
    gate = PermissionGate(repo)
    with pytest.raises(PermissionDeniedError):
        await _authorize(gate, "alice@example.com")


async def test_grant_belonging_to_another_org_does_not_authorize() -> None:
    gate = await _gate(_row("example.com", org="someone-else"))
    with pytest.raises(PermissionDeniedError):
        await _authorize(gate, "alice@example.com")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

async def test_exact_address_authorizes() -> None:
    gate = await _gate(_row("alice@example.com"))
    grant = await _authorize(gate, "alice@example.com")
    assert grant.grantee == "alice@example.com"
    assert grant.matched_pattern == "alice@example.com"


async def test_bare_domain_authorizes_any_address_at_that_domain() -> None:
    gate = await _gate(_row("example.com"))
    grant = await _authorize(gate, "bob@example.com")
    assert grant.matched_pattern == "example.com"


async def test_address_pattern_does_not_authorize_a_sibling_at_same_domain() -> None:
    """An exact-address rule is exact: it must not widen to the whole domain."""
    gate = await _gate(_row("alice@example.com"))
    with pytest.raises(PermissionDeniedError, match="matches no pre-authorized"):
        await _authorize(gate, "mallory@example.com")


async def test_domain_rule_does_not_authorize_a_lookalike_domain() -> None:
    """Substring/suffix confusion is the obvious attack: notexample.com must not
    match example.com, and example.com.evil.com must not either."""
    gate = await _gate(_row("example.com"))
    for hostile in ("eve@notexample.com", "eve@example.com.evil.com", "eve@evilexample.com"):
        with pytest.raises(PermissionDeniedError):
            await _authorize(gate, hostile)


async def test_matching_is_case_insensitive() -> None:
    gate = await _gate(_row("Example.COM"))
    grant = await _authorize(gate, "Alice@EXAMPLE.com")
    assert grant.role == "reader"


async def test_wildcard_is_not_a_pattern_language() -> None:
    """'*' is stored as a literal, not honoured as a glob — the column is
    deliberately not a pattern language."""
    gate = await _gate(_row("*"))
    with pytest.raises(PermissionDeniedError):
        await _authorize(gate, "anyone@anywhere.com")


async def test_malformed_grantee_is_unmatchable_not_permissive() -> None:
    gate = await _gate(_row("example.com"))
    for malformed in ("not-an-address", "a@b@example.com", "@example.com"):
        with pytest.raises(PermissionDeniedError):
            await _authorize(gate, malformed)


# ---------------------------------------------------------------------------
# Role ceiling
# ---------------------------------------------------------------------------

async def test_role_at_the_ceiling_authorizes() -> None:
    gate = await _gate(_row("example.com", role="commenter"))
    grant = await _authorize(gate, "a@example.com", role="commenter")
    assert grant.role == "commenter"


async def test_role_above_the_ceiling_denies_even_with_matching_recipient() -> None:
    """Matching the recipient does not buy an elevated role — the two are ANDed."""
    gate = await _gate(_row("example.com", role="reader"))
    with pytest.raises(PermissionDeniedError, match="exceeds the maximum"):
        await _authorize(gate, "a@example.com", role="writer")


async def test_unknown_role_is_unavailable_not_denied() -> None:
    """'Could not evaluate' must never be reported as 'operator refused'."""
    gate = await _gate(_row("example.com"))
    with pytest.raises(PermissionUnavailableError, match="unknown role"):
        await _authorize(gate, "a@example.com", role="owner")


# ---------------------------------------------------------------------------
# Link sharing — a distinct risk class, not a broader pattern
# ---------------------------------------------------------------------------

async def test_link_sharing_denied_when_not_explicitly_enabled() -> None:
    gate = await _gate(_row("example.com", role="writer", link=False))
    with pytest.raises(PermissionDeniedError, match="link sharing"):
        await _authorize(gate, "anyone")


async def test_address_rule_never_satisfies_a_link_grant() -> None:
    """Even a permissive address rule must not authorize 'anyone with the link':
    a link grant names no recipient at all."""
    gate = await _gate(_row("example.com", role="writer", link=False))
    with pytest.raises(PermissionDeniedError):
        await _authorize(gate, "anyone", role="reader")


async def test_link_sharing_authorized_when_enabled() -> None:
    gate = await _gate(_row("example.com", role="reader", link=True))
    grant = await _authorize(gate, "anyone", role="reader")
    assert grant.grantee == "anyone"


async def test_link_sharing_still_respects_the_role_ceiling() -> None:
    gate = await _gate(_row("example.com", role="reader", link=True))
    with pytest.raises(PermissionDeniedError, match="exceeds the maximum"):
        await _authorize(gate, "anyone", role="writer")


# ---------------------------------------------------------------------------
# Audit detail
# ---------------------------------------------------------------------------

async def test_exact_address_preferred_over_domain_in_matched_pattern() -> None:
    """The audit trail should name the narrowest rule that authorized the call."""
    gate = await _gate(
        _row("example.com", role="writer"),
        _row("alice@example.com", role="writer"),
    )
    grant = await _authorize(gate, "alice@example.com", role="writer")
    assert grant.matched_pattern == "alice@example.com"
