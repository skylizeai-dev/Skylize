from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

from ...dal.credentials import CredentialRepository, CredentialRow
from ..audit.service import AuditService
from .encryption import FernetEncryptor

log = structlog.get_logger()


class CredentialNotFoundError(Exception):
    """Raised when a requested credential does not exist for the given org."""


class CredentialVault:
    def __init__(
        self,
        encryptor: FernetEncryptor,
        repo: CredentialRepository,
        audit: AuditService,
    ) -> None:
        self._enc = encryptor
        self._repo = repo
        self._audit = audit

    async def store(
        self,
        org_id: str,
        provider: str,
        raw_value: str,
        *,
        label: str = "",
        metadata: dict[str, object] | None = None,
        correlation_id: UUID,
    ) -> UUID:
        cred_id = uuid4()
        row = CredentialRow(
            cred_id=cred_id,
            org_id=org_id,
            provider=provider,
            label=label,
            encrypted_value=self._enc.encrypt(raw_value),
            metadata_json=json.dumps(metadata or {}),
            created_at=datetime.now(timezone.utc),
            rotated_at=None,
        )
        await self._repo.insert(row)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="credential.stored",
            result="success",
            inputs={"provider": provider, "label": label},
        )
        log.info("credential.stored", org_id=org_id, provider=provider, label=label)
        return cred_id

    async def retrieve(
        self,
        org_id: str,
        provider: str,
        label: str = "",
    ) -> str:
        """Decrypt and return a credential. Return value MUST NOT appear in logs."""
        row = await self._repo.get(org_id, provider, label)
        if row is None:
            raise CredentialNotFoundError(f"{provider!r} credential not found for org {org_id!r}")
        return self._enc.decrypt(row.encrypted_value)

    async def rotate(
        self,
        org_id: str,
        provider: str,
        new_value: str,
        *,
        label: str = "",
        correlation_id: UUID,
    ) -> None:
        row = await self._repo.get(org_id, provider, label)
        if row is None:
            raise CredentialNotFoundError(f"{provider!r} credential not found for org {org_id!r}")
        await self._repo.update_encrypted_value(
            cred_id=row.cred_id,
            org_id=org_id,
            encrypted_value=self._enc.encrypt(new_value),
            rotated_at=datetime.now(timezone.utc),
        )
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="credential.rotated",
            result="success",
            inputs={"provider": provider, "label": label},
        )
        log.info("credential.rotated", org_id=org_id, provider=provider, label=label)

    async def delete(
        self,
        org_id: str,
        provider: str,
        *,
        label: str = "",
        correlation_id: UUID,
    ) -> None:
        row = await self._repo.get(org_id, provider, label)
        if row is None:
            raise CredentialNotFoundError(f"{provider!r} credential not found for org {org_id!r}")
        await self._repo.delete_by_id(row.cred_id, org_id)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="credential.deleted",
            result="success",
            inputs={"provider": provider, "label": label},
        )
        log.info("credential.deleted", org_id=org_id, provider=provider, label=label)

    async def delete_by_id(
        self,
        cred_id: UUID,
        org_id: str,
        *,
        correlation_id: UUID,
    ) -> None:
        row = await self._repo.get_by_id(cred_id, org_id)
        if row is None:
            raise CredentialNotFoundError(f"credential {cred_id} not found")
        await self._repo.delete_by_id(cred_id, org_id)
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="credential.deleted",
            result="success",
            inputs={"cred_id": str(cred_id), "provider": row.provider, "label": row.label},
        )
        log.info("credential.deleted", org_id=org_id, cred_id=str(cred_id))

    async def list_providers(self, org_id: str) -> list[str]:
        return await self._repo.list_providers(org_id)
