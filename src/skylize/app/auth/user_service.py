"""Human-user authentication: register, login, refresh, verify."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final
from uuid import UUID, uuid4

import logging

from ...config import Settings
from ...dal.ports import RefreshTokenRow, UserRepository, UserRow
from ..principal.provider import PrincipalProvisioner
from .passwords import hash_password, verify_password
from .tokens import InvalidTokenError, create_access_token, create_refresh_token, decode_token

VALID_ROLES = frozenset({"owner", "admin", "operator", "viewer"})

#: Refresh-token replay grace window, in seconds (design doc 9.0.2, 9.1). When
#: a refresh is refused because its token was ALREADY CONSUMED by its owner,
#: the time since that consume decides what the refusal means:
#:   * less than this: the benign concurrent refresh - two tabs, or two BFF
#:     instances, presenting one cookie at once, and the other request won the
#:     rotation. Plain refusal, no log, no write.
#:   * this long or longer: reuse (a stolen token, a lost rotation response, a
#:     logged-out token). Still a plain refusal, plus the live rows of the
#:     presented token's FAMILY - its own login's lineage, and nothing else -
#:     are revoked and ONE WARNING `auth_refresh_reuse_family_revoked` is
#:     logged. Never a user-wide revocation (owner ruling R-B: PR #47's
#:     user-wide variant re-triggered on rows it had itself revoked and
#:     cascaded logouts across every device of the user).
REFRESH_REUSE_GRACE_SECONDS: Final = 10

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshReplay:
    """A refused refresh whose token its owner had ALREADY consumed - by a
    rotation, by logout, or by a family revocation; `revoked_at` does not say
    which."""

    #: `now - revoked_at`: how long ago the presented token was consumed.
    consumed_age: timedelta

    @property
    def within_grace(self) -> bool:
        return self.consumed_age < timedelta(seconds=REFRESH_REUSE_GRACE_SECONDS)


def classify_refused_refresh(
    row: RefreshTokenRow | None, sub: UUID, now: datetime
) -> RefreshReplay | None:
    """Classify a refresh the store refused. Pure: no I/O, no side effect.

    None means "not a replay": the token is unknown, its row belongs to a user
    other than `sub`, it was never consumed, or it is past its own expiry. The
    plain refusal is all there is to it. Otherwise the token's owner consumed
    it earlier, and the `RefreshReplay` says how long ago, inside or past the
    grace window.
    """
    if row is None or row.user_id != sub or row.revoked_at is None:
        return None
    if row.expires_at <= now:
        return None
    return RefreshReplay(consumed_age=now - row.revoked_at)


class DuplicateEmailError(Exception):
    pass


class OrgNotAvailableError(Exception):
    """Registration was refused: the org_id cannot be used to create a NEW org.

    Either the org already has at least one user, or a concurrent registration
    won the owner race. Carries no detail about which — the caller only needs to
    know the identifier is unavailable.
    """


class InvalidCredentialsError(Exception):
    """Credentials or a token were refused.

    The message is a FIXED, distinct reason for server-side logging only and
    must never interpolate a token (or any part of one). POST /refresh does not
    echo it: every refusal there answers one fixed 401 detail
    (edge/routes/auth.py `REFRESH_REFUSED_DETAIL`).
    """


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._provisioner = provisioner
        # Used only to age a consumed refresh token against the replay grace
        # window (`classify_refused_refresh`); injectable so tests need not sleep.
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

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

    def _refresh_claims(self, refresh_token: str) -> tuple[UUID, UUID]:
        """Verify a refresh token's signature and type; return (jti, sub).

        Raises InvalidCredentialsError for anything that is not a well-formed,
        validly signed refresh token minted by this server.
        """
        try:
            claims = decode_token(refresh_token, self._settings.jwt_secret)
        except InvalidTokenError as exc:
            raise InvalidCredentialsError("invalid refresh token") from exc

        if claims.get("type") != "refresh":
            raise InvalidCredentialsError("not a refresh token")
        try:
            return UUID(str(claims["jti"])), UUID(str(claims["sub"]))
        except (KeyError, ValueError) as exc:
            raise InvalidCredentialsError("invalid refresh token") from exc

    async def refresh(self, *, refresh_token: str) -> LoginResult:
        jti, sub = self._refresh_claims(refresh_token)

        # The user is read BEFORE the rotation (it used to be read after the
        # consume) so that the consume and the insert of the new token can be one
        # transaction (design doc 9.0.1, Round 2 N1). Consequence: an inactive or
        # deleted user's token is refused without being consumed - harmless, every
        # later attempt is refused by this same check.
        user = await self._repo.get_by_id(sub)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("user not found or inactive")

        # Rotate. `rotate_refresh_token` consumes with ONE conditional write: of
        # two concurrent refreshes with the same token, exactly one wins and the
        # other is refused. The former read-then-revoke let both through and
        # minted two live token families (design doc 1.0.2). Ownership (`sub`
        # must own the `jti` row) is inside the same condition, so a mismatched
        # token is refused WITHOUT consuming the other user's row. The consume
        # and the INSERT of the new row commit together or not at all.
        pair, token_id, expires_at = self._new_pair(user)
        if await self._repo.rotate_refresh_token(
            old_token_id=jti, user_id=sub, new_token_id=token_id, new_expires_at=expires_at
        ):
            return pair

        await self._on_refused_refresh(jti, sub)
        raise InvalidCredentialsError("refresh token revoked, expired, or not found")

    async def _on_refused_refresh(self, jti: UUID, sub: UUID) -> None:
        """Reuse handling for a refused refresh (design doc 9.0.2, 9.1).

        Not a replay, or a replay inside the grace window (the benign
        concurrent refresh): nothing at all - no log, no write. A replay past
        the grace window: the live rows of the presented token's family are
        revoked (`revoke_family`) and one structured WARNING is logged. Either
        way the caller raises the same `InvalidCredentialsError`.

        The revocation is scoped to ONE family, the presented row's own, which
        is one login's lineage of one user. It can never reach another device's
        session (another login = another family) or another user. A later
        replay of a token this revoked lands back in the same, now dead, family
        and revokes nothing more (`revoked_count=0`): the cascade of PR #47's
        user-wide variant (owner ruling R-B) has no path here.
        """
        row = await self._repo.get_refresh_token(jti)
        replay = classify_refused_refresh(row, sub, self._clock())
        # `row is None` already makes `replay` None; it is repeated only so the
        # type checker knows `row` below is a row.
        if row is None or replay is None or replay.within_grace:
            return
        revoked_count = await self._repo.revoke_family(row.family_id)
        # Ids, counts, the age and the window - never the token or any part of it.
        _log.warning(
            "auth_refresh_reuse_family_revoked user_id=%s token_id=%s family_id=%s "
            "consumed_age_seconds=%d grace_seconds=%d revoked_count=%d",
            sub, jti, row.family_id, int(replay.consumed_age.total_seconds()),
            REFRESH_REUSE_GRACE_SECONDS, revoked_count,
        )

    async def logout(self, *, refresh_token: str) -> bool:
        """Revoke the refresh token's own row. Never raises for a bad token.

        Proof of possession of the refresh token IS the authorization: only its
        own `jti` can be revoked, and no user or token id is taken from the
        caller. Returns True when a live token was revoked, False otherwise
        (invalid signature, wrong type, unknown, expired, or already revoked).
        The route answers 204 either way, so the result is for logging only.
        """
        try:
            jti, sub = self._refresh_claims(refresh_token)
        except InvalidCredentialsError:
            return False
        return await self._repo.consume_refresh_token(jti, sub)

    async def get_user(self, user_id: UUID) -> UserRow | None:
        return await self._repo.get_by_id(user_id)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _mint_pair(self, user: UserRow) -> LoginResult:
        pair, token_id, expires_at = self._new_pair(user)
        await self._repo.store_refresh_token(
            token_id=token_id, user_id=user.user_id, expires_at=expires_at
        )
        return pair

    def _new_pair(self, user: UserRow) -> tuple[LoginResult, UUID, datetime]:
        """Sign a fresh access/refresh pair. Persists NOTHING: the caller stores
        the refresh row (login) or rotates onto it (refresh)."""
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
        pair = LoginResult(access_token=access, refresh_token=refresh, user=user)
        return pair, token_id, expires_at
