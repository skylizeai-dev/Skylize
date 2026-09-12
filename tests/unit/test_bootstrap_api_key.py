"""The bootstrap-key command: idempotency guard, --force, and format identity.

The command exists because ``POST /api/v1/api-keys`` cannot mint the first key
on a postgres deployment -- it is gated by ``get_context``, which accepts only an
existing API key or an OIDC JWT. See ``skylize/ops/bootstrap_api_key.py`` for the
full argument and the precedent it follows.

What these tests pin, in order of how much it would hurt to get wrong:

  1. A bootstrapped key AUTHENTICATES through the ordinary
     ``ApiKeyService.authenticate`` path and carries its scopes as roles. If this
     broke, the command would hand the operator a credential the API rejects.
  2. The stored form is the EXISTING one -- ``sky.<prefix>.<secret>`` with only
     ``sha256(secret)`` persisted -- asserted against ``security.api_keys``
     directly, so a second hashing scheme cannot be introduced here unnoticed.
  3. The plaintext reaches the return value and NOTHING else: not the row, not
     the audit trail.
  4. The refusal and ``--force`` semantics, including the two states that
     deliberately do not block a re-bootstrap (revoked, expired).

The live end-to-end proof -- the printed key authenticating against a running
instance backed by real Postgres -- is a separate manual verification recorded in
docs/08_operations/DAY1_PROVISIONING.md; these are the in-process invariants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.auth.service import ApiKeyService
from skylize.app.tenants.service import TenantService
from skylize.dal.memory import (
    InMemoryApiKeyRepository,
    InMemoryAuditRepository,
    InMemoryTenantRepository,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.ops.bootstrap_api_key import (
    BOOTSTRAP_KEY_NAME,
    CREATED_BY,
    BootstrapRefused,
    TenantMissing,
    active_bootstrap_keys,
    ensure_tenant,
    mint_bootstrap_key,
)
from skylize.security.api_keys import hash_secret, parse_api_key

_ORG = "acme"


def _wiring() -> tuple[ApiKeyService, InMemoryApiKeyRepository, InMemoryAuditRepository]:
    repo = InMemoryApiKeyRepository()
    audit_repo = InMemoryAuditRepository()
    return ApiKeyService(repo, AuditService(InMemoryEventBus(), audit_repo)), repo, audit_repo


# ── the happy path ────────────────────────────────────────────────────────────

async def test_bootstrap_on_empty_org_mints_a_usable_owner_key() -> None:
    """The whole point: an org with no keys gets one, and it actually works."""
    svc, repo, _ = _wiring()

    row, secret = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    assert row.org_id == _ORG
    assert row.name == BOOTSTRAP_KEY_NAME
    assert row.scopes == ["owner"]
    assert row.created_by == CREATED_BY
    assert row.revoked_at is None

    # The credential resolves through the SAME path a route caller takes, and a
    # key's scopes become its roles -- which is what clears require_role("owner").
    ctx = await svc.authenticate(secret, ttl_s=300)
    assert ctx is not None
    assert ctx.org_id == _ORG
    assert "owner" in ctx.roles


async def test_stored_form_is_the_existing_one_not_a_second_scheme() -> None:
    """Guards against anyone re-implementing hashing inside the command."""
    svc, repo, _ = _wiring()

    row, secret = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    parsed = parse_api_key(secret)
    assert parsed is not None, "must render as sky.<prefix>.<secret>"
    prefix, plaintext_secret = parsed
    assert prefix == row.prefix
    # The persisted value is the SHA-256 of the secret component and nothing else.
    assert row.key_hash == hash_secret(plaintext_secret)
    assert row.key_hash != plaintext_secret


async def test_plaintext_never_leaves_the_return_value() -> None:
    """The secret is in the returned string only -- not the row, not the audit."""
    svc, repo, audit_repo = _wiring()

    row, secret = await mint_bootstrap_key(svc, repo, org_id=_ORG)
    _, plaintext_secret = parse_api_key(secret)  # type: ignore[misc]

    stored = await repo.list_for_org(_ORG)
    assert [r.key_hash for r in stored] == [row.key_hash]
    # repr, not vars: ApiKeyRow is a slots dataclass and has no __dict__.
    assert all(plaintext_secret not in repr(r) for r in stored)

    issued = [r for r in audit_repo.rows if r.action_type == "apikey.issued"]
    assert len(issued) == 1, "minting must be audited"
    assert all(plaintext_secret not in repr(r) for r in audit_repo.rows)


# ── the idempotency guard ─────────────────────────────────────────────────────

async def test_second_bootstrap_is_refused_without_force() -> None:
    svc, repo, _ = _wiring()
    await mint_bootstrap_key(svc, repo, org_id=_ORG)

    with pytest.raises(BootstrapRefused) as exc:
        await mint_bootstrap_key(svc, repo, org_id=_ORG)

    # The message has to tell the operator how to get unstuck, since the first
    # key's plaintext is gone for good.
    assert "--force" in str(exc.value)
    assert len(await repo.list_for_org(_ORG)) == 1, "refusal must not write a key"


async def test_force_mints_an_additional_key_and_leaves_the_first_live() -> None:
    svc, repo, _ = _wiring()
    first, first_secret = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    second, second_secret = await mint_bootstrap_key(svc, repo, org_id=_ORG, force=True)

    assert second.key_id != first.key_id
    assert second_secret != first_secret
    assert len(await repo.list_for_org(_ORG)) == 2
    # --force does NOT revoke; the command warns about this on stderr and the
    # runbook tells the operator to revoke deliberately.
    assert await svc.authenticate(first_secret, ttl_s=300) is not None
    assert await svc.authenticate(second_secret, ttl_s=300) is not None


async def test_refusal_is_scoped_to_the_org() -> None:
    """A bootstrapped org must not block a different, unbootstrapped tenant."""
    svc, repo, _ = _wiring()
    await mint_bootstrap_key(svc, repo, org_id=_ORG)

    row, _ = await mint_bootstrap_key(svc, repo, org_id="other-co")

    assert row.org_id == "other-co"


async def test_an_ordinary_key_does_not_count_as_a_bootstrap() -> None:
    """The name is the handle, so normal keys neither block nor masquerade."""
    svc, repo, _ = _wiring()

    await svc.issue(
        org_id=_ORG, name="ci-runner", scopes=["admin"], created_by="u",
        correlation_id=uuid4(),
    )

    row, _ = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    assert row.name == BOOTSTRAP_KEY_NAME


async def test_revoked_bootstrap_key_does_not_block_a_re_bootstrap() -> None:
    """Revoking the only credential must leave the org recoverable without --force."""
    svc, repo, _ = _wiring()

    first, _ = await mint_bootstrap_key(svc, repo, org_id=_ORG)
    assert await svc.revoke(
        org_id=_ORG, key_id=first.key_id, actor="ops", reason="leaked",
        correlation_id=uuid4(),
    )

    second, _ = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    assert second.key_id != first.key_id


async def test_expired_bootstrap_key_does_not_block_a_re_bootstrap() -> None:
    svc, repo, _ = _wiring()
    past = datetime.now(timezone.utc) - timedelta(days=1)

    await mint_bootstrap_key(svc, repo, org_id=_ORG, expires_at=past)
    second, _ = await mint_bootstrap_key(svc, repo, org_id=_ORG)

    assert second.expires_at is None


# ── the pure predicate the guard is built on ──────────────────────────────────

async def test_active_bootstrap_keys_counts_only_live_matching_rows() -> None:
    svc, repo, _ = _wiring()
    now = datetime.now(timezone.utc)

    live, _ = await mint_bootstrap_key(svc, repo, org_id=_ORG)
    await mint_bootstrap_key(
        svc, repo, org_id=_ORG, expires_at=now - timedelta(seconds=1), force=True
    )
    await svc.issue(
        org_id=_ORG, name="other", scopes=[], created_by="u", correlation_id=uuid4()
    )

    active = active_bootstrap_keys(
        await repo.list_for_org(_ORG), name=BOOTSTRAP_KEY_NAME, now=now
    )

    assert [r.key_id for r in active] == [live.key_id]


# ── the tenant precondition ───────────────────────────────────────────────────
#
# api_keys.org_id and users.org_id are both FOREIGN KEYS onto tenants(org_id)
# (migration 0001), which is what makes a fresh deployment a deadlock rather
# than just a missing credential: the route that creates a tenant needs a key,
# the route that registers a user needs a tenant, and the route that mints a key
# needs a key. These pin the escape hatch.

def _tenants() -> tuple[TenantService, InMemoryTenantRepository]:
    repo = InMemoryTenantRepository()
    return TenantService(repo, AuditService(InMemoryEventBus(), InMemoryAuditRepository())), repo


async def test_missing_tenant_is_refused_unless_create_is_asked_for() -> None:
    """--org-id is free text, so a typo must not silently create a second tenant."""
    tenants, repo = _tenants()

    with pytest.raises(TenantMissing) as exc:
        await ensure_tenant(tenants, org_id=_ORG, display_name=_ORG, create=False)

    assert "--create-tenant" in str(exc.value)
    assert await repo.get_tenant(_ORG) is None, "a refusal must write nothing"


async def test_create_tenant_provisions_an_active_org() -> None:
    tenants, repo = _tenants()

    created = await ensure_tenant(
        tenants, org_id=_ORG, display_name="Acme Inc", create=True
    )

    assert created is True
    row = await repo.get_tenant(_ORG)
    assert row is not None
    assert row.display_name == "Acme Inc"
    assert row.status == "active"


async def test_ensure_tenant_is_idempotent_and_leaves_an_existing_org_alone() -> None:
    """Re-running after a partial failure must not rename or reset the tenant."""
    tenants, repo = _tenants()
    await ensure_tenant(tenants, org_id=_ORG, display_name="Acme Inc", create=True)

    created = await ensure_tenant(
        tenants, org_id=_ORG, display_name="Something Else", create=True
    )

    assert created is False
    row = await repo.get_tenant(_ORG)
    assert row is not None
    assert row.display_name == "Acme Inc", "an existing tenant must not be rewritten"
