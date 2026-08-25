"""The credential vault's Fernet key must be an explicit, persistent secret.

`bootstrap.build_container` used to wire the vault as
``FernetEncryptor(settings.credential_encryption_key or FernetEncryptor.generate_key())``:
a production deployment that forgot SKYLIZE_CREDENTIAL_ENCRYPTION_KEY started
cleanly on a per-boot random key, wrote real credentials into
``org_credentials.encrypted_value``, and made every one of them undecryptable at
the next restart -- surfacing one restart later as
``DecryptionError("wrong key or corrupted ciphertext")``, a corruption message
for what was a configuration mistake. These tests pin the fallback shut on any
backend whose rows outlive the process.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from skylize.app.credentials.encryption import FernetEncryptor
from skylize.bootstrap import (
    ConfigurationError,
    build_container,
    resolve_credential_encryption_key,
)
from skylize.config import Settings


def _prod_settings(**overrides: object) -> Settings:
    """A minimally-valid postgres Settings; the other boot interlocks are satisfied
    so each test fails (or passes) on the credential key alone.

    ``_env_file=None`` keeps a developer's local .env -- which Settings loads by
    default (config.py:24) -- from supplying the very key a test asserts is unset.
    """
    return Settings(
        _env_file=None,
        backend="postgres",
        dev_auth=False,                      # _forbid_dev_auth_on_a_real_backend
        jwt_secret="test-jwt-secret",        # _require_jwt_secret_when_prod
        db_url="postgresql://owner@db:5432/skylize",
        db_app_url="postgresql://app@db:5432/skylize",  # must differ from db_url
        **overrides,
    )


async def test_build_container_refuses_to_start_without_a_key_on_a_real_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: no key + durable rows => the boot fails, loudly.

    This goes through the real ``build_container``, not just the helper, and the
    guard sits ahead of the backend branch -- so this raises without a database
    or Redis in reach. A silent ephemeral key would instead return a Container
    here, which is the regression being pinned.
    """
    monkeypatch.delenv("SKYLIZE_CREDENTIAL_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ConfigurationError) as exc:
        await build_container(_prod_settings(credential_encryption_key=""))

    # The message must name the variable an operator has to set, and say why.
    assert "SKYLIZE_CREDENTIAL_ENCRYPTION_KEY" in str(exc.value)
    assert "undecryptable" in str(exc.value)


async def test_key_is_required_before_any_connection_is_opened() -> None:
    """Fail while the process is still doing nothing.

    ``db_url``/``db_app_url`` above point at a host that does not resolve. If the
    guard ran at the vault (bootstrap.py's original position, after the postgres
    branch builds pools), this would surface as a connection error instead.
    """
    with pytest.raises(ConfigurationError):
        await build_container(_prod_settings(credential_encryption_key=""))


def test_resolver_rejects_a_blank_key_on_a_real_backend() -> None:
    """Whitespace is not a key: a variable set to "" or " " in a deployment's
    secret store is the same missing-key condition, not a valid Fernet key."""
    for blank in ("", "   ", "\n"):
        with pytest.raises(ConfigurationError):
            resolve_credential_encryption_key(
                _prod_settings(credential_encryption_key=blank)
            )


def test_resolver_rejects_a_malformed_key_at_boot() -> None:
    """A placeholder value fails the boot with a message naming the variable,
    rather than a bare cryptography ValueError raised deeper in the wiring."""
    with pytest.raises(ConfigurationError) as exc:
        resolve_credential_encryption_key(
            _prod_settings(credential_encryption_key="changeme")
        )

    assert "SKYLIZE_CREDENTIAL_ENCRYPTION_KEY" in str(exc.value)


def test_configured_key_is_returned_verbatim_never_re_minted() -> None:
    """The resolver hands back the operator's key itself. If it ever minted a
    fresh one despite a configured value, stored credentials would still be
    orphaned at restart -- the exact defect, one layer down."""
    key = Fernet.generate_key().decode()
    resolved = resolve_credential_encryption_key(
        _prod_settings(credential_encryption_key=key)
    )

    assert resolved == key
    # And it is a working key, not just an equal string.
    encryptor = FernetEncryptor(resolved)
    assert encryptor.decrypt(encryptor.encrypt("hunter2")) == "hunter2"


def test_memory_backend_still_mints_an_ephemeral_key() -> None:
    """The dev path survives deliberately: on ``backend="memory"`` the vault is
    backed by InMemoryCredentialRepository (bootstrap.py:230), whose rows are
    discarded at process exit, so there is nothing left to be undecryptable.
    Requiring a key here would break every local run and unit test for no
    security gain.
    """
    settings = Settings(_env_file=None, backend="memory", credential_encryption_key="")

    first = resolve_credential_encryption_key(settings)
    second = resolve_credential_encryption_key(settings)

    assert first != second, "each ephemeral mint must be a fresh key"
    encryptor = FernetEncryptor(first)
    assert encryptor.decrypt(encryptor.encrypt("hunter2")) == "hunter2"
