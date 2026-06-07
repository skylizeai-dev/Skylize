"""
asyncpg implementations of the repository ports.

SQL lives ONLY here (and in migrations). Governance/audit tables are RLS-scoped,
so writes/reads go through `Database.tenant_session(org_id)`; the
`agent_contracts` table is platform-level and uses `admin_session`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from .connection import Database
from .ports import (
    AgentStateRow,
    ApiKeyRow,
    AuditRow,
    KillScope,
    TenantRow,
    TenantUserRow,
    TokenRow,
)


class PgGovernanceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_token(self, row: TokenRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO governance_tokens (
                    token_id, agent_id, org_id, authority_level, department, scope,
                    max_token_budget, max_execution_time_seconds,
                    issued_at, expires_at, correlation_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                row.token_id, row.agent_id, row.org_id, row.authority_level,
                row.department, row.scope, row.max_token_budget,
                row.max_execution_time_seconds, row.issued_at, row.expires_at,
                row.correlation_id,
            )

    async def revoke_token(self, token_id: UUID, reason: str, when: datetime) -> None:
        # token_id is unique; org binding enforced by the caller's session context.
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE governance_tokens SET revoked_at=$2, revocation_reason=$3 "
                "WHERE token_id=$1",
                token_id, when, reason,
            )

    async def is_token_revoked(self, token_id: UUID) -> bool:
        async with self._db.admin_session() as conn:
            val = await conn.fetchval(
                "SELECT revoked_at IS NOT NULL FROM governance_tokens WHERE token_id=$1",
                token_id,
            )
            return bool(val)

    async def set_agent_state(
        self, agent_id: str, org_id: str, state: str, reason: str | None
    ) -> None:
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                INSERT INTO agent_live_state (agent_id, org_id, state, reason, updated_at)
                VALUES ($1,$2,$3,$4, now())
                ON CONFLICT (agent_id, org_id)
                DO UPDATE SET state=EXCLUDED.state, reason=EXCLUDED.reason, updated_at=now()
                """,
                agent_id, org_id, state, reason,
            )

    async def get_agent_state(self, agent_id: str, org_id: str) -> str:
        async with self._db.tenant_session(org_id) as conn:
            val = await conn.fetchval(
                "SELECT state FROM agent_live_state WHERE agent_id=$1 AND org_id=$2",
                agent_id, org_id,
            )
            return val or "active"

    async def increment_circuit_breaker(self, agent_id: str, org_id: str) -> int:
        async with self._db.tenant_session(org_id) as conn:
            trips: int = await conn.fetchval(
                """
                INSERT INTO agent_live_state (agent_id, org_id, circuit_breaker_trips, updated_at)
                VALUES ($1,$2,1, now())
                ON CONFLICT (agent_id, org_id)
                DO UPDATE SET circuit_breaker_trips = agent_live_state.circuit_breaker_trips + 1,
                              updated_at = now()
                RETURNING circuit_breaker_trips
                """,
                agent_id, org_id,
            )
            return trips

    async def engage_kill_switch(
        self, scope: KillScope, engaged_by: str, reason: str
    ) -> None:
        async with self._db.tenant_session(scope.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO kill_switch_state
                    (scope_type, scope_id, org_id, engaged_at, engaged_by, reason)
                VALUES ($1,$2,$3, now(), $4, $5)
                ON CONFLICT (scope_type, scope_id, org_id)
                DO UPDATE SET engaged_at=now(), engaged_by=EXCLUDED.engaged_by,
                              reason=EXCLUDED.reason, disengaged_at=NULL
                """,
                scope.scope_type, scope.scope_id, scope.org_id, engaged_by, reason,
            )

    async def disengage_kill_switch(self, scope: KillScope, disengaged_by: str) -> None:
        async with self._db.tenant_session(scope.org_id) as conn:
            await conn.execute(
                "UPDATE kill_switch_state SET disengaged_at=now() "
                "WHERE scope_type=$1 AND scope_id=$2 AND org_id=$3",
                scope.scope_type, scope.scope_id, scope.org_id,
            )

    async def active_kill_scopes(self, org_id: str) -> list[KillScope]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                "SELECT scope_type, scope_id, org_id FROM kill_switch_state "
                "WHERE disengaged_at IS NULL AND engaged_at IS NOT NULL",
            )
            return [
                KillScope(scope_type=r["scope_type"], scope_id=r["scope_id"], org_id=r["org_id"])
                for r in rows
            ]

    # -- rehydration reads (platform-wide; RLS-bypass session) --------------
    # These warm the in-memory snapshot at Authority startup and must see EVERY
    # tenant's state, so they run in a rehydration session that sets RLS bypass
    # (migration 0002 grants the app role a `skylize.rehydrate` carve-out). This
    # is read-only and only ever called at startup, never on a request path.
    async def revoked_token_ids(self) -> list[UUID]:
        async with self._db.rehydration_session() as conn:
            rows = await conn.fetch(
                "SELECT token_id FROM governance_tokens WHERE revoked_at IS NOT NULL"
            )
            return [r["token_id"] for r in rows]

    async def non_active_agents(self) -> list[AgentStateRow]:
        async with self._db.rehydration_session() as conn:
            rows = await conn.fetch(
                "SELECT agent_id, org_id, state FROM agent_live_state WHERE state <> 'active'"
            )
            return [
                AgentStateRow(agent_id=r["agent_id"], org_id=r["org_id"], state=r["state"])
                for r in rows
            ]

    async def all_active_kill_scopes(self) -> list[KillScope]:
        async with self._db.rehydration_session() as conn:
            rows = await conn.fetch(
                "SELECT scope_type, scope_id, org_id FROM kill_switch_state "
                "WHERE disengaged_at IS NULL AND engaged_at IS NOT NULL"
            )
            return [
                KillScope(scope_type=r["scope_type"], scope_id=r["scope_id"], org_id=r["org_id"])
                for r in rows
            ]


class PgAuditRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, row: AuditRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    event_id, org_id, tenant_id, correlation_id, causation_id,
                    source_agent_id, authority_level, governance_token_id,
                    action_type, inputs_hash, outputs_hash, result, result_reason,
                    occurred_at
                ) VALUES ($1,$2,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                row.event_id, row.org_id, row.correlation_id, row.causation_id,
                row.source_agent_id, row.authority_level, row.governance_token_id,
                row.action_type, row.inputs_hash, row.outputs_hash, row.result,
                row.result_reason, row.occurred_at,
            )


class PgContractRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(self, agent_id: str, version: int, contract_json: str) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO agent_contracts (agent_id, version, contract_json, is_active)
                VALUES ($1,$2,$3,true)
                ON CONFLICT (agent_id, version)
                DO UPDATE SET contract_json=EXCLUDED.contract_json, is_active=true
                """,
                agent_id, version, contract_json,
            )


# ---------------------------------------------------------------------------
# Tenant & Auth (Subsystem 1)
#
# tenants / tenant_users / api_keys are platform-level (no RLS — see migrations
# 0001 and 0004), so they go through `admin_session` (no org binding). Tenant
# isolation for management is enforced by the explicit `org_id` filters below.
# ---------------------------------------------------------------------------

class PgTenantRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_tenant(self, row: TenantRow) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO tenants (org_id, display_name, oidc_issuer, status,
                                     created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                row.org_id, row.display_name, row.oidc_issuer, row.status,
                row.created_at, row.updated_at,
            )

    async def get_tenant(self, org_id: str) -> TenantRow | None:
        async with self._db.admin_session() as conn:
            r = await conn.fetchrow(
                "SELECT org_id, display_name, oidc_issuer, status, created_at, updated_at "
                "FROM tenants WHERE org_id=$1",
                org_id,
            )
            if r is None:
                return None
            return TenantRow(
                org_id=r["org_id"], display_name=r["display_name"],
                oidc_issuer=r["oidc_issuer"], status=r["status"],
                created_at=r["created_at"], updated_at=r["updated_at"],
            )

    async def set_status(self, org_id: str, status: str) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE tenants SET status=$2, updated_at=now() WHERE org_id=$1",
                org_id, status,
            )

    async def add_user(self, row: TenantUserRow) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO tenant_users (user_id, org_id, role, created_at)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (user_id, org_id) DO UPDATE SET role=EXCLUDED.role
                """,
                row.user_id, row.org_id, row.role, row.created_at,
            )

    async def get_user(self, user_id: str, org_id: str) -> TenantUserRow | None:
        async with self._db.admin_session() as conn:
            r = await conn.fetchrow(
                "SELECT user_id, org_id, role, created_at FROM tenant_users "
                "WHERE user_id=$1 AND org_id=$2",
                user_id, org_id,
            )
            if r is None:
                return None
            return TenantUserRow(
                user_id=r["user_id"], org_id=r["org_id"], role=r["role"],
                created_at=r["created_at"],
            )

    async def list_users(self, org_id: str) -> list[TenantUserRow]:
        async with self._db.admin_session() as conn:
            rows = await conn.fetch(
                "SELECT user_id, org_id, role, created_at FROM tenant_users "
                "WHERE org_id=$1 ORDER BY created_at",
                org_id,
            )
            return [
                TenantUserRow(
                    user_id=r["user_id"], org_id=r["org_id"], role=r["role"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    async def remove_user(self, user_id: str, org_id: str) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "DELETE FROM tenant_users WHERE user_id=$1 AND org_id=$2",
                user_id, org_id,
            )


def _api_key_row(rec: Any) -> ApiKeyRow:
    # `rec` is an asyncpg.Record (mapping access); build explicitly so the result
    # is order-independent of the SELECT column order.
    return ApiKeyRow(
        key_id=rec["key_id"], org_id=rec["org_id"], prefix=rec["prefix"],
        key_hash=rec["key_hash"], name=rec["name"], scopes=list(rec["scopes"]),
        created_by=rec["created_by"], created_at=rec["created_at"],
        expires_at=rec["expires_at"], last_used_at=rec["last_used_at"],
        revoked_at=rec["revoked_at"], revocation_reason=rec["revocation_reason"],
    )


class PgApiKeyRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, row: ApiKeyRow) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, org_id, prefix, key_hash, name, scopes, created_by,
                    created_at, expires_at, last_used_at, revoked_at, revocation_reason
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                """,
                row.key_id, row.org_id, row.prefix, row.key_hash, row.name, row.scopes,
                row.created_by, row.created_at, row.expires_at, row.last_used_at,
                row.revoked_at, row.revocation_reason,
            )

    async def get_by_prefix(self, prefix: str) -> ApiKeyRow | None:
        async with self._db.admin_session() as conn:
            r = await conn.fetchrow("SELECT * FROM api_keys WHERE prefix=$1", prefix)
            return None if r is None else _api_key_row(r)

    async def list_for_org(self, org_id: str) -> list[ApiKeyRow]:
        async with self._db.admin_session() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_keys WHERE org_id=$1 ORDER BY created_at DESC", org_id
            )
            return [_api_key_row(r) for r in rows]

    async def revoke(self, key_id: UUID, org_id: str, reason: str, when: datetime) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE api_keys SET revoked_at=$3, revocation_reason=$4 "
                "WHERE key_id=$1 AND org_id=$2",
                key_id, org_id, when, reason,
            )

    async def touch_last_used(self, key_id: UUID, when: datetime) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE api_keys SET last_used_at=$2 WHERE key_id=$1", key_id, when
            )
