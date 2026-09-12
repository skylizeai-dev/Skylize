"""Mint the FIRST API key for an org, from the operator's shell.

    python -m skylize.ops.bootstrap_api_key --org-id acme

THE GAP THIS CLOSES. ``POST /api/v1/api-keys`` is gated by
``require_any_role("owner", "admin")``, which resolves through ``get_context``
(``edge/deps.py:81-94``). On a postgres deployment that dependency accepts
exactly two credentials: an existing API key, or an OIDC JWT --
``build_request_context`` refuses the ``X-Dev-*`` headers unless
``settings.dev_auth``, and ``dev_auth`` is itself forbidden on any non-memory
backend (``config.py:365-375``, ``_forbid_dev_auth_on_a_real_backend``). So a
fresh deployment with no OIDC provider has no way to mint its first key: the
route that issues keys already requires one. Every ``/api/console/*`` BFF route
in ``website/`` depends on that key, so the gap blocks the whole console, not
just one route.

WHY A CLI AND NOT A ROUTE. Two precedents in this repo point the same way.

  1. Privileged secret material is already minted by operational entrypoints,
     never by HTTP: ``scripts/gen_governance_key.py`` and
     ``scripts/gen_wif_signing_key.py`` both generate a key, write it ONCE to
     stdout, persist nothing, and tell the operator to inject it through a
     secrets manager. This command is the same shape for a different secret.
  2. The repo has already been burned by the alternative. ``POST
     /api/v1/auth/register`` is the one unauthenticated "first X per org"
     surface, and its docstring (``edge/routes/auth.py:94-115``) records that it
     previously admitted any stranger who knew an org_id into that tenant as
     ``viewer``, and that it had to be narrowed to new-orgs-only. It closes with
     "Do not reopen this route". An env-gated first-key-per-org exception would
     rebuild that same surface with a strictly worse blast radius -- an
     owner-scoped API key rather than a viewer role -- and an env flag left on
     after setup is exactly the failure that class of route invites. A command
     that is not routed cannot be probed, cannot be hit by accident, and cannot
     be left enabled.

Seeding the key from a migration was the third option and is also against
precedent: the migrations do pure DDL and seed no data, and a key in a migration
is a key in the repository.

THE DEADLOCK IS WIDER THAN THE KEY, which is why ``--create-tenant`` exists.
``api_keys.org_id`` and ``users.org_id`` are both FOREIGN KEYS onto
``tenants(org_id)`` (migration 0001), so on a fresh database all three doors are
locked behind each other:

  * ``POST /api/v1/tenants`` creates the tenant row -- but is gated by
    ``get_context``, so it needs a key or an OIDC token;
  * ``POST /api/v1/auth/register`` needs no credential, but its INSERT into
    ``users`` violates the FK until a tenant row exists;
  * ``POST /api/v1/api-keys`` needs a key, and its INSERT would violate the same
    FK anyway.

So minting a key alone would still fail with a ForeignKeyViolationError on a
genuinely empty deployment. ``--create-tenant`` provisions the ``tenants`` row
through the ordinary ``TenantService.register`` first. It is an explicit flag
rather than implicit behaviour because ``--org-id`` is free text: silently
creating a tenant would turn a typo into a second, permanent, empty tenant.

WHAT IT DOES NOT REINVENT. The key is minted by ``ApiKeyService.issue``
(``app/auth/service.py:30-72``) -- the same call the route makes -- so the
rendering (``sky.<prefix>.<secret>``), the SHA-256-of-secret storage format
(``security/api_keys.py:34-36``), and the ``apikey.issued`` audit row are
identical to a key issued over HTTP by construction, not by imitation. There is
no second hashing scheme here and there must never be one.

OUTPUT DISCIPLINE. The plaintext key is written to STDOUT and nothing else:
every status line, warning and error goes to stderr, so piping stdout into a
secrets manager carries the key alone. It is never logged and never written to
disk by this command. It exists in the database only as a SHA-256 hash; if the
operator loses the printed value it is unrecoverable and a new key must be
minted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..app.audit.service import AuditService
from ..app.auth.service import ApiKeyService
from ..app.tenants.service import TenantService
from ..bootstrap import verify_app_role_is_rls_subject
from ..config import Settings, get_settings
from ..dal.connection import Database
from ..dal.ports import ApiKeyRepository, ApiKeyRow
from ..dal.repositories import PgApiKeyRepository, PgAuditRepository, PgTenantRepository
from ..events.memory_bus import InMemoryEventBus
from ..memory.identity import InvalidIdentifier, validate_identifier

#: The ``name`` that marks a key as this command's product. It is the whole
#: idempotency handle: "has this org been bootstrapped?" is answered by looking
#: for a live key with this name, so a key minted under any other name (i.e. any
#: key issued normally through the route) neither blocks a bootstrap nor is
#: mistaken for one.
BOOTSTRAP_KEY_NAME = "bootstrap"

#: ``created_by`` for the audit trail. Not a user id -- it records that the
#: credential came from the operator's shell rather than from an authenticated
#: caller, which is precisely the distinction an auditor needs here.
CREATED_BY = "ops:bootstrap_api_key"

_DEFAULT_SCOPES = "owner"


class BootstrapRefused(Exception):
    """The org already holds a live bootstrap key and ``--force`` was not given."""


def active_bootstrap_keys(
    rows: Sequence[ApiKeyRow], *, name: str, now: datetime
) -> list[ApiKeyRow]:
    """The bootstrap keys that a caller could still authenticate with.

    Revoked and expired keys are deliberately NOT counted. Both are states an
    operator reaches on purpose -- revoking a leaked bootstrap key, or letting a
    time-boxed one lapse -- and in both the org is back to having no usable
    credential, which is exactly the situation this command exists to fix.
    Counting them would make the refusal unrecoverable without ``--force``.
    """
    return [
        r
        for r in rows
        if r.name == name
        and r.revoked_at is None
        and (r.expires_at is None or r.expires_at > now)
    ]


class TenantMissing(Exception):
    """No ``tenants`` row for the org, and ``--create-tenant`` was not given."""


async def ensure_tenant(
    tenants: TenantService,
    *,
    org_id: str,
    display_name: str,
    create: bool,
) -> bool:
    """Make the org's ``tenants`` row exist. Returns True if this call created it.

    Idempotent by design: an org that is already provisioned is left exactly as
    it is, so re-running the command after a partial failure is safe and never
    touches an existing tenant's display name or status.
    """
    if await tenants.get(org_id) is not None:
        return False
    if not create:
        raise TenantMissing(
            f"no tenant row exists for org {org_id!r}, and api_keys.org_id is a "
            "foreign key onto tenants(org_id) -- minting would fail. Re-run with "
            f'--create-tenant --display-name "..." to provision it, or correct '
            "the --org-id if this was a typo."
        )
    await tenants.register(
        org_id=org_id,
        display_name=display_name,
        owner_user_id=CREATED_BY,
        correlation_id=uuid4(),
    )
    return True


async def mint_bootstrap_key(
    service: ApiKeyService,
    repo: ApiKeyRepository,
    *,
    org_id: str,
    name: str = BOOTSTRAP_KEY_NAME,
    scopes: Sequence[str] = ("owner",),
    expires_at: datetime | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[ApiKeyRow, str]:
    """Issue a bootstrap key for ``org_id``; return the row and the one-time secret.

    Raises ``BootstrapRefused`` when a live bootstrap key already exists and
    ``force`` is false. The check is read-then-write and therefore NOT atomic:
    two operators running this concurrently against the same org could both be
    admitted. That is accepted deliberately -- unlike the registration race this
    mirrors (``user_service.register``, where the loser must not silently become
    an owner), the loser here mints a second audited key that an operator can
    see and revoke, and the command is run by hand on a fresh deployment rather
    than served to anonymous callers.
    """
    moment = now or datetime.now(timezone.utc)
    if not force:
        existing = active_bootstrap_keys(
            await repo.list_for_org(org_id), name=name, now=moment
        )
        if existing:
            listed = ", ".join(
                f"{r.prefix} (created {r.created_at:%Y-%m-%d})" for r in existing
            )
            raise BootstrapRefused(
                f"org {org_id!r} already has {len(existing)} live bootstrap "
                f"key(s) named {name!r}: {listed}. The plaintext of an existing "
                "key cannot be recovered -- only its SHA-256 hash is stored. If "
                "it is lost or leaked, revoke it (DELETE /api/v1/api-keys/<key_id>) "
                "and re-run, or pass --force to mint an ADDITIONAL key while the "
                "existing one stays live."
            )
    return await service.issue(
        org_id=org_id,
        name=name,
        scopes=list(scopes),
        created_by=CREATED_BY,
        correlation_id=uuid4(),
        expires_at=expires_at,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m skylize.ops.bootstrap_api_key",
        description=(
            "Mint the first API key for an org, directly against the database. "
            "The plaintext key is printed to stdout exactly once and is not "
            "recoverable afterwards."
        ),
    )
    parser.add_argument("--org-id", required=True, help="tenant to mint the key for")
    parser.add_argument(
        "--scopes",
        default=_DEFAULT_SCOPES,
        help=(
            "comma-separated scopes (default: owner). A key's scopes BECOME its "
            "roles (app/auth/service.py:88-95), so --scopes owner is what "
            "satisfies the owner-only routes the console proxy calls."
        ),
    )
    parser.add_argument(
        "--name",
        default=BOOTSTRAP_KEY_NAME,
        help=f"key name, also the idempotency handle (default: {BOOTSTRAP_KEY_NAME})",
    )
    parser.add_argument(
        "--expires-in-days",
        type=int,
        default=None,
        help=(
            "expire the key after N days. Omitted means no expiry, matching the "
            "route's default; prefer setting it and minting real keys with the "
            "bootstrap one."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="mint an additional bootstrap key even though a live one exists",
    )
    parser.add_argument(
        "--create-tenant",
        action="store_true",
        help=(
            "provision the tenants row first if it is absent. Required on a "
            "genuinely fresh database: api_keys.org_id is a foreign key onto "
            "tenants(org_id). No-op when the tenant already exists."
        ),
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="display name for --create-tenant (defaults to the org id)",
    )
    return parser.parse_args(argv)


def _validated_org_id(raw: str) -> str:
    """Apply the same slug rule registration applies when it mints an org_id.

    ``org_id`` is a tenant-isolation key, so a value carrying ``:`` or other
    separators could forge a namespace boundary. ``edge/routes/auth.py:44-56``
    normalizes case and delegates to ``memory.identity`` for exactly this
    reason; a credential minted here lands in the same isolation domain and
    gets the same rule rather than a second, looser one.
    """
    try:
        return validate_identifier(raw.lower(), field="org_id")
    except InvalidIdentifier as exc:
        raise SystemExit(f"error: {exc}") from exc


async def _run(args: argparse.Namespace, settings: Settings) -> str:
    """Compose the minimum needed to mint a key, and return the plaintext."""
    if settings.backend != "postgres":
        raise SystemExit(
            f"error: SKYLIZE_BACKEND is {settings.backend!r}, not 'postgres'. This "
            "command writes a durable credential; on the memory backend it would "
            "mint a key into a store that dies with the process."
        )

    org_id = _validated_org_id(args.org_id)
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    if not scopes:
        raise SystemExit("error: --scopes resolved to an empty list")

    expires_at: datetime | None = None
    if args.expires_in_days is not None:
        if args.expires_in_days < 1:
            raise SystemExit("error: --expires-in-days must be >= 1")
        expires_at = datetime.now(timezone.utc) + timedelta(days=args.expires_in_days)

    # Own concretes rather than `build_container`, following the decision-engine
    # worker (decision_engine/worker.py:26-31): a separate process composes what
    # it needs. Minting a key must not require Redis, OPA or an LLM provider to
    # be reachable, which the full container does.
    db = Database(settings.runtime_db_url)
    await db.connect()
    try:
        # Same interlock the API applies at startup. An operator who points this
        # at the superuser/migration DSN would write the audit row with RLS
        # inert; refuse instead, exactly as bootstrap.build_container does.
        await verify_app_role_is_rls_subject(db)
        # The durable audit record is the `repo.append` inside
        # AuditService.record (app/audit/service.py:79-95), which lands in
        # Postgres. The bus publish on the line above it is live fan-out, and a
        # one-shot command has no subscribers -- so an in-memory bus costs
        # nothing and spares this command a Redis dependency. The
        # `apikey.issued` row is still written.
        audit = AuditService(InMemoryEventBus(), PgAuditRepository(db))
        created_tenant = await ensure_tenant(
            TenantService(PgTenantRepository(db), audit),
            org_id=org_id,
            display_name=args.display_name or org_id,
            create=args.create_tenant,
        )
        repo = PgApiKeyRepository(db)
        service = ApiKeyService(repo, audit)
        row, secret = await mint_bootstrap_key(
            service,
            repo,
            org_id=org_id,
            name=args.name,
            scopes=scopes,
            expires_at=expires_at,
            force=args.force,
        )
    finally:
        await db.close()

    expiry = (
        f" Expires {row.expires_at:%Y-%m-%d %H:%M UTC}."
        if row.expires_at is not None
        else " No expiry."
    )
    if created_tenant:
        print(f"Provisioned tenant {org_id!r}.", file=sys.stderr)
    print(
        f"Minted API key {row.key_id} (prefix {row.prefix}) for org {org_id!r} "
        f"with scopes {scopes}.{expiry}",
        file=sys.stderr,
    )
    if args.force:
        print(
            "warning: --force was used. Any pre-existing bootstrap key is STILL "
            "LIVE; revoke it deliberately via DELETE /api/v1/api-keys/<key_id>.",
            file=sys.stderr,
        )
    print(
        "The key below is shown once and is not recoverable -- only its SHA-256 "
        "hash is stored. Put it straight into your secrets manager.",
        file=sys.stderr,
    )
    return secret


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        secret = asyncio.run(_run(args, get_settings()))
    except (BootstrapRefused, TenantMissing) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # The one and only emission of plaintext key material. stdout carries the key
    # and nothing else, so redirecting stdout captures the key alone.
    sys.stdout.write(secret + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
