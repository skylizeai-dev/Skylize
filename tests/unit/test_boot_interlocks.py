"""Boot interlocks: the settings combinations the process refuses to start on.

Both checks here guard the SAME failure shape — a deployment that looks healthy,
serves every request, and has silently switched a security guarantee off:

  * `dev_auth` on a real backend. `edge/auth.py:39-50` trusts X-Dev-Org /
    X-Dev-User / X-Dev-Roles verbatim, so any caller asserts any org and any
    role. Nothing about that is authentication.
  * `db_app_url` empty or equal to `db_url`. `Settings.runtime_db_url` falls back
    to `db_url`, the table-OWNING superuser, and a table owner bypasses RLS
    regardless of FORCE ROW LEVEL SECURITY
    (migrations/versions/0003_app_role_rls_subject.py:7-12).

Neither produces an error at request time. Refusing to construct Settings is the
only place the mistake is visible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skylize.config import Settings

_APP_DSN = "postgresql://skylize_app@localhost:5432/skylize"
_OWNER_DSN = "postgresql://skylize@localhost:5432/skylize"
_SECRET = "boot-interlock-test-secret-not-a-credential"


def _prod(**kwargs: object) -> Settings:
    """Non-memory Settings that satisfies every interlock except the one under test."""
    base: dict[str, object] = {
        "backend": "postgres",
        "dev_auth": False,
        "jwt_secret": _SECRET,
        "db_url": _OWNER_DSN,
        "db_app_url": _APP_DSN,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


# ── dev_auth on a real backend ───────────────────────────────────────────────

def test_dev_auth_on_postgres_is_refused() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_DEV_AUTH must be false"):
        _prod(dev_auth=True)


def test_dev_auth_refusal_names_the_variable_and_the_remedy() -> None:
    with pytest.raises(ValidationError) as ei:
        _prod(dev_auth=True)
    message = str(ei.value)
    assert "SKYLIZE_DEV_AUTH" in message
    assert "SKYLIZE_BACKEND" in message
    # It must say what to set instead, not merely that something is wrong.
    assert "SKYLIZE_DEV_AUTH=false" in message
    assert "SKYLIZE_BACKEND=memory" in message


def test_dev_auth_on_memory_backend_is_allowed() -> None:
    """The memory backend is the one place dev auth is honest: no real tenant
    data exists behind it."""
    settings = Settings(backend="memory", dev_auth=True)
    assert settings.dev_auth is True


# ── db_app_url must be a distinct, non-superuser DSN ─────────────────────────

def test_empty_app_dsn_on_postgres_is_refused() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_DB_APP_URL must be set"):
        _prod(db_app_url="")


def test_app_dsn_equal_to_owner_dsn_is_refused() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_DB_APP_URL must differ"):
        _prod(db_app_url=_OWNER_DSN)


def test_equality_is_compared_after_stripping_whitespace() -> None:
    """A trailing newline from a secrets file or a heredoc must not defeat the
    check — the two values are the same DSN."""
    with pytest.raises(ValidationError, match="SKYLIZE_DB_APP_URL must differ"):
        _prod(db_app_url=f"  {_OWNER_DSN}\n")


def test_whitespace_only_app_dsn_counts_as_empty() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_DB_APP_URL must be set"):
        _prod(db_app_url="   \n ")


def test_distinct_app_dsn_on_postgres_is_allowed() -> None:
    settings = _prod()
    assert settings.runtime_db_url == _APP_DSN
    # The property is what the pool actually reads, so pin it: this is the value
    # the interlock exists to keep off the superuser.
    assert settings.runtime_db_url != settings.db_url


def test_memory_backend_needs_no_app_dsn() -> None:
    """Nothing connects to Postgres on the memory backend, so neither DSN is
    load-bearing there."""
    settings = Settings(backend="memory", db_app_url="")
    assert settings.backend == "memory"


# ── the interlocks are independent ───────────────────────────────────────────

def test_both_misconfigurations_at_once_still_refuse() -> None:
    with pytest.raises(ValidationError):
        Settings(backend="postgres", dev_auth=True, db_app_url="")
