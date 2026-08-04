"""
Principal authority — the attenuation kernel.

Everything in this module is a PURE FUNCTION over frozensets. No I/O, no clock
reads except the injected `at`, no LLM. That is intentional: this is the code a
security reviewer will read line by line during due diligence, and it must be
exhaustively testable without a database.

THE ONE INVARIANT
-----------------
    effective_scope  ⊆  contract.allowed_tools  ∩  principal.grants  ∩  parent.scope

Most restrictive wins (agent_governance.md §12.3). Authority only ever narrows as
it flows down: human -> their agent -> that agent's sub-agents. There is no code
path in this module that can widen a scope set, which is why the property is
provable rather than merely intended.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Iterable

from .errors import (
    AuthorityExceeded,
    ExpiryExtensionDenied,
    PrincipalSuspended,
    StaleAuthority,
)
from .models import (
    AuthoritySnapshot,
    Grant,
    GrantSource,
    OnBehalfOf,
    Principal,
    ScopeId,
)


def fingerprint_scopes(org_id: str, principal_id: str, scopes: Iterable[ScopeId]) -> str:
    """Stable identity of an authority set.

    Embedded in the token so a verifier can detect that the principal's authority
    changed since mint, using a cheap string compare against a cached snapshot
    instead of a per-call permission join.
    """
    canonical = "\x1f".join([org_id, principal_id, *sorted(set(scopes))])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_authority(
    principal: Principal,
    grants: Iterable[Grant],
    *,
    at: datetime,
) -> AuthoritySnapshot:
    """Collapse effective-dated grants into the principal's authority at `at`.

    Resolution order (deterministic, no policy engine required):
      1. Take every ACTIVE grant whose source is POSITION, GROUP, or EXPLICIT_GRANT.
      2. Subtract every ACTIVE EXPLICIT_DENY.

    Deny-wins is not negotiable: it is the only way to express segregation of
    duties ("the AP clerk may not also approve payments") without mutating the
    org chart, and the only mechanism that makes an emergency scope revocation a
    single INSERT rather than a reorg.
    """
    if principal.is_suspended:
        # Fail closed. A suspended human's agents lose authority immediately —
        # this mirrors "kill-switch overrides all authority" (§12.5).
        raise PrincipalSuspended(
            f"principal {principal.principal_id!r} suspended at "
            f"{principal.suspended_at!r}"
        )

    active = [g for g in grants if g.is_active_at(at)]
    allowed: set[ScopeId] = {
        g.scope for g in active if g.source is not GrantSource.EXPLICIT_DENY
    }
    denied: set[ScopeId] = {
        g.scope for g in active if g.source is GrantSource.EXPLICIT_DENY
    }
    effective = frozenset(allowed - denied)

    return AuthoritySnapshot(
        principal_id=principal.principal_id,
        org_id=principal.org_id,
        scopes=effective,
        computed_at=at,
        fingerprint=fingerprint_scopes(
            principal.org_id, principal.principal_id, effective
        ),
    )


def resolve_effective_scope(
    *,
    requested: Iterable[ScopeId],
    contract_tools: Iterable[ScopeId],
    snapshot: AuthoritySnapshot,
    parent_scope: Iterable[ScopeId] | None = None,
) -> frozenset[ScopeId]:
    """The mint-time gate. Called by `GovernanceAuthority.mint()` BEFORE signing.

    Raises `AuthorityExceeded` naming the exact offending scopes — never silently
    trims. Silent trimming is the failure mode that produces "the agent quietly
    did less than asked and nobody noticed"; a loud denial is cheaper to debug and
    is the event a buyer wants to see in the audit trail.
    """
    req = frozenset(requested)
    ceiling = frozenset(contract_tools) & snapshot.scopes
    if parent_scope is not None:
        ceiling &= frozenset(parent_scope)

    excess = req - ceiling
    if excess:
        raise AuthorityExceeded(
            requested=sorted(req),
            ceiling=sorted(ceiling),
            excess=sorted(excess),
            principal_id=snapshot.principal_id,
        )
    return req


def attenuate_for_subagent(
    *,
    parent_scope: Iterable[ScopeId],
    parent_expires_at: datetime,
    child_contract_tools: Iterable[ScopeId],
    child_requested: Iterable[ScopeId],
    child_expires_at: datetime,
    snapshot: AuthoritySnapshot,
) -> frozenset[ScopeId]:
    """Delegation from an agent to a sub-agent (executive -> vp -> ... -> worker).

    Two things narrow, never widen: the scope set, and the validity window. The
    expiry check matters more than it looks — without it a worker agent could be
    handed a token outliving its parent's run and keep acting after the parent was
    already killed.
    """
    if child_expires_at > parent_expires_at:
        raise ExpiryExtensionDenied(
            f"sub-agent expiry {child_expires_at.isoformat()} exceeds parent "
            f"{parent_expires_at.isoformat()}"
        )
    return resolve_effective_scope(
        requested=child_requested,
        contract_tools=child_contract_tools,
        snapshot=snapshot,
        parent_scope=parent_scope,
    )


def assert_snapshot_current(
    claim: OnBehalfOf,
    snapshot: AuthoritySnapshot,
) -> None:
    """Verification-time check, run by the tool proxy on every side-effecting call.

    A token minted under authority that has since changed is refused. Cost is one
    string comparison against a cached snapshot; the cache is invalidated by the
    grant-write path, so a revocation propagates at the next call rather than at
    token expiry.
    """
    if claim.principal_id != snapshot.principal_id:
        raise StaleAuthority(
            f"token principal {claim.principal_id!r} != snapshot "
            f"{snapshot.principal_id!r}"
        )
    if claim.authority_fingerprint != snapshot.fingerprint:
        raise StaleAuthority(
            f"authority changed since mint for {claim.principal_id!r} "
            f"(token={claim.authority_fingerprint[:12]}…, "
            f"current={snapshot.fingerprint[:12]}…)"
        )
