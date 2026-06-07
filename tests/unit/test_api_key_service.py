"""ApiKeyService: issue → authenticate, plus revoked / expired / bad-key rejection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from skylize.app.audit.service import AuditService
from skylize.app.auth.service import ApiKeyService
from skylize.dal.memory import InMemoryApiKeyRepository, InMemoryAuditRepository
from skylize.events.memory_bus import InMemoryEventBus


def _service() -> tuple[ApiKeyService, InMemoryAuditRepository]:
    audit_repo = InMemoryAuditRepository()
    audit = AuditService(InMemoryEventBus(), audit_repo)
    return ApiKeyService(InMemoryApiKeyRepository(), audit), audit_repo


async def test_issue_then_authenticate() -> None:
    svc, audit_repo = _service()
    row, secret = await svc.issue(
        org_id="o", name="ci", scopes=["admin"], created_by="u", correlation_id=uuid4()
    )
    assert secret.startswith("sky.")
    ctx = await svc.authenticate(secret, ttl_s=300)
    assert ctx is not None
    assert ctx.org_id == "o"
    assert "admin" in ctx.roles
    assert ctx.user_id == f"apikey:{row.key_id}"
    assert any(r.action_type == "apikey.issued" for r in audit_repo.rows)


async def test_bad_key_rejected() -> None:
    svc, _ = _service()
    assert await svc.authenticate("sky.deadbeefdead.nope", ttl_s=300) is None
    assert await svc.authenticate("garbage", ttl_s=300) is None


async def test_revoked_key_rejected() -> None:
    svc, _ = _service()
    row, secret = await svc.issue(
        org_id="o", name="k", scopes=[], created_by="u", correlation_id=uuid4()
    )
    assert await svc.revoke(
        org_id="o", key_id=row.key_id, actor="u", reason="x", correlation_id=uuid4()
    )
    assert await svc.authenticate(secret, ttl_s=300) is None


async def test_revoke_foreign_key_returns_false() -> None:
    svc, _ = _service()
    row, _secret = await svc.issue(
        org_id="o", name="k", scopes=[], created_by="u", correlation_id=uuid4()
    )
    # A different org cannot revoke a key it does not own.
    assert not await svc.revoke(
        org_id="other", key_id=row.key_id, actor="x", reason="y", correlation_id=uuid4()
    )


async def test_expired_key_rejected() -> None:
    svc, _ = _service()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    _row, secret = await svc.issue(
        org_id="o", name="k", scopes=[], created_by="u", correlation_id=uuid4(),
        expires_at=past,
    )
    assert await svc.authenticate(secret, ttl_s=300) is None
