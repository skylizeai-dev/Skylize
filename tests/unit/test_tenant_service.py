"""TenantService: onboarding seeds an owner, RBAC validation, audit trail."""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.tenants.service import TenantError, TenantService
from skylize.dal.memory import InMemoryAuditRepository, InMemoryTenantRepository
from skylize.events.memory_bus import InMemoryEventBus


def _service() -> tuple[TenantService, InMemoryAuditRepository]:
    audit_repo = InMemoryAuditRepository()
    audit = AuditService(InMemoryEventBus(), audit_repo)
    return TenantService(InMemoryTenantRepository(), audit), audit_repo


async def test_register_seeds_owner_and_audits() -> None:
    svc, audit_repo = _service()
    row = await svc.register(
        org_id="org_1", display_name="Acme", owner_user_id="u1", correlation_id=uuid4()
    )
    assert row.status == "active"
    users = await svc.list_users("org_1")
    assert any(u.user_id == "u1" and u.role == "owner" for u in users)
    assert any(r.action_type == "tenant.registered" for r in audit_repo.rows)


async def test_duplicate_register_rejected() -> None:
    svc, _ = _service()
    await svc.register(org_id="o", display_name="x", owner_user_id="u", correlation_id=uuid4())
    with pytest.raises(TenantError):
        await svc.register(
            org_id="o", display_name="y", owner_user_id="u2", correlation_id=uuid4()
        )


async def test_invalid_role_rejected() -> None:
    svc, _ = _service()
    await svc.register(org_id="o", display_name="x", owner_user_id="u", correlation_id=uuid4())
    with pytest.raises(TenantError):
        await svc.set_user_role(
            org_id="o", user_id="u2", role="superadmin", actor="u", correlation_id=uuid4()
        )


async def test_cannot_remove_last_owner() -> None:
    svc, _ = _service()
    await svc.register(org_id="o", display_name="x", owner_user_id="u", correlation_id=uuid4())
    with pytest.raises(TenantError):
        await svc.remove_user(org_id="o", user_id="u", actor="u", correlation_id=uuid4())


async def test_set_status_updates_and_audits() -> None:
    svc, audit_repo = _service()
    await svc.register(org_id="o", display_name="x", owner_user_id="u", correlation_id=uuid4())
    updated = await svc.set_status(
        org_id="o", status="suspended", actor="u", correlation_id=uuid4()
    )
    assert updated.status == "suspended"
    assert any(r.action_type == "tenant.status_changed" for r in audit_repo.rows)
