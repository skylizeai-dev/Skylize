"""Registration provisions the new owner's principal — and nothing more.

The hole this closes: `PrincipalAuthorityService.snapshot_for` raises
`PrincipalNotFound` when no `principal` row exists (app/principal/provider.py),
which is a denial, so an owner without one can log in and is then refused every
co-work turn. Migration 0020 seeds principals for owners who exist when it runs;
anyone registering afterwards got nothing, which is the state backfill migration
0031 had to repair on the live database.

The negative tests here matter as much as the positive ones. Registration must
NOT have become able to create a tenant as a side effect of this change — that
is Option 1, explicitly declined by the owner — and it must NOT create a
`spend_envelope`, because a principal existing must never imply that a budget
was configured.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from skylize.app.auth.user_service import UserAuthService
from skylize.app.principal.models import COWORK_SEED_MANIFEST, GrantSource
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
    PrincipalProvisioner,
    PrincipalRepository,
)
from skylize.config import Settings
from skylize.dal.memory import InMemoryUserRepository


def _settings() -> Settings:
    return Settings(jwt_secret="test-secret", jwt_access_token_ttl_minutes=15)


def _service(
    repo: InMemoryUserRepository | None = None,
    principals: InMemoryPrincipalRepository | None = None,
) -> tuple[UserAuthService, InMemoryUserRepository, InMemoryPrincipalRepository]:
    users = repo or InMemoryUserRepository()
    prin = principals or InMemoryPrincipalRepository()
    return UserAuthService(users, _settings(), provisioner=prin), users, prin


async def test_registration_creates_a_principal_for_the_new_owner() -> None:
    svc, _users, prin = _service()
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    principal = await prin.load_principal(
        org_id="org_a", principal_id=str(user.user_id)
    )
    assert principal is not None, "a registered owner must be known to the kernel"
    assert principal.org_id == "org_a"
    assert principal.authority_level == "executive"


async def test_principal_id_is_the_user_id_rendered_as_text() -> None:
    """The identity rule fixed by migration 0020 and repeated in 0031.

    `RequestContext.user_id` is the JWT `sub`, minted from `users.user_id`, so a
    request context becomes a principal lookup with no mapping table. Any other
    derivation silently splits the two identity spaces — which is precisely the
    failure this asserts against, not a formatting preference.
    """
    svc, _users, prin = _service()
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    principal = await prin.load_principal(
        org_id="org_a", principal_id=str(user.user_id)
    )
    assert principal is not None
    assert principal.principal_id == str(user.user_id)


async def test_owner_is_granted_exactly_the_cowork_manifest() -> None:
    """Exactly the co-work manifest — not "every scope in the system"."""
    svc, _users, prin = _service()
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    grants = await prin.load_grants(org_id="org_a", principal_id=str(user.user_id))
    assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST)
    assert {g.source for g in grants} == {GrantSource.POSITION}


async def test_granted_scopes_match_the_cowork_contract() -> None:
    """The seed is pinned to the contract by a test rather than by an import.

    `models.COWORK_SEED_MANIFEST` deliberately does not import the contract: a
    contract's `allowed_tools` may legitimately grow, and an import would widen
    every owner's standing authority as a silent side effect of a contract edit.
    This test is what turns that decoupling into caught drift instead of
    inherited drift.
    """
    from skylize.contracts.mvp.cowork import cowork_agent

    assert sorted(COWORK_SEED_MANIFEST) == sorted(
        g.tool_id for g in cowork_agent.allowed_tools
    )


async def test_the_new_owner_can_actually_resolve_authority() -> None:
    """The point of the whole change: co-work is reachable, not merely un-403'd.

    Asserting the rows exist is not enough — `snapshot_for` is what mint calls,
    and it is what raised `PrincipalNotFound` before.
    """
    svc, _users, prin = _service()
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    snapshot = await PrincipalAuthorityService(prin).snapshot_for(
        org_id="org_a",
        principal_id=str(user.user_id),
        at=datetime.now(timezone.utc),
    )
    assert sorted(snapshot.scopes) == sorted(COWORK_SEED_MANIFEST)


async def test_display_name_falls_back_to_email_when_blank() -> None:
    """Mirrors the migrations' COALESCE(NULLIF(btrim(display_name),''), email)."""
    svc, _users, prin = _service()
    user = await svc.register(
        org_id="org_a",
        email="Owner@Example.com",
        password="hunter2pw",
        display_name="   ",
    )

    principal = await prin.load_principal(
        org_id="org_a", principal_id=str(user.user_id)
    )
    assert principal is not None
    assert principal.display_name == "owner@example.com"


async def test_provisioning_is_idempotent() -> None:
    """A crash between the user write and this one must be repairable by retry.

    The two writes are not atomic (different connections, one RLS-bound), so
    convergence on re-run is the property that replaces atomicity.
    """
    prin = InMemoryPrincipalRepository()
    svc, _users, _ = _service(principals=prin)
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )
    pid = str(user.user_id)

    first = await prin.provision_owner_principal(
        org_id="org_a", principal_id=pid, display_name="Owner"
    )
    second = await prin.provision_owner_principal(
        org_id="org_a", principal_id=pid, display_name="Owner"
    )

    assert first is False, "registration already created it"
    assert second is False
    grants = await prin.load_grants(org_id="org_a", principal_id=pid)
    assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST), (
        "re-running must not duplicate grants"
    )


async def test_missing_grants_are_repaired_on_a_later_call() -> None:
    """A principal with no grants is a person the kernel knows who can do
    nothing. If a crash landed the principal and none of its grants, calling
    again must fill them — so the grant write is not keyed off "was created"."""
    prin = InMemoryPrincipalRepository()
    await prin.provision_owner_principal(
        org_id="org_a", principal_id="p1", display_name="Owner"
    )
    prin._grants[("org_a", "p1")] = []  # simulate the crash-torn state

    created = await prin.provision_owner_principal(
        org_id="org_a", principal_id="p1", display_name="Owner"
    )

    assert created is False
    grants = await prin.load_grants(org_id="org_a", principal_id="p1")
    assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST)


async def test_a_failed_principal_write_does_not_fail_the_registration() -> None:
    """The user row is the sole source of truth.

    Raising here would report failure for a registration that actually
    succeeded — the user exists and can log in — and would invite a retry that
    now fails as a duplicate email. The recoverable state is "user without
    principal"; the unrecoverable one would be a user who believes registration
    failed.
    """

    class Exploding:
        async def provision_owner_principal(
            self, *, org_id: str, principal_id: str, display_name: str
        ) -> bool:
            raise RuntimeError("database went away")

    users = InMemoryUserRepository()
    svc = UserAuthService(users, _settings(), provisioner=Exploding())

    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    assert user.roles == ["owner"]
    assert await users.get_by_email("owner@example.com") is not None, (
        "the committed user row must survive a principal-write failure"
    )


async def test_service_without_a_provisioner_still_registers() -> None:
    """The provisioner is optional so existing constructions keep working."""
    users = InMemoryUserRepository()
    svc = UserAuthService(users, _settings())

    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )
    assert user.roles == ["owner"]


async def test_principals_are_org_scoped() -> None:
    """Org A's owner is invisible to org B — the in-memory mirror of the RLS
    policy the durable side enforces."""
    prin = InMemoryPrincipalRepository()
    svc, _users, _ = _service(principals=prin)
    user = await svc.register(
        org_id="org_a", email="owner@example.com", password="hunter2pw"
    )

    assert (
        await prin.load_principal(org_id="org_b", principal_id=str(user.user_id))
        is None
    )


async def test_read_port_stays_read_only() -> None:
    """`PrincipalRepository` is what token minting depends on. A component that
    RESOLVES authority must not be able to GRANT it, so the write method lives on
    a separate port. This guards that separation against a future 'tidy-up' that
    merges the two protocols."""
    assert not hasattr(PrincipalRepository, "provision_owner_principal")
    assert hasattr(PrincipalProvisioner, "provision_owner_principal")


async def test_provisioning_creates_no_spend_envelope() -> None:
    """A principal existing must not imply a budget was configured.

    `spend_envelope` carries a ceiling and an `over_ceiling_behavior` that are
    governance decisions someone has to actually make. The in-memory principal
    store has no envelope concept at all, which is the parity assertion: the
    provisioning path touches principals and grants only.
    """
    prin = InMemoryPrincipalRepository()
    svc, _users, _ = _service(principals=prin)
    await svc.register(org_id="org_a", email="owner@example.com", password="hunter2pw")

    assert set(vars(prin)) == {"_principals", "_grants"}, (
        "provisioning must touch principals and grants only"
    )


async def test_registration_into_an_occupied_org_provisions_nothing() -> None:
    """A refused registration must leave no principal behind either — otherwise
    the refusal would still hand out standing authority."""
    prin = InMemoryPrincipalRepository()
    svc, _users, _ = _service(principals=prin)
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    before = dict(prin._principals)

    from skylize.app.auth.user_service import OrgNotAvailableError

    with pytest.raises(OrgNotAvailableError):
        await svc.register(org_id="org_a", email="b@example.com", password="hunter2pw")

    assert prin._principals == before
