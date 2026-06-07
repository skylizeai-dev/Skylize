"""
In-memory repository fakes — used by the `memory` backend and by tests.

These uphold the same Protocols as the asyncpg implementations, so the
Governance Authority, Audit service, and Orchestrator behave identically whether
backed by Postgres or by these. No driver import.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from .ports import (
    AgentStateRow,
    ApiKeyRow,
    AuditRow,
    KillScope,
    TenantRow,
    TenantUserRow,
    TokenRow,
)


class InMemoryGovernanceRepository:
    def __init__(self) -> None:
        self._tokens: dict[UUID, TokenRow] = {}
        self._revoked: dict[UUID, tuple[str, datetime]] = {}
        self._agent_state: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._cb_trips: dict[tuple[str, str], int] = {}
        self._kill: dict[tuple[str, str, str], tuple[str, str]] = {}

    async def insert_token(self, row: TokenRow) -> None:
        self._tokens[row.token_id] = row

    async def revoke_token(self, token_id: UUID, reason: str, when: datetime) -> None:
        self._revoked[token_id] = (reason, when)

    async def is_token_revoked(self, token_id: UUID) -> bool:
        return token_id in self._revoked

    async def set_agent_state(
        self, agent_id: str, org_id: str, state: str, reason: str | None
    ) -> None:
        self._agent_state[(agent_id, org_id)] = (state, reason)

    async def get_agent_state(self, agent_id: str, org_id: str) -> str:
        return self._agent_state.get((agent_id, org_id), ("active", None))[0]

    async def increment_circuit_breaker(self, agent_id: str, org_id: str) -> int:
        key = (agent_id, org_id)
        self._cb_trips[key] = self._cb_trips.get(key, 0) + 1
        return self._cb_trips[key]

    async def engage_kill_switch(
        self, scope: KillScope, engaged_by: str, reason: str
    ) -> None:
        self._kill[(scope.scope_type, scope.scope_id, scope.org_id)] = (engaged_by, reason)

    async def disengage_kill_switch(self, scope: KillScope, disengaged_by: str) -> None:
        self._kill.pop((scope.scope_type, scope.scope_id, scope.org_id), None)

    async def active_kill_scopes(self, org_id: str) -> list[KillScope]:
        out: list[KillScope] = []
        for (stype, sid, oid) in self._kill:
            if oid == org_id or stype == "platform":
                out.append(KillScope(scope_type=stype, scope_id=sid, org_id=oid))
        return out

    # -- rehydration reads --------------------------------------------------
    async def revoked_token_ids(self) -> list[UUID]:
        return list(self._revoked.keys())

    async def non_active_agents(self) -> list[AgentStateRow]:
        return [
            AgentStateRow(agent_id=agent, org_id=org, state=state)
            for (agent, org), (state, _reason) in self._agent_state.items()
            if state != "active"
        ]

    async def all_active_kill_scopes(self) -> list[KillScope]:
        return [
            KillScope(scope_type=stype, scope_id=sid, org_id=oid)
            for (stype, sid, oid) in self._kill
        ]


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.rows: list[AuditRow] = []

    async def append(self, row: AuditRow) -> None:
        self.rows.append(row)


class InMemoryContractRepository:
    def __init__(self) -> None:
        self.contracts: dict[tuple[str, int], str] = {}

    async def upsert(self, agent_id: str, version: int, contract_json: str) -> None:
        self.contracts[(agent_id, version)] = contract_json


class InMemoryTenantRepository:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantRow] = {}
        self._users: dict[tuple[str, str], TenantUserRow] = {}  # (user_id, org_id)

    async def create_tenant(self, row: TenantRow) -> None:
        self._tenants[row.org_id] = row

    async def get_tenant(self, org_id: str) -> TenantRow | None:
        return self._tenants.get(org_id)

    async def set_status(self, org_id: str, status: str) -> None:
        current = self._tenants[org_id]
        self._tenants[org_id] = replace(
            current, status=status, updated_at=datetime.now(timezone.utc)
        )

    async def add_user(self, row: TenantUserRow) -> None:
        self._users[(row.user_id, row.org_id)] = row

    async def get_user(self, user_id: str, org_id: str) -> TenantUserRow | None:
        return self._users.get((user_id, org_id))

    async def list_users(self, org_id: str) -> list[TenantUserRow]:
        return [u for (_, oid), u in self._users.items() if oid == org_id]

    async def remove_user(self, user_id: str, org_id: str) -> None:
        self._users.pop((user_id, org_id), None)


class InMemoryApiKeyRepository:
    def __init__(self) -> None:
        self._keys: dict[UUID, ApiKeyRow] = {}

    async def insert(self, row: ApiKeyRow) -> None:
        self._keys[row.key_id] = row

    async def get_by_prefix(self, prefix: str) -> ApiKeyRow | None:
        return next((k for k in self._keys.values() if k.prefix == prefix), None)

    async def list_for_org(self, org_id: str) -> list[ApiKeyRow]:
        return [k for k in self._keys.values() if k.org_id == org_id]

    async def revoke(self, key_id: UUID, org_id: str, reason: str, when: datetime) -> None:
        k = self._keys.get(key_id)
        if k is None or k.org_id != org_id:
            return
        self._keys[key_id] = replace(k, revoked_at=when, revocation_reason=reason)

    async def touch_last_used(self, key_id: UUID, when: datetime) -> None:
        k = self._keys.get(key_id)
        if k is not None:
            self._keys[key_id] = replace(k, last_used_at=when)
