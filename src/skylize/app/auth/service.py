"""
API key issuance, authentication, and lifecycle (Subsystem 1).

Programmatic / agent-to-agent callers present an API key instead of an OIDC JWT.
A key is ``sky.<prefix>.<secret>``: only the SHA-256 of ``<secret>`` is stored,
with ``<prefix>`` as the public, indexed lookup handle. ``authenticate`` resolves
a presented key to its owning org and yields the SAME short-lived
``RequestContext`` the OIDC path produces — so everything downstream is identical
regardless of credential type. The plaintext secret is returned exactly once, at
issuance, and never persisted.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from ...dal.ports import ApiKeyRepository, ApiKeyRow
from ...schemas.base import RequestContext
from ...security.api_keys import generate_api_key, parse_api_key, verify_secret
from ..audit.service import AuditService


class ApiKeyService:
    def __init__(self, repo: ApiKeyRepository, audit: AuditService) -> None:
        self._repo = repo
        self._audit = audit

    async def issue(
        self,
        *,
        org_id: str,
        name: str,
        scopes: Sequence[str],
        created_by: str,
        correlation_id: UUID,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKeyRow, str]:
        """Mint a key for ``org_id``. Returns the stored row and the one-time secret."""
        generated = generate_api_key()
        key_id = uuid4()
        row = ApiKeyRow(
            key_id=key_id,
            org_id=org_id,
            prefix=generated.prefix,
            key_hash=generated.key_hash,
            name=name,
            scopes=list(scopes),
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            last_used_at=None,
            revoked_at=None,
            revocation_reason=None,
        )
        await self._repo.insert(row)
        # The secret never enters the audit trail — only its identity/metadata.
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="apikey.issued",
            result="success",
            inputs={
                "key_id": str(key_id),
                "name": name,
                "scopes": list(scopes),
                "created_by": created_by,
            },
        )
        return row, generated.full_key

    async def authenticate(self, presented: str, *, ttl_s: int) -> RequestContext | None:
        """Resolve a presented key to a RequestContext, or None if invalid.

        A service principal's scopes become its ``roles``, so the same RBAC
        ``require_role`` checks apply to keys and human tokens alike.
        """
        parsed = parse_api_key(presented)
        if parsed is None:
            return None
        prefix, secret = parsed
        row = await self._repo.get_by_prefix(prefix)
        if row is None or row.revoked_at is not None:
            return None
        if not verify_secret(secret, row.key_hash):
            return None
        now = datetime.now(timezone.utc)
        if row.expires_at is not None and row.expires_at <= now:
            return None
        await self._repo.touch_last_used(row.key_id, now)
        return RequestContext(
            org_id=row.org_id,
            user_id=f"apikey:{row.key_id}",
            roles=list(row.scopes),
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_s),
        )

    async def list(self, org_id: str) -> list[ApiKeyRow]:
        return await self._repo.list_for_org(org_id)

    async def revoke(
        self, *, org_id: str, key_id: UUID, actor: str, reason: str, correlation_id: UUID
    ) -> bool:
        """Revoke a key the tenant owns. Returns False if it isn't theirs / absent."""
        keys = await self._repo.list_for_org(org_id)
        if not any(k.key_id == key_id for k in keys):
            return False
        await self._repo.revoke(key_id, org_id, reason, datetime.now(timezone.utc))
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="apikey.revoked",
            result="success",
            inputs={"key_id": str(key_id), "actor": actor, "reason": reason},
        )
        return True
