"""CredentialVault: encryption round-trip, wrong key, store/retrieve, list, rotate, delete."""

from __future__ import annotations

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import DecryptionError, FernetEncryptor
from skylize.app.credentials.vault import CredentialNotFoundError, CredentialVault
from skylize.dal.credentials import InMemoryCredentialRepository
from skylize.dal.memory import InMemoryAuditRepository
from skylize.events.memory_bus import InMemoryEventBus


def _vault(key: str | None = None) -> tuple[CredentialVault, InMemoryCredentialRepository]:
    key = key or Fernet.generate_key().decode()
    repo = InMemoryCredentialRepository()
    audit = AuditService(InMemoryEventBus(), InMemoryAuditRepository())
    return CredentialVault(FernetEncryptor(key), repo, audit), repo


# ---------------------------------------------------------------------------
# Encryption unit tests
# ---------------------------------------------------------------------------

async def test_fernet_round_trip() -> None:
    key = Fernet.generate_key().decode()
    enc = FernetEncryptor(key)
    original = "super-secret-api-key"
    assert enc.decrypt(enc.encrypt(original)) == original


async def test_wrong_key_raises_decryption_error() -> None:
    enc1 = FernetEncryptor(Fernet.generate_key().decode())
    enc2 = FernetEncryptor(Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        enc2.decrypt(enc1.encrypt("secret"))


async def test_corrupted_ciphertext_raises_decryption_error() -> None:
    enc = FernetEncryptor(Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        enc.decrypt("not-valid-fernet-data")


# ---------------------------------------------------------------------------
# Vault integration (mocked DB via InMemory repo)
# ---------------------------------------------------------------------------

async def test_store_and_retrieve() -> None:
    vault, _ = _vault()
    await vault.store("org1", "hubspot", "tok_abc123", correlation_id=uuid4())
    assert await vault.retrieve("org1", "hubspot") == "tok_abc123"


async def test_retrieve_decrypts_correctly() -> None:
    vault, repo = _vault()
    await vault.store("org1", "slack", "xoxb-real", correlation_id=uuid4())
    # The stored value must be ciphertext, not plaintext
    row = await repo.get("org1", "slack", "")
    assert row is not None
    assert row.encrypted_value != "xoxb-real"
    assert await vault.retrieve("org1", "slack") == "xoxb-real"


async def test_list_providers_returns_names_only() -> None:
    vault, _ = _vault()
    await vault.store("org1", "hubspot", "tok1", correlation_id=uuid4())
    await vault.store("org1", "slack", "xoxb-token", correlation_id=uuid4())
    providers = await vault.list_providers("org1")
    assert set(providers) == {"hubspot", "slack"}
    assert "tok1" not in providers
    assert "xoxb-token" not in providers


async def test_list_providers_empty_org() -> None:
    vault, _ = _vault()
    assert await vault.list_providers("org_empty") == []


async def test_rotate_overwrites_value() -> None:
    vault, _ = _vault()
    await vault.store("org1", "anytype", "old-key", correlation_id=uuid4())
    await vault.rotate("org1", "anytype", "new-key", correlation_id=uuid4())
    assert await vault.retrieve("org1", "anytype") == "new-key"


async def test_rotate_updates_rotated_at() -> None:
    vault, repo = _vault()
    await vault.store("org1", "anytype", "old", correlation_id=uuid4())
    row_before = await repo.get("org1", "anytype", "")
    assert row_before is not None and row_before.rotated_at is None
    await vault.rotate("org1", "anytype", "new", correlation_id=uuid4())
    row_after = await repo.get("org1", "anytype", "")
    assert row_after is not None and row_after.rotated_at is not None


async def test_delete_removes_credential() -> None:
    vault, _ = _vault()
    await vault.store("org1", "slack", "tok", correlation_id=uuid4())
    await vault.delete("org1", "slack", correlation_id=uuid4())
    with pytest.raises(CredentialNotFoundError):
        await vault.retrieve("org1", "slack")


async def test_retrieve_missing_raises_not_found() -> None:
    vault, _ = _vault()
    with pytest.raises(CredentialNotFoundError):
        await vault.retrieve("org1", "nonexistent")


async def test_delete_missing_raises_not_found() -> None:
    vault, _ = _vault()
    with pytest.raises(CredentialNotFoundError):
        await vault.delete("org1", "ghost", correlation_id=uuid4())


async def test_tenant_isolation() -> None:
    vault, _ = _vault()
    await vault.store("org1", "hubspot", "tok1", correlation_id=uuid4())
    # org2 must not see org1's credential
    with pytest.raises(CredentialNotFoundError):
        await vault.retrieve("org2", "hubspot")


async def test_label_differentiates_credentials() -> None:
    vault, _ = _vault()
    await vault.store("org1", "hubspot", "tok-main", label="main", correlation_id=uuid4())
    await vault.store("org1", "hubspot", "tok-sandbox", label="sandbox", correlation_id=uuid4())
    assert await vault.retrieve("org1", "hubspot", "main") == "tok-main"
    assert await vault.retrieve("org1", "hubspot", "sandbox") == "tok-sandbox"


async def test_delete_by_id() -> None:
    vault, repo = _vault()
    cred_id = await vault.store("org1", "google_ads", "ads-key", correlation_id=uuid4())
    await vault.delete_by_id(cred_id, "org1", correlation_id=uuid4())
    assert await repo.get_by_id(cred_id, "org1") is None
