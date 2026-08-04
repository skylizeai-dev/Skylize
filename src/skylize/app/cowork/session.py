"""Co-work session lifecycle — where mid-session revocation actually lands.

THE PROBLEM (Q3). A governance token is short-lived by design (5 minutes,
`Settings.token_ttl_minutes`). A co-work session is not: an employee may work
with their agent for hours. The naive fixes are both wrong:

  * a long-lived session token would mean a grant revoked at 09:05 keeps working
    until the session ends, which is the exact failure the whole principal
    kernel exists to prevent;
  * re-authenticating the human every 5 minutes is unusable.

THE RESOLUTION. The session is long; the TOKEN is short. `refresh()` re-mints,
and because `GovernanceAuthority.mint` already recompiles the principal's
authority through `_gate_principal_scope` -> `compile_authority` ->
`resolve_effective_scope` before signing, a refresh IS a re-authorization. A
grant revoked mid-session therefore surfaces at the next refresh: the new token
is either narrower, or the mint is refused outright with `AuthorityExceeded`.
Revocation lands at the next refresh, never at session end.

There are in fact TWO independent paths by which a revocation takes effect, and
the session is killed by whichever fires first:

  1. IMMEDIATELY, for tokens already in flight — the grant-write path calls
     `GovernanceAuthority.invalidate_principal_authority`, which drops the cached
     authority fingerprint and broadcasts that drop to every instance. The next
     tool call made with the OLD token fails the authority-freshness check in the
     revocation stage of `validate_tool_call`.
  2. AT THE NEXT REFRESH, for the session as a whole — this module.

Path 1 makes the exposure window "until the next call"; path 2 makes it "until
the next refresh" even if invalidation was somehow missed. Neither depends on the
session ending.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from ...contracts.base import AgentContract, GovernanceToken
from ..governance.authority import GovernanceAuthority
from ..principal.errors import AuthorityExceeded
from ..principal.provider import AuthorityProvider

#: A co-work token is deliberately shorter-lived than the platform default: it is
#: handed to an interactive surface, so the window in which a revoked grant could
#: still be honoured on an in-flight token is bounded by this rather than by how
#: long the human stays logged in.
COWORK_TOKEN_TTL_MINUTES = 5


class CoworkSessionService:
    """Mints and refreshes the short-lived token behind a long-lived session."""

    def __init__(
        self,
        *,
        authority: GovernanceAuthority,
        contract: AgentContract,
        principal_authority: AuthorityProvider,
    ) -> None:
        self._authority = authority
        self._contract = contract
        self._principal_authority = principal_authority

    async def start(
        self, *, org_id: str, principal_id: str, correlation_id: UUID | None = None
    ) -> GovernanceToken:
        """Open a session. Identical to `refresh` by construction — a session
        that starts must pass exactly the same authority gate a session that
        continues does, or the first 5 minutes would be privileged."""
        return await self._mint(
            org_id=org_id,
            principal_id=principal_id,
            correlation_id=correlation_id or uuid4(),
        )

    async def refresh(
        self, *, org_id: str, principal_id: str, correlation_id: UUID | None = None
    ) -> GovernanceToken:
        """Re-authorize an ongoing session.

        THIS IS THE REVOCATION POINT. It is not a token extension: the mint path
        recompiles the human's authority from their current grants, so a scope
        withdrawn since the last refresh is simply not in the new token — or, if
        the caller still demands it, the mint raises `AuthorityExceeded` and the
        session cannot continue with it.
        """
        return await self._mint(
            org_id=org_id,
            principal_id=principal_id,
            correlation_id=correlation_id or uuid4(),
        )

    async def _mint(
        self, *, org_id: str, principal_id: str, correlation_id: UUID
    ) -> GovernanceToken:
        """Request exactly the intersection of the contract manifest with what
        this human may actually do, then let the mint gate re-verify it.

        WHY THE INTERSECTION IS COMPUTED HERE. `mint` defaults an unspecified
        scope to the contract's FULL manifest and refuses loudly if any of it
        exceeds the principal's authority — correct for an explicit request,
        where a silent trim would be the "agent quietly did less than asked"
        failure. But a session is not an explicit request for the whole
        manifest: the manifest is a CEILING shared by every employee. Left to
        the default, the co-work agent would be unusable by anyone who does not
        personally hold every tool in it.

        So the session asks for what it is entitled to, and the mint gate still
        independently verifies that ask. Compiling the snapshot twice (once here,
        once inside mint) is deliberate: mint's recompilation is the
        authoritative one, and if a grant is withdrawn between the two, mint
        raises rather than honouring what this method computed a moment earlier.
        """
        snapshot = await self._principal_authority.snapshot_for(
            org_id=org_id,
            principal_id=principal_id,
            at=datetime.now(timezone.utc),
        )
        manifest = [grant.tool_id for grant in self._contract.allowed_tools]
        effective = sorted(set(manifest) & snapshot.scopes)
        if not effective:
            # Refuse loudly. A session holding no tools at all is not a degraded
            # session, it is an unauthorized one.
            raise AuthorityExceeded(
                requested=sorted(manifest),
                ceiling=[],
                excess=sorted(manifest),
                principal_id=principal_id,
            )
        return await self._authority.mint(
            self._contract,
            org_id=org_id,
            correlation_id=correlation_id,
            scope=effective,
            on_behalf_of_principal=principal_id,
            session_kind="cowork",
            ttl_minutes=COWORK_TOKEN_TTL_MINUTES,
        )
