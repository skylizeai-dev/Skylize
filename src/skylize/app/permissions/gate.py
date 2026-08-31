"""The elevated-action permission gate — org pre-authorization for sharing.

Backs `ToolPermissionProfile`, the third opt-in `ToolProxy` stage
(integration_inputs.md 2.5, Q2.5d). Nothing here is Drive-specific: the gate
knows about an `action_class`, a grantee, and a role. Drive supplies those three
through its tool's profile; a future connector supplies its own.

THE DENIAL ASYMMETRY, which is the security core of this module:
  * An empty allow-list DENIES. An org that has pre-authorized nobody can share
    with nobody. Absence is never an implicit allow — the same stance migration
    0014 takes on a missing spend ceiling.
  * A link grant ('anyone with the link') is NOT an address pattern and is never
    satisfied by one. It needs `allow_link_sharing` explicitly set on a matching
    row, because it names no recipient at all.
  * A role above the org's `max_role` DENIES even when the grantee matches. The
    two are ANDed; matching the recipient does not buy an elevated role.

Matching is EXACT STRING COMPARISON, never a regex, glob, or SQL `LIKE`. A
pattern language on the value that decides who receives a customer's documents is
an injection surface and an operator footgun. A row is either an exact address
(`alice@example.com`) or a bare domain (`example.com`, matching any address at
that domain and no other).
"""

from __future__ import annotations

import structlog

from ...dal.permission_grants import (
    ROLE_RANK,
    PermissionGrantRepository,
    PermissionGrantRow,
)
from ...tools.base import PermissionGrant

log = structlog.get_logger()


class PermissionDeniedError(Exception):
    """The allow-list refused this grantee/role combination."""


class PermissionUnavailableError(Exception):
    """The request could not be evaluated — fail closed, do not claim a refusal."""


def _normalize(value: str) -> str:
    return value.strip().lower()


def _domain_of(address: str) -> str | None:
    """The domain part of an address, or None if it is not one address.

    Deliberately strict: exactly one '@', with a non-empty local part and a
    non-empty domain part. An input like 'a@b@c' or '@example.com' is not an
    address this gate will reason about, and returning None makes it unmatchable
    rather than accidentally matching a domain rule.
    """
    parts = address.split("@")
    if len(parts) != 2:
        return None
    local, domain = parts
    if not local or not domain:
        return None
    return domain


def _matches(pattern: str, grantee: str) -> bool:
    """Exact address match, or bare-domain match against the grantee's domain."""
    pattern = _normalize(pattern)
    grantee = _normalize(grantee)
    if pattern == grantee:
        return True
    if "@" in pattern:
        # An address pattern only ever matches that exact address.
        return False
    domain = _domain_of(grantee)
    return domain is not None and domain == pattern


class PermissionGate:
    """Evaluates one elevated action against the org's pre-authorization rows."""

    def __init__(self, repo: PermissionGrantRepository) -> None:
        self._repo = repo

    async def authorize(
        self,
        *,
        org_id: str,
        action_class: str,
        grantee: str,
        role: str,
        link_sharing_sentinel: str,
    ) -> PermissionGrant:
        """Return a `PermissionGrant` or raise. There is no permissive return path.

        Raises `PermissionDeniedError` when the allow-list refuses, and
        `PermissionUnavailableError` when the request is unevaluable (an unknown
        role, say) — never conflating "refused" with "could not check".
        """
        requested_role = _normalize(role)
        if requested_role not in ROLE_RANK:
            raise PermissionUnavailableError(
                f"unknown role {role!r} for {action_class!r}; expected one of "
                f"{sorted(ROLE_RANK)}"
            )

        rows = await self._repo.list_for_action(org_id, action_class)
        if not rows:
            raise PermissionDeniedError(
                f"org {org_id!r} has pre-authorized no recipient for "
                f"{action_class!r}; absence of a grant is a denial, not an allow"
            )

        grantee_norm = _normalize(grantee)
        is_link_grant = grantee_norm == _normalize(link_sharing_sentinel)

        if is_link_grant:
            return self._authorize_link_grant(
                rows, org_id=org_id, action_class=action_class,
                requested_role=requested_role, grantee=grantee_norm,
            )
        return self._authorize_addressed_grant(
            rows, org_id=org_id, action_class=action_class,
            requested_role=requested_role, grantee=grantee_norm,
        )

    def _authorize_link_grant(
        self,
        rows: list[PermissionGrantRow],
        *,
        org_id: str,
        action_class: str,
        requested_role: str,
        grantee: str,
    ) -> PermissionGrant:
        """A link grant names no recipient, so no address pattern can satisfy it."""
        enabled = [r for r in rows if r.allow_link_sharing]
        if not enabled:
            raise PermissionDeniedError(
                f"link sharing ('anyone with the link') is not enabled for org "
                f"{org_id!r} on {action_class!r}. An address rule does not permit "
                f"it: a link grant names no recipient and is a distinct risk class"
            )
        permitted = [r for r in enabled if ROLE_RANK[r.max_role] >= ROLE_RANK[requested_role]]
        if not permitted:
            best = max(ROLE_RANK[r.max_role] for r in enabled)
            best_name = next(k for k, v in ROLE_RANK.items() if v == best)
            raise PermissionDeniedError(
                f"link sharing at role {requested_role!r} exceeds the maximum "
                f"{best_name!r} pre-authorized for org {org_id!r} on {action_class!r}"
            )
        row = permitted[0]
        log.info(
            "permission.authorized",
            org_id=org_id, action_class=action_class,
            grantee="anyone", role=requested_role,
        )
        return PermissionGrant(
            action_class=action_class, grantee=grantee,
            role=requested_role, matched_pattern=row.grantee_pattern,
        )

    def _authorize_addressed_grant(
        self,
        rows: list[PermissionGrantRow],
        *,
        org_id: str,
        action_class: str,
        requested_role: str,
        grantee: str,
    ) -> PermissionGrant:
        matching = [r for r in rows if _matches(r.grantee_pattern, grantee)]
        if not matching:
            raise PermissionDeniedError(
                f"recipient {grantee!r} matches no pre-authorized pattern for org "
                f"{org_id!r} on {action_class!r}"
            )
        permitted = [
            r for r in matching if ROLE_RANK[r.max_role] >= ROLE_RANK[requested_role]
        ]
        if not permitted:
            best = max(ROLE_RANK[r.max_role] for r in matching)
            best_name = next(k for k, v in ROLE_RANK.items() if v == best)
            raise PermissionDeniedError(
                f"role {requested_role!r} for recipient {grantee!r} exceeds the "
                f"maximum {best_name!r} pre-authorized for org {org_id!r} on "
                f"{action_class!r}"
            )
        # Prefer the most specific rule that authorized it: an exact address
        # beats a domain rule, so the audit trail names the narrowest grant.
        permitted.sort(key=lambda r: ("@" not in r.grantee_pattern, r.grantee_pattern))
        row = permitted[0]
        log.info(
            "permission.authorized",
            org_id=org_id, action_class=action_class,
            grantee=grantee, role=requested_role, matched=row.grantee_pattern,
        )
        return PermissionGrant(
            action_class=action_class, grantee=grantee,
            role=requested_role, matched_pattern=row.grantee_pattern,
        )
