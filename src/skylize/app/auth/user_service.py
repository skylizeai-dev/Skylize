"""Human-user authentication: register, login, refresh, verify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import logging

from ...config import Settings
from ...dal.ports import RefreshTokenRow, UserRepository, UserRow
from ..principal.provider import PrincipalProvisioner
from .passwords import hash_password, verify_password
from .tokens import InvalidTokenError, create_access_token, create_refresh_token, decode_token

VALID_ROLES = frozenset({"owner", "admin", "operator", "viewer"})


class DuplicateEmailError(Exception):
    pass


class OrgNotAvailableError(Exception):
    """Registration was refused: the org_id cannot be used to create a NEW org.

    Either the org already has at least one user, or a concurrent registration
    won the owner race. Carries no detail about which — the caller only needs to
    know the identifier is unavailable.
    """


class InvalidCredentialsError(Exception):
    pass


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str
    user: UserRow


class UserAuthService:
    def __init__(
        self,
        repo: UserRepository,
        settings: Settings,
        provisioner: PrincipalProvisioner | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._provisioner = provisioner

    async def register(
        self,
        *,
        org_id: str,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> UserRow:
        """Create a NEW organisation and its owner. Refuses an existing org.

        This endpoint is unauthenticated, so what it is allowed to do bounds what
        a stranger can do. It previously admitted any subsequent registrant to an
        EXISTING org as `viewer` — a role accepted on `GET /api/v1/agents` and on
        every deliverable read route — so anyone who knew an org_id could read
        that tenant's deliverables. Registration now mints an owner for an org
        that has no users, or refuses.

        The refusal is decided by the STORE, not here: `create_owner_of_new_org`
        is an atomic conditional write backed by a unique constraint, so two
        simultaneous registrations for the same new org cannot both become owner.
        A read-then-write in this method could not make that guarantee.
        """
        existing = await self._repo.get_by_email(email)
        if existing is not None:
            raise DuplicateEmailError(f"email already registered: {email}")

        now = datetime.now(timezone.utc)
        row = UserRow(
            user_id=uuid4(),
            org_id=org_id,
            # Always owner: this path creates the org, and there is no other role
            # it could legitimately mint for an org that does not exist yet.
            roles=["owner"],
            email=email.lower().strip(),
            password_hash=hash_password(password),
            display_name=display_name,
            is_active=True,
            created_at=now,
            last_login_at=None,
        )
        if not await self._repo.create_owner_of_new_org(row):
            raise OrgNotAvailableError(org_id)

        await self._provision_principal(row)
        return row

    async def _provision_principal(self, row: UserRow) -> None:
        """Give the new owner a principal so the co-work surface is reachable.

        WHY THIS RUNS AT ALL. Without a `principal` row,
        `PrincipalAuthorityService.snapshot_for` raises `PrincipalNotFound` for
        this user (app/principal/provider.py:71-75) — correctly, since absence of
        a principal record is a denial and never a grant. The owner would be able
        to register and log in, and every co-work turn would be refused. Migration
        0020 seeds principals for owners who exist when it runs; nobody who
        registers AFTERWARDS gets one, which is exactly the hole backfill
        migration 0031 had to repair once already.

        WHAT THIS DELIBERATELY DOES NOT DO. It does not create the tenant. This
        registration path still requires the `tenants` row to exist already
        (`users.org_id` is a FK onto it, migration 0001), which today means an
        operator created it out-of-band via `python -m skylize.ops.bootstrap_api_key
        --create-tenant`. Unauthenticated tenant creation stays closed; this
        method narrows the gap for owners of operator-provisioned orgs only, and
        an org with no tenant row still fails at `create_owner_of_new_org` with a
        ForeignKeyViolationError, before this is ever reached.

        TRANSACTION SAFETY — the deliberate choice, stated plainly. The user
        INSERT and this write are two separate statements on two separate
        connections and are NOT atomic. That is forced, not overlooked: the user
        write goes through `admin_session` (no tenant GUC — `users` has no RLS
        policy keyed on one), while `principal` carries FORCE ROW LEVEL SECURITY
        whose WITH CHECK requires `skylize.org_id` to be set (migration 0019), so
        it MUST go through `tenant_session`. Each helper acquires its own
        connection from the pool (dal/connection.py:71-88), so one transaction
        cannot span both without restructuring `Database` to hand out a
        caller-managed connection — a change to the single module every query in
        the system flows through, which is more than this task should do
        unilaterally.

        The sequence is instead made CRASH-RECOVERABLE, which is what atomicity
        would have bought here:

          * the user row is the sole source of truth — it is written first and,
            once written, the registration is real;
          * `provision_owner_principal` is idempotent (ON CONFLICT / NOT EXISTS,
            dal/principal.py), so re-running it after a crash converges;
          * a failure HERE is logged and swallowed rather than raised, because
            raising would return an error for a registration that actually
            succeeded — the user exists and can log in — and would invite the
            caller to retry with the same email, which now fails as a duplicate.
            The recoverable state is "user without principal"; the unrecoverable
            one would be a user who believes registration failed.

        The residual gap is therefore bounded and self-announcing: an owner whose
        principal write failed can log in but is denied co-work, and migration
        0031's query — or simply re-running it — repairs exactly that state. No
        silent data loss, and no authority granted by accident.
        """
        if self._provisioner is None:
            return
        try:
            await self._provisioner.provision_owner_principal(
                org_id=row.org_id,
                # The identity rule fixed by migration 0020 and repeated in 0031:
                # principal_id IS users.user_id rendered as text, because
                # `RequestContext.user_id` is the JWT `sub` (edge/deps.py:75)
                # minted from this same column (edge/routes/auth.py:136). Any
                # other derivation silently splits the two identity spaces.
                principal_id=str(row.user_id),
                display_name=(row.display_name or "").strip() or row.email,
            )
        except Exception:
            # Swallowed by design — see the docstring. The user row is already
            # committed; surfacing this would misreport a successful registration.
            logging.getLogger(__name__).exception(
                "registration succeeded but principal provisioning failed for "
                "user_id=%s org_id=%s; the owner can log in but co-work will be "
                "denied until a principal is provisioned (re-runnable: "
                "migration 0031 / provision_owner_principal)",
                row.user_id,
                row.org_id,
            )

    async def login(self, *, email: str, password: str) -> LoginResult:
        user = await self._repo.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("invalid email or password")
        if not user.is_active:
            raise InvalidCredentialsError("account is inactive")

        await self._repo.update_last_login(user.user_id, datetime.now(timezone.utc))
        return await self._mint_pair(user)

    async def refresh(self, *, refresh_token: str) -> LoginResult:
        try:
            claims = decode_token(refresh_token, self._settings.jwt_secret)
        except InvalidTokenError as exc:
            raise InvalidCredentialsError("invalid refresh token") from exc

        if claims.get("type") != "refresh":
            raise InvalidCredentialsError("not a refresh token")

        jti = UUID(claims["jti"])
        rt_row: RefreshTokenRow | None = await self._repo.get_refresh_token(jti)
        if rt_row is None or rt_row.revoked_at is not None:
            raise InvalidCredentialsError("refresh token revoked or not found")
        if rt_row.expires_at <= datetime.now(timezone.utc):
            raise InvalidCredentialsError("refresh token expired")

        # Rotate: revoke the consumed token before issuing the new pair.
        await self._repo.revoke_refresh_token(jti)

        user = await self._repo.get_by_id(UUID(claims["sub"]))
        if user is None or not user.is_active:
            raise InvalidCredentialsError("user not found or inactive")

        return await self._mint_pair(user)

    async def get_user(self, user_id: UUID) -> UserRow | None:
        return await self._repo.get_by_id(user_id)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _mint_pair(self, user: UserRow) -> LoginResult:
        secret = self._settings.jwt_secret
        access = create_access_token(
            user_id=str(user.user_id),
            org_id=user.org_id,
            roles=user.roles,
            secret=secret,
            ttl_minutes=self._settings.jwt_access_token_ttl_minutes,
        )
        token_id = uuid4()
        refresh = create_refresh_token(
            user_id=str(user.user_id),
            token_id=token_id,
            secret=secret,
            ttl_days=self._settings.jwt_refresh_token_ttl_days,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self._settings.jwt_refresh_token_ttl_days
        )
        await self._repo.store_refresh_token(
            token_id=token_id, user_id=user.user_id, expires_at=expires_at
        )
        return LoginResult(access_token=access, refresh_token=refresh, user=user)
