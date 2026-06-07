"""
Tenant onboarding + RBAC (Subsystem 1).

The system of record for org provisioning and per-user roles. It writes the
platform-level ``tenants`` / ``tenant_users`` tables (no RLS — see migration
0001) through the ``TenantRepository`` port and mirrors every mutation to the
audit trail. Role and status vocabularies are pinned to the DB CHECK constraints
so an invalid value is rejected here (a 4xx) rather than at the driver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from ...dal.ports import TenantRepository, TenantRow, TenantUserRow
from ..audit.service import AuditService

# Pinned to the CHECK constraints in migration 0001.
VALID_ROLES = frozenset({"owner", "admin", "operator", "analyst", "viewer"})
VALID_STATUSES = frozenset({"active", "suspended", "killed"})


class TenantError(Exception):
    """Domain error — the edge maps this to a 4xx response."""


class TenantService:
    def __init__(self, repo: TenantRepository, audit: AuditService) -> None:
        self._repo = repo
        self._audit = audit

    async def register(
        self,
        *,
        org_id: str,
        display_name: str,
        owner_user_id: str,
        correlation_id: UUID,
        oidc_issuer: str = "dev",
    ) -> TenantRow:
        """Provision a new org and seed the caller as its first owner."""
        if await self._repo.get_tenant(org_id) is not None:
            raise TenantError(f"tenant already registered: {org_id}")
        now = datetime.now(timezone.utc)
        row = TenantRow(
            org_id=org_id,
            display_name=display_name,
            oidc_issuer=oidc_issuer,
            status="active",
            created_at=now,
            updated_at=now,
        )
        await self._repo.create_tenant(row)
        await self._repo.add_user(
            TenantUserRow(user_id=owner_user_id, org_id=org_id, role="owner", created_at=now)
        )
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="tenant.registered",
            result="success",
            inputs={"display_name": display_name, "owner": owner_user_id},
        )
        return row

    async def get(self, org_id: str) -> TenantRow | None:
        return await self._repo.get_tenant(org_id)

    async def set_status(
        self, *, org_id: str, status: str, actor: str, correlation_id: UUID
    ) -> TenantRow:
        if status not in VALID_STATUSES:
            raise TenantError(f"invalid status: {status}")
        if await self._repo.get_tenant(org_id) is None:
            raise TenantError(f"unknown tenant: {org_id}")
        await self._repo.set_status(org_id, status)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="tenant.status_changed",
            result="success",
            inputs={"status": status, "actor": actor},
        )
        updated = await self._repo.get_tenant(org_id)
        assert updated is not None  # just written above
        return updated

    async def set_user_role(
        self, *, org_id: str, user_id: str, role: str, actor: str, correlation_id: UUID
    ) -> TenantUserRow:
        if role not in VALID_ROLES:
            raise TenantError(f"invalid role: {role}")
        if await self._repo.get_tenant(org_id) is None:
            raise TenantError(f"unknown tenant: {org_id}")
        row = TenantUserRow(
            user_id=user_id, org_id=org_id, role=role, created_at=datetime.now(timezone.utc)
        )
        await self._repo.add_user(row)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="tenant.user_role_set",
            result="success",
            inputs={"user_id": user_id, "role": role, "actor": actor},
        )
        return row

    async def list_users(self, org_id: str) -> list[TenantUserRow]:
        return await self._repo.list_users(org_id)

    async def remove_user(
        self, *, org_id: str, user_id: str, actor: str, correlation_id: UUID
    ) -> None:
        users = await self._repo.list_users(org_id)
        target = next((u for u in users if u.user_id == user_id), None)
        if target is None:
            raise TenantError(f"user not in tenant: {user_id}")
        owners = [u for u in users if u.role == "owner"]
        if target.role == "owner" and len(owners) <= 1:
            raise TenantError("cannot remove the last owner")
        await self._repo.remove_user(user_id, org_id)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="tenant.user_removed",
            result="success",
            inputs={"user_id": user_id, "actor": actor},
        )
