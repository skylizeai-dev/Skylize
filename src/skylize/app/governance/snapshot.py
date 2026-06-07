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
