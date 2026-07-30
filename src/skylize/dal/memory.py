"""
In-memory repository fakes — used by the `memory` backend and by tests.

These uphold the same Protocols as the asyncpg implementations, so the
Governance Authority, Audit service, and Orchestrator behave identically whether
backed by Postgres or by these. No driver import.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .ports import (
    AgentStateRow,
    ApiKeyRow,
    AuditRow,
    BudgetCeiling,
    DeliverableRow,
    HitlEscalation,
    HitlQueueItem,
    KillScope,
    RefreshTokenRow,
    TenantRow,
    TenantUserRow,
    TokenRow,
    UserRow,
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

    async def list_for_org(
        self, org_id: str, *, limit: int = 50, before: datetime | None = None
    ) -> list[AuditRow]:
        rows = [
            r for r in self.rows
            if r.org_id == org_id and (before is None or r.occurred_at < before)
        ]
        rows.sort(key=lambda r: r.occurred_at, reverse=True)
        return rows[:limit]


class InMemoryContractRepository:
    def __init__(self) -> None:
        self.contracts: dict[tuple[str, int], str] = {}

    async def upsert(self, agent_id: str, version: int, contract_json: str) -> None:
        self.contracts[(agent_id, version)] = contract_json

    async def load_all_active(self) -> list[tuple[str, str]]:
        latest: dict[str, tuple[int, str]] = {}
        for (agent_id, version), payload in self.contracts.items():
            if agent_id not in latest or version > latest[agent_id][0]:
                latest[agent_id] = (version, payload)
        return [(agent_id, payload) for agent_id, (_, payload) in sorted(latest.items())]

    async def get_latest_active(self, agent_id: str) -> str | None:
        versions = [
            (version, payload)
            for (aid, version), payload in self.contracts.items()
            if aid == agent_id
        ]
        if not versions:
            return None
        return max(versions)[1]


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


class InMemoryCapitalRepository:
    """In-memory budget ledger (memory backend + tests). Ceilings are seeded via
    `set_ceiling`; the evaluator reads them through the `CapitalRepository` port."""

    def __init__(self) -> None:
        self._ceilings: dict[tuple[str, str], BudgetCeiling] = {}

    def set_ceiling(self, ceiling: BudgetCeiling) -> None:
        self._ceilings[(ceiling.org_id, ceiling.scope)] = ceiling

    async def get_ceiling(self, org_id: str, scope: str) -> BudgetCeiling | None:
        return self._ceilings.get((org_id, scope))


class InMemoryProcessedEventStore:
    """In-memory idempotency guard for the async Decision Engine. Keyed by
    (org_id, key) to mirror the tenant-scoped Postgres implementation."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], str] = {}

    async def is_processed(self, key: str, *, org_id: str) -> bool:
        return (org_id, key) in self._seen

    async def mark_processed(self, key: str, outcome: str, *, org_id: str) -> None:
        self._seen[(org_id, key)] = outcome


class InMemoryHitlQueueRepository:
    """In-memory HITL escalation store (memory backend + tests). Mirrors the Pg
    implementation's contract: enqueue records the escalation; the review path
    reads it back as HitlQueueItem and claims verdicts with the same
    only-while-pending predicate. Tests still read raw escalations via `all`."""

    def __init__(self) -> None:
        self._rows: list[HitlEscalation] = []
        # hitl_id -> mutable verdict state mirroring the Pg row's lifecycle
        # columns: [status, verdict_by, verdict_json, verdict_at].
        self._state: dict[UUID, list[Any]] = {}

    async def enqueue(self, escalation: HitlEscalation) -> None:
        self._rows.append(escalation)
        self._state[escalation.hitl_id] = ["pending", None, None, None]

    def all(self) -> list[HitlEscalation]:
        return list(self._rows)

    def _item(self, e: HitlEscalation) -> HitlQueueItem:
        status, verdict_by, verdict_json, verdict_at = self._state[e.hitl_id]
        return HitlQueueItem(
            hitl_id=e.hitl_id,
            org_id=e.org_id,
            decision_id=e.decision_id,
            correlation_id=e.correlation_id,
            partition_key=e.partition_key,
            trigger_reason=e.trigger_reason,
            proposal_json=dict(e.proposal_json),
            request_json=dict(e.request_json) if e.request_json is not None else None,
            status=status,
            verdict_by=verdict_by,
            verdict_json=verdict_json,
            verdict_at=verdict_at,
            expires_at=e.expires_at,
            created_at=e.created_at,
        )

    async def list_pending(
        self, org_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[HitlQueueItem], int]:
        pending = [
            self._item(e)
            for e in self._rows
            if e.org_id == org_id and self._state[e.hitl_id][0] == "pending"
        ]
        pending.sort(key=lambda i: i.created_at, reverse=True)
        return pending[offset : offset + limit], len(pending)

    async def get(self, hitl_id: UUID, org_id: str) -> HitlQueueItem | None:
        for e in self._rows:
            if e.hitl_id == hitl_id and e.org_id == org_id:
                return self._item(e)
        return None

    async def claim(
        self,
        hitl_id: UUID,
        org_id: str,
        *,
        status_to: str,
        verdict_by: str,
        verdict_json: dict[str, Any],
        verdict_at: datetime,
        require_request: bool,
    ) -> HitlQueueItem | None:
        for e in self._rows:
            if e.hitl_id != hitl_id or e.org_id != org_id:
                continue
            state = self._state[e.hitl_id]
            if state[0] != "pending":
                return None
            if e.expires_at is not None and e.expires_at <= verdict_at:
                return None
            if require_request and e.request_json is None:
                return None
            self._state[e.hitl_id] = [status_to, verdict_by, dict(verdict_json), verdict_at]
            return self._item(e)
        return None

    async def release(self, hitl_id: UUID, org_id: str, *, from_status: str) -> bool:
        for e in self._rows:
            if e.hitl_id == hitl_id and e.org_id == org_id:
                if self._state[e.hitl_id][0] != from_status:
                    return False
                self._state[e.hitl_id] = ["pending", None, None, None]
                return True
        return False

    async def terminate(self, hitl_id: UUID, org_id: str, *, from_status: str) -> bool:
        """Terminal 'expired' for a permanently unreplayable row; verdict kept
        (mirrors PgHitlQueueRepository.terminate)."""
        for e in self._rows:
            if e.hitl_id == hitl_id and e.org_id == org_id:
                state = self._state[e.hitl_id]
                if state[0] != from_status:
                    return False
                state[0] = "expired"
                return True
        return False

    async def update_verdict_json(
        self, hitl_id: UUID, org_id: str, verdict_json: dict[str, Any]
    ) -> None:
        for e in self._rows:
            if e.hitl_id == hitl_id and e.org_id == org_id:
                self._state[e.hitl_id][2] = dict(verdict_json)
                return


class InMemoryUserRepository:
    """In-memory human-user store + refresh-token lifecycle (memory backend + tests)."""

    def __init__(self) -> None:
        self._users: dict[UUID, UserRow] = {}
        self._by_email: dict[str, UUID] = {}
        self._refresh: dict[UUID, RefreshTokenRow] = {}

    async def create_user(self, row: UserRow) -> None:
        self._users[row.user_id] = row
        self._by_email[row.email.lower()] = row.user_id

    async def create_owner_of_new_org(self, row: UserRow) -> bool:
        """Mirror of the Pg conditional insert (dal/users.py).

        The two guards there — "no user in this org" and "at most one owner per
        org" — are both applied, so the memory backend refuses exactly what
        Postgres refuses. There is no race to settle: this store is a plain dict
        mutated from a single event loop, so the check and the write cannot be
        interleaved. The Pg implementation needs migration 0017's unique index
        precisely because that is not true there.
        """
        if any(u.org_id == row.org_id for u in self._users.values()):
            return False
        if any(
            u.org_id == row.org_id and "owner" in u.roles for u in self._users.values()
        ):  # pragma: no cover - implied by the check above; kept for parity
            return False
        await self.create_user(row)
        return True

    async def get_by_email(self, email: str) -> UserRow | None:
        user_id = self._by_email.get(email.lower())
        return self._users.get(user_id) if user_id is not None else None

    async def get_by_id(self, user_id: UUID) -> UserRow | None:
        return self._users.get(user_id)

    async def list_by_org(self, org_id: str) -> list[UserRow]:
        return [u for u in self._users.values() if u.org_id == org_id]

    async def update_last_login(self, user_id: UUID, when: datetime) -> None:
        u = self._users.get(user_id)
        if u is not None:
            self._users[user_id] = replace(u, last_login_at=when)

    async def store_refresh_token(
        self, token_id: UUID, user_id: UUID, expires_at: datetime
    ) -> None:
        self._refresh[token_id] = RefreshTokenRow(
            token_id=token_id, user_id=user_id, expires_at=expires_at
        )

    async def get_refresh_token(self, token_id: UUID) -> RefreshTokenRow | None:
        return self._refresh.get(token_id)

    async def revoke_refresh_token(self, token_id: UUID) -> None:
        rt = self._refresh.get(token_id)
        if rt is not None:
            self._refresh[token_id] = replace(rt, revoked_at=datetime.now(timezone.utc))


class InMemoryDeliverableRepository:
    """In-memory deliverable store (memory backend + tests). Every read is
    `org_id`-scoped to uphold tenant isolation, same as the Pg implementation."""

    def __init__(self) -> None:
        self._rows: dict[UUID, DeliverableRow] = {}

    async def create(self, row: DeliverableRow) -> None:
        self._rows[row.id] = row

    async def get_by_id(self, id: UUID, org_id: str) -> DeliverableRow | None:
        row = self._rows.get(id)
        return row if row is not None and row.org_id == org_id else None

    async def list_by_org(
        self,
        org_id: str,
        *,
        status: str | None = None,
        deliverable_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DeliverableRow], int]:
        matched = [
            r
            for r in self._rows.values()
            if r.org_id == org_id
            and (status is None or r.status == status)
            and (deliverable_type is None or r.deliverable_type == deliverable_type)
        ]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        total = len(matched)
        return matched[offset : offset + limit], total

    async def update_status(self, id: UUID, org_id: str, status: str) -> bool:
        row = await self.get_by_id(id, org_id)
        if row is None:
            return False
        self._rows[id] = replace(row, status=status, updated_at=datetime.now(timezone.utc))
        return True

    async def update_approved(
        self, id: UUID, org_id: str, approved_by: str, approved_at: datetime
    ) -> bool:
        row = await self.get_by_id(id, org_id)
        if row is None:
            return False
        self._rows[id] = replace(
            row,
            status="approved",
            approved_by=approved_by,
            approved_at=approved_at,
            updated_at=datetime.now(timezone.utc),
        )
        return True

    async def list_versions(
        self, org_id: str, deliverable_id: UUID
    ) -> list[DeliverableRow]:
        root = await self.get_by_id(deliverable_id, org_id)
        if root is None:
            return []
        # Walk the parent chain up to the root, then collect the whole lineage.
        root_id = deliverable_id
        seen: set[UUID] = set()
        cursor: DeliverableRow | None = root
        while cursor is not None and cursor.parent_id is not None and cursor.parent_id not in seen:
            seen.add(cursor.id)
            root_id = cursor.parent_id
            cursor = self._rows.get(cursor.parent_id)

        lineage = [
            r
            for r in self._rows.values()
            if r.org_id == org_id and (r.id == root_id or _descends_from(r, root_id, self._rows))
        ]
        lineage.sort(key=lambda r: r.version)
        return lineage


def _descends_from(
    row: DeliverableRow, root_id: UUID, rows: dict[UUID, DeliverableRow]
) -> bool:
    seen: set[UUID] = set()
    cursor: DeliverableRow | None = row
    while cursor is not None and cursor.parent_id is not None and cursor.id not in seen:
        seen.add(cursor.id)
        if cursor.parent_id == root_id:
            return True
        cursor = rows.get(cursor.parent_id)
    return False
