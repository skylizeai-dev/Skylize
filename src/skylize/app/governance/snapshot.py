"""
In-memory governance snapshot — the hot path for token validation.

kill_switch_protocol.md §5: rejection is "local to each proxy/adapter using the
cached revocation set + live-state", effective in milliseconds. This is that
cache. The Authority mutates it synchronously on mint/revoke/suspend/kill and
also persists to the DB for durability. Department-scope kills are expanded to
per-agent entries at engage time so the lookup stays O(1) and needs no
department argument.
"""

from __future__ import annotations

from uuid import UUID


class GovernanceSnapshot:
    def __init__(self) -> None:
        self._revoked: set[UUID] = set()
        self._agent_state: dict[tuple[str, str], str] = {}  # (agent,org) -> suspended|killed
        self._killed_tenants: set[str] = set()
        self._platform_killed: bool = False
        # (org, principal) -> the fingerprint of that human's CURRENT authority.
        # Populated at mint (which has just compiled it anyway) and dropped when
        # their grants change, so a live token minted under the old authority is
        # detectable without a per-call permission join.
        self._authority: dict[tuple[str, str], str] = {}

    # -- mutations ----------------------------------------------------------
    def revoke(self, token_id: UUID) -> None:
        self._revoked.add(token_id)

    def set_agent_state(self, agent_id: str, org_id: str, state: str) -> None:
        if state == "active":
            self._agent_state.pop((agent_id, org_id), None)
        else:
            self._agent_state[(agent_id, org_id)] = state

    def kill_tenant(self, org_id: str) -> None:
        self._killed_tenants.add(org_id)

    def unkill_tenant(self, org_id: str) -> None:
        self._killed_tenants.discard(org_id)

    def kill_platform(self) -> None:
        self._platform_killed = True

    def unkill_platform(self) -> None:
        self._platform_killed = False

    def set_authority_fingerprint(
        self, org_id: str, principal_id: str, fingerprint: str
    ) -> None:
        """Record a human's current authority fingerprint (called at mint)."""
        self._authority[(org_id, principal_id)] = fingerprint

    def forget_authority(self, org_id: str, principal_id: str) -> None:
        """Drop a cached fingerprint because that human's grants changed.

        Dropping rather than overwriting is deliberate and is what makes
        revocation fail CLOSED: every token minted under the old authority now
        hits an unknown entry and is refused, including the case where we cannot
        recompute the new value here. Overwriting would leave a window in which a
        stale value still matched a live token.
        """
        self._authority.pop((org_id, principal_id), None)

    # -- lookup -------------------------------------------------------------
    def reason_for(self, token_id: UUID | None, agent_id: str, org_id: str) -> str | None:
        """Return the deny reason if this token/agent is not live, else None.

        Order: platform kill > tenant kill > agent suspend/kill > token revoked.
        Kill-switch state overrides all authority (agent_governance.md §8).
        """
        if self._platform_killed:
            return "platform kill switch engaged"
        if org_id in self._killed_tenants:
            return "tenant kill switch engaged"
        state = self._agent_state.get((agent_id, org_id))
        if state == "killed":
            return "agent kill switch engaged"
        if state == "suspended":
            return "agent suspended (circuit breaker)"
        if token_id is not None and token_id in self._revoked:
            return "token revoked"
        return None

    def authority_stale_reason(
        self, org_id: str, principal_id: str, fingerprint: str
    ) -> str | None:
        """Deny reason if this token's authority claim is no longer current.

        FAIL CLOSED ON A MISS. An absent entry means we cannot establish that the
        human still holds what the token says they held, and "we do not know" must
        deny — this is the hot validation path, which is synchronous and cannot
        reach the database to find out.

        A miss is rare by construction rather than by luck: minting a principal
        token populates the entry (the Authority has just compiled the snapshot
        to gate the scope), so an entry exists for the whole life of any token
        that was legitimately issued by a running instance. The two ways to miss
        are a grant change (the point) and a process restart before rehydrate,
        both of which SHOULD deny until the authority is re-established.
        """
        current = self._authority.get((org_id, principal_id))
        if current is None:
            return (
                f"authority for principal {principal_id!r} is not established on "
                f"this instance; refusing to honour a principal-bound token whose "
                f"authority cannot be confirmed"
            )
        if current != fingerprint:
            return (
                f"authority for principal {principal_id!r} changed since this "
                f"token was minted (token={fingerprint[:12]}..., "
                f"current={current[:12]}...)"
            )
        return None
