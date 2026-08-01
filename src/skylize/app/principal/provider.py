"""Loading a principal's authority — the seam between the pure kernel and storage.

`authority.py` is deliberately pure: it compiles grants into a snapshot and can
be tested exhaustively without a database. This module is the thin I/O layer that
feeds it, and the seam `GovernanceAuthority.mint` depends on.

FAIL-CLOSED IS THE RULE HERE. An unknown principal, a suspended one, or a missing
repository are all denials. A human whose authority cannot be established has no
authority — never "no restrictions".
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from .authority import compile_authority
from .errors import PrincipalNotFound
from .models import AuthoritySnapshot, Grant, Principal


@runtime_checkable
class PrincipalRepository(Protocol):
    """Read port over `principal` / `principal_grant` (migration 0019).

    Every method takes `org_id` explicitly — there is no unscoped read, matching
    the convention every other repository in this codebase follows.
    """

    async def load_principal(
        self, *, org_id: str, principal_id: str
    ) -> Principal | None: ...

    async def load_grants(
        self, *, org_id: str, principal_id: str
    ) -> Sequence[Grant]: ...


@runtime_checkable
class AuthorityProvider(Protocol):
    """What `GovernanceAuthority.mint` needs in order to gate a principal's scope."""

    async def snapshot_for(
        self, *, org_id: str, principal_id: str, at: datetime
    ) -> AuthoritySnapshot: ...


class PrincipalAuthorityService:
    """Compiles a principal's effective authority at an instant.

    Thin by design: the resolution rules (deny-wins, effective dating, suspension)
    all live in the pure `compile_authority`, so they stay exhaustively testable
    without a database. This class only fetches and delegates.
    """

    def __init__(self, repo: PrincipalRepository) -> None:
        self._repo = repo

    async def snapshot_for(
        self, *, org_id: str, principal_id: str, at: datetime
    ) -> AuthoritySnapshot:
        """The principal's effective authority at `at`.

        Raises `PrincipalNotFound` when no such principal exists in this org, and
        `PrincipalSuspended` (from `compile_authority`) when they are deactivated.
        Both are denials: absence of a principal record is never a grant.
        """
        principal = await self._repo.load_principal(
            org_id=org_id, principal_id=principal_id
        )
        if principal is None:
            raise PrincipalNotFound(
                f"no principal {principal_id!r} in org {org_id!r}; a token cannot "
                f"be minted on behalf of a human this platform does not know"
            )
        grants = await self._repo.load_grants(org_id=org_id, principal_id=principal_id)
        return compile_authority(principal, grants, at=at)


class InMemoryPrincipalRepository:
    """In-memory principal store (memory backend + tests).

    Upholds the same port as the Postgres implementation, including the org
    scoping — a principal registered under org A is invisible to org B, which is
    what the RLS policy enforces on the durable side.
    """

    def __init__(self) -> None:
        self._principals: dict[tuple[str, str], Principal] = {}
        self._grants: dict[tuple[str, str], list[Grant]] = {}

    def add_principal(self, principal: Principal) -> None:
        self._principals[(principal.org_id, principal.principal_id)] = principal

    def add_grant(self, *, org_id: str, principal_id: str, grant: Grant) -> None:
        self._grants.setdefault((org_id, principal_id), []).append(grant)

    async def load_principal(
        self, *, org_id: str, principal_id: str
    ) -> Principal | None:
        return self._principals.get((org_id, principal_id))

    async def load_grants(
        self, *, org_id: str, principal_id: str
    ) -> Sequence[Grant]:
        return list(self._grants.get((org_id, principal_id), []))
