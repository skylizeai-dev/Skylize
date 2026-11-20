"""Refresh-token single-use consume and POST /api/v1/auth/logout (memory backend).

Design: docs/06_integrations/myday_session_revocation_design.md. What these pin:
  * of two concurrent refreshes with ONE token, exactly one succeeds (the race
    that used to mint two live token families, design 1.0.2);
  * a rotated token cannot be refreshed again;
  * rotation is ONE atomic unit: a failed insert of the new token leaves the old
    token unconsumed and usable (Round 2 N1, design 9.0.1);
  * a replay of a consumed token is DETECTION ONLY (design 9.0.2): inside the
    10 s grace window nothing is logged or written; past it, one WARNING
    `auth_refresh_replay_after_grace`; in neither case is any token revoked;
  * two concurrent refreshes of ONE token through the backend alone (no BFF
    lock involved) leave one winner, one plain refusal, and nothing revoked;
  * logout revokes the presented refresh token, so a later refresh is 401;
  * logout is always 204 and never an oracle (garbage, wrong type, unknown,
    already revoked all answer the same);
  * logout only ever revokes the presented token's own row;
  * logout is a non-2xx when the revoke write itself fails (the BFF fails closed);
  * POST /refresh answers one fixed 401 detail for every refusal reason;
  * Settings refuses an access-token TTL above 15 minutes (OD3-AMEND).
The real-Postgres version of the race lives in
tests/integration/test_refresh_revocation_pg.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import skylize.config as config_module
from skylize.app.auth import user_service
from skylize.app.auth.tokens import create_refresh_token, decode_token
from skylize.app.auth.user_service import (
    REFRESH_REUSE_GRACE_SECONDS,
    InvalidCredentialsError,
    LoginResult,
    RefreshReplay,
    UserAuthService,
    classify_refused_refresh,
)
from skylize.config import Settings
from skylize.dal.memory import InMemoryUserRepository
from skylize.dal.ports import RefreshTokenRow
from skylize.edge.gateway import create_app
from skylize.edge.routes.auth import REFRESH_REFUSED_DETAIL

SECRET = "test-secret"
REPLAY_EVENT = "auth_refresh_replay_after_grace"


class _Clock:
    """Wall clock plus a settable offset, so the grace window is crossed without
    sleeping. Only the SERVICE reads it (to age a consumed token); the store
    still stamps `revoked_at` and checks expiry with the real time."""

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def advance(self, seconds: float) -> None:
        self._offset += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return datetime.now(timezone.utc) + self._offset


def _service(clock: _Clock | None = None) -> tuple[UserAuthService, InMemoryUserRepository]:
    repo = InMemoryUserRepository()
    return UserAuthService(repo, Settings(jwt_secret=SECRET), clock=clock), repo


def _replay_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if REPLAY_EVENT in r.getMessage()]


def _rows(repo: InMemoryUserRepository) -> dict[UUID, RefreshTokenRow]:
    """Snapshot of every refresh row (frozen dataclasses, so == compares values)."""
    return dict(repo._refresh)


def _consumed_ids(repo: InMemoryUserRepository) -> set[UUID]:
    return {rt.token_id for rt in repo._refresh.values() if rt.revoked_at is not None}


async def _logged_in(svc: UserAuthService, org: str = "org_a", email: str = "a@example.com"):
    await svc.register(org_id=org, email=email, password="hunter2pw")
    return await svc.login(email=email, password="hunter2pw")


# ── the store primitive ──────────────────────────────────────────────────────

async def test_consume_is_single_use() -> None:
    repo = InMemoryUserRepository()
    token_id, user_id = uuid4(), uuid4()
    await repo.store_refresh_token(
        token_id, user_id, datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert await repo.consume_refresh_token(token_id, user_id) is True
    assert await repo.consume_refresh_token(token_id, user_id) is False


async def test_consume_refuses_expired_and_unknown() -> None:
    repo = InMemoryUserRepository()
    expired, owner = uuid4(), uuid4()
    await repo.store_refresh_token(
        expired, owner, datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert await repo.consume_refresh_token(expired, owner) is False
    assert await repo.consume_refresh_token(uuid4(), owner) is False


async def test_consume_by_a_non_owner_refuses_and_leaves_the_row_live() -> None:
    repo = InMemoryUserRepository()
    token_id, owner, stranger = uuid4(), uuid4(), uuid4()
    await repo.store_refresh_token(
        token_id, owner, datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert await repo.consume_refresh_token(token_id, stranger) is False
    row = await repo.get_refresh_token(token_id)
    assert row is not None and row.revoked_at is None
    # The real owner can still use it.
    assert await repo.consume_refresh_token(token_id, owner) is True


# ── refresh rotation ─────────────────────────────────────────────────────────

async def test_concurrent_refreshes_of_one_token_mint_exactly_one_pair() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)

    results = await asyncio.gather(
        svc.refresh(refresh_token=first.refresh_token),
        svc.refresh(refresh_token=first.refresh_token),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, InvalidCredentialsError)]
    assert len(ok) == 1, results
    assert len(refused) == 1, results


async def test_a_rotated_token_cannot_be_refreshed_again() -> None:
    """Reuse of a rotated token is refused and does NOT revoke the token family:
    the rotated-to token keeps working. Per-family revocation on reuse is a
    separate change (#47b, design doc 7.0 / 9.0.4), not part of this one."""
    svc, _ = _service()
    first = await _logged_in(svc)
    second = await svc.refresh(refresh_token=first.refresh_token)

    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.refresh_token)
    # The new token from the rotation is live.
    assert (await svc.refresh(refresh_token=second.refresh_token)).access_token


# ── Round 2: rotation is one atomic unit (N1) ────────────────────────────────

async def test_a_failed_insert_during_rotation_leaves_the_old_token_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, repo = _service()
    first = await _logged_in(svc)
    jti = UUID(decode_token(first.refresh_token, SECRET)["jti"])

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated failure storing the new refresh token")

    monkeypatch.setattr(repo, "_insert_refresh", _boom)
    with pytest.raises(RuntimeError, match="simulated failure"):
        await svc.refresh(refresh_token=first.refresh_token)

    row = await repo.get_refresh_token(jti)
    assert row is not None and row.revoked_at is None, "old token was consumed"
    monkeypatch.undo()
    assert (await svc.refresh(refresh_token=first.refresh_token)).access_token


async def test_rotate_refuses_a_non_consumable_token_and_writes_nothing() -> None:
    repo = InMemoryUserRepository()
    old, new, owner = uuid4(), uuid4(), uuid4()
    later = datetime.now(timezone.utc) + timedelta(days=1)
    await repo.store_refresh_token(old, owner, later)
    assert await repo.consume_refresh_token(old, owner) is True

    assert await repo.rotate_refresh_token(
        old_token_id=old, user_id=owner, new_token_id=new, new_expires_at=later
    ) is False
    assert await repo.get_refresh_token(new) is None


# ── #47b: token families (migration 0038, design 9.1) ────────────────────────

def _jti(token: str) -> UUID:
    return UUID(decode_token(token, SECRET)["jti"])


def _family(repo: InMemoryUserRepository, token: str) -> UUID:
    return repo._refresh[_jti(token)].family_id


async def test_store_refresh_token_roots_a_new_family_at_its_own_token_id() -> None:
    repo = InMemoryUserRepository()
    token_id, user_id = uuid4(), uuid4()
    await repo.store_refresh_token(
        token_id, user_id, datetime.now(timezone.utc) + timedelta(days=1)
    )
    row = await repo.get_refresh_token(token_id)
    assert row is not None and row.family_id == token_id


async def test_rotate_copies_the_consumed_rows_family_into_the_new_row() -> None:
    repo = InMemoryUserRepository()
    root, child, grandchild, owner = uuid4(), uuid4(), uuid4(), uuid4()
    later = datetime.now(timezone.utc) + timedelta(days=1)
    await repo.store_refresh_token(root, owner, later)

    assert await repo.rotate_refresh_token(
        old_token_id=root, user_id=owner, new_token_id=child, new_expires_at=later
    )
    assert await repo.rotate_refresh_token(
        old_token_id=child, user_id=owner, new_token_id=grandchild, new_expires_at=later
    )
    for token_id in (root, child, grandchild):
        row = await repo.get_refresh_token(token_id)
        assert row is not None and row.family_id == root, token_id


async def test_every_rotation_of_one_login_stays_in_its_family_and_two_logins_differ() -> None:
    """Family propagation through the service: after N rotations every token of
    ONE login shares that login's family (its first token's id), and a second
    login of the same user is a different family."""
    svc, repo = _service()
    first = await _logged_in(svc)
    second_login = await svc.login(email="a@example.com", password="hunter2pw")

    chain = [first]
    for _ in range(5):
        chain.append(await svc.refresh(refresh_token=chain[-1].refresh_token))
    root = _jti(first.refresh_token)
    assert {_family(repo, pair.refresh_token) for pair in chain} == {root}
    assert len({_jti(pair.refresh_token) for pair in chain}) == 6

    other_root = _jti(second_login.refresh_token)
    assert _family(repo, second_login.refresh_token) == other_root != root
    rotated_other = await svc.refresh(refresh_token=second_login.refresh_token)
    assert _family(repo, rotated_other.refresh_token) == other_root


async def test_revoke_family_revokes_only_that_familys_live_rows_and_is_idempotent() -> None:
    repo = InMemoryUserRepository()
    owner, stranger = uuid4(), uuid4()
    later = datetime.now(timezone.utc) + timedelta(days=1)
    fa_root, fa_live, fb_live, other_user = uuid4(), uuid4(), uuid4(), uuid4()
    expired = uuid4()
    await repo.store_refresh_token(fa_root, owner, later)
    assert await repo.rotate_refresh_token(
        old_token_id=fa_root, user_id=owner, new_token_id=fa_live, new_expires_at=later
    )
    await repo.store_refresh_token(fb_live, owner, later)
    await repo.store_refresh_token(other_user, stranger, later)
    await repo.store_refresh_token(expired, owner, datetime.now(timezone.utc) - timedelta(seconds=1))
    before = _rows(repo)

    assert await repo.revoke_family(fa_root) == 1
    after = _rows(repo)
    # Exactly one row changed: family A's live token. Its consumed root, the
    # user's other family, the other user's row and an expired row are as before.
    assert {k for k in after if after[k] != before[k]} == {fa_live}
    assert after[fa_live].revoked_at is not None
    assert await repo.revoke_family(fa_root) == 0, "a second revocation found a live row"
    assert await repo.revoke_family(uuid4()) == 0
    assert await repo.revoke_family(expired) == 0, "an expired row counted as live"
    assert _rows(repo) == after


# ── Round 2: replay grace window, DETECTION ONLY (design 9.0.2) ──────────────

_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
_GRACE = timedelta(seconds=REFRESH_REUSE_GRACE_SECONDS)


def _token_row(
    owner: UUID, *, consumed_ago: timedelta | None, expires_in: timedelta
) -> RefreshTokenRow:
    """A row as the store would return it at `_NOW` (for the pure classifier)."""
    token_id = uuid4()
    return RefreshTokenRow(
        token_id=token_id,
        user_id=owner,
        expires_at=_NOW + expires_in,
        family_id=token_id,
        revoked_at=None if consumed_ago is None else _NOW - consumed_ago,
    )


def test_the_classifier_says_not_a_replay_for_every_early_return() -> None:
    owner, stranger = uuid4(), uuid4()
    day = timedelta(days=1)
    long_ago = _GRACE * 100
    assert classify_refused_refresh(None, owner, _NOW) is None  # unknown token
    other = _token_row(stranger, consumed_ago=long_ago, expires_in=day)
    assert classify_refused_refresh(other, owner, _NOW) is None  # another user's row
    live = _token_row(owner, consumed_ago=None, expires_in=day)
    assert classify_refused_refresh(live, owner, _NOW) is None  # never consumed
    # Consumed AND past its own expiry: not a replay, however long ago.
    expired = _token_row(owner, consumed_ago=long_ago, expires_in=timedelta(0))
    assert classify_refused_refresh(expired, owner, _NOW) is None


def test_the_classifier_grace_boundary_is_exactly_the_window() -> None:
    owner = uuid4()
    day = timedelta(days=1)
    almost = _GRACE - timedelta(microseconds=1)
    just_inside = _token_row(owner, consumed_ago=almost, expires_in=day)
    at_the_edge = _token_row(owner, consumed_ago=_GRACE, expires_in=day)

    inside = classify_refused_refresh(just_inside, owner, _NOW)
    edge = classify_refused_refresh(at_the_edge, owner, _NOW)
    assert inside == RefreshReplay(consumed_age=almost)
    assert inside.within_grace is True
    assert edge == RefreshReplay(consumed_age=_GRACE)
    assert edge.within_grace is False


async def test_a_replay_within_grace_logs_nothing_and_writes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    svc, repo = _service(clock)
    first = await _logged_in(svc)
    sibling = await svc.login(email="a@example.com", password="hunter2pw")
    rotated = await svc.refresh(refresh_token=first.refresh_token)

    clock.advance(REFRESH_REUSE_GRACE_SECONDS - 1)
    before = _rows(repo)
    with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
        with pytest.raises(InvalidCredentialsError):
            await svc.refresh(refresh_token=first.refresh_token)

    assert not _replay_warnings(caplog)
    assert _rows(repo) == before, "a within-grace replay wrote to the store"
    assert (await svc.refresh(refresh_token=rotated.refresh_token)).access_token
    assert (await svc.refresh(refresh_token=sibling.refresh_token)).access_token


async def test_a_replay_after_grace_logs_one_warning_and_revokes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    svc, repo = _service(clock)
    first = await _logged_in(svc)
    sibling = await svc.login(email="a@example.com", password="hunter2pw")
    rotated = await svc.refresh(refresh_token=first.refresh_token)
    claims = decode_token(first.refresh_token, SECRET)

    clock.advance(REFRESH_REUSE_GRACE_SECONDS + 1)
    before = _rows(repo)
    with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
        with pytest.raises(InvalidCredentialsError):
            await svc.refresh(refresh_token=first.refresh_token)

    records = _replay_warnings(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert f"user_id={claims['sub']}" in message
    assert f"token_id={claims['jti']}" in message
    assert f"grace_seconds={REFRESH_REUSE_GRACE_SECONDS}" in message
    assert "consumed_age_seconds=" in message
    assert message.endswith("action=none")
    # Ids only: never a token or its signature.
    for token in (first.refresh_token, rotated.refresh_token, sibling.refresh_token):
        assert token not in message
        assert token.split(".")[-1] not in message

    # Detection only: the replay wrote nothing, and every other live token of
    # the user still refreshes.
    assert _rows(repo) == before, "an after-grace replay wrote to the store"
    assert (await svc.refresh(refresh_token=rotated.refresh_token)).access_token
    assert (await svc.refresh(refresh_token=sibling.refresh_token)).access_token


async def test_a_consumed_and_expired_token_is_refused_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The consumed-AND-expired early return (#47 never exercised it). The
    service clock is moved past the row's own expiry; the JWT is still within
    its real-time `exp`, so the refusal reaches the classifier."""
    clock = _Clock()
    svc, repo = _service(clock)
    first = await _logged_in(svc)
    await svc.refresh(refresh_token=first.refresh_token)

    ttl_days = Settings(jwt_secret=SECRET).jwt_refresh_token_ttl_days
    clock.advance(timedelta(days=ttl_days + 1).total_seconds())
    before = _rows(repo)
    with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
        with pytest.raises(InvalidCredentialsError):
            await svc.refresh(refresh_token=first.refresh_token)

    assert not _replay_warnings(caplog)
    assert _rows(repo) == before


async def test_a_logged_out_token_replayed_after_grace_is_logged_but_revokes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PINS A KNOWN LIMIT (design 9.0.4): `revoked_at` is set by logout and by
    rotation alike, so a logged-out token replayed past the grace window is
    reported as a replay too. Detection only, so the user's other session is
    untouched."""
    clock = _Clock()
    svc, _ = _service(clock)
    first = await _logged_in(svc)
    sibling = await svc.login(email="a@example.com", password="hunter2pw")
    assert await svc.logout(refresh_token=first.refresh_token) is True

    clock.advance(REFRESH_REUSE_GRACE_SECONDS + 1)
    with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
        with pytest.raises(InvalidCredentialsError):
            await svc.refresh(refresh_token=first.refresh_token)

    assert len(_replay_warnings(caplog)) == 1
    assert (await svc.refresh(refresh_token=sibling.refresh_token)).access_token


# ── G1.6: two concurrent refreshes of ONE token, backend only (no BFF lock) ──

def _race_rotations(monkeypatch: pytest.MonkeyPatch, repo: Any) -> list[bool]:
    """Hold each `rotate_refresh_token` call until TWO are in flight, then run
    both: the two refreshes are past the user read and inside the rotation at
    the same time, as two requests on two BFF instances would be. Returns the
    real rotation outcomes, in completion order."""
    barrier = asyncio.Barrier(2)
    real = repo.rotate_refresh_token
    outcomes: list[bool] = []

    async def racing(**kwargs: Any) -> bool:
        await asyncio.wait_for(barrier.wait(), timeout=5)
        won: bool = await real(**kwargs)
        outcomes.append(won)
        return won

    monkeypatch.setattr(repo, "rotate_refresh_token", racing)
    return outcomes


def _record_verdicts(monkeypatch: pytest.MonkeyPatch) -> list[RefreshReplay | None]:
    """Record every classification the service makes of a refused refresh."""
    real = user_service.classify_refused_refresh
    seen: list[RefreshReplay | None] = []

    def spy(*args: Any, **kwargs: Any) -> RefreshReplay | None:
        verdict = real(*args, **kwargs)
        seen.append(verdict)
        return verdict

    monkeypatch.setattr(user_service, "classify_refused_refresh", spy)
    return seen


async def test_two_concurrent_refreshes_of_one_token_never_revoke_anything(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Gate G1.6, memory store. Correctness comes from the BACKEND (single-winner
    atomic rotation + the grace window), not from any BFF coalescing: there is
    no BFF here. The real-Postgres twin is
    `test_pg_two_concurrent_refreshes_of_one_token_never_revoke_anything`."""
    svc, repo = _service()
    first = await _logged_in(svc)
    sibling = await svc.login(email="a@example.com", password="hunter2pw")
    jti = UUID(decode_token(first.refresh_token, SECRET)["jti"])
    rotations = _race_rotations(monkeypatch, repo)
    verdicts = _record_verdicts(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
        results = await asyncio.gather(
            svc.refresh(refresh_token=first.refresh_token),
            svc.refresh(refresh_token=first.refresh_token),
            return_exceptions=True,
        )
    monkeypatch.undo()

    # Exactly one valid pair; the other call is the deterministic refusal, and
    # nothing else (no other exception type).
    winners = [r for r in results if isinstance(r, LoginResult)]
    losers = [r for r in results if isinstance(r, InvalidCredentialsError)]
    assert len(winners) == 1 and len(losers) == 1, results
    assert str(losers[0]) == "refresh token revoked, expired, or not found", losers
    # Both really reached the rotation; the store picked one.
    assert sorted(rotations) == [False, True], rotations
    # The loser took the WITHIN-grace branch: classified once, inside the window,
    # and no after-grace WARNING.
    assert len(verdicts) == 1, verdicts
    assert verdicts[0] is not None and verdicts[0].within_grace, verdicts
    assert not _replay_warnings(caplog)
    # Nothing revoked: the only consumed row is the presented token's; the
    # sibling session and the winner's new token are the two live rows.
    assert _consumed_ids(repo) == {jti}
    assert len([rt for rt in repo._refresh.values() if rt.revoked_at is None]) == 2
    # The winner's new refresh token still refreshes, and so does the sibling.
    assert (await svc.refresh(refresh_token=winners[0].refresh_token)).access_token
    assert (await svc.refresh(refresh_token=sibling.refresh_token)).access_token


async def _mismatched_token(svc: UserAuthService, repo: InMemoryUserRepository):
    """User A's `sub` bound to user B's live `jti`, signed with the REAL key.

    A refresh token is a bearer credential: B's genuine token presented by
    anyone simply refreshes as B, and no check can tell the holder apart. What
    the server CAN enforce is that a token's `sub` owns the row its `jti`
    names. This forges exactly that mismatch (it needs the server secret, which
    only a test or a key compromise has) and returns (token, b_refresh, b_jti).
    """
    from skylize.app.auth.tokens import decode_token

    await _logged_in(svc, org="org_a", email="a@example.com")
    b = await _logged_in(svc, org="org_b", email="b@example.com")
    user_a = await repo.get_by_email("a@example.com")
    assert user_a is not None
    b_jti = decode_token(b.refresh_token, SECRET)["jti"]
    forged = create_refresh_token(
        user_id=str(user_a.user_id), token_id=UUID(b_jti), secret=SECRET, ttl_days=1
    )
    return forged, b.refresh_token, b_jti


async def test_refresh_refuses_a_sub_that_does_not_own_the_jti_without_consuming_it() -> None:
    svc, repo = _service()
    forged, b_refresh, b_jti = await _mismatched_token(svc, repo)

    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=forged)

    # B's row was NOT consumed: still live, and B can still refresh with it.
    row = await repo.get_refresh_token(UUID(b_jti))
    assert row is not None and row.revoked_at is None
    assert (await svc.refresh(refresh_token=b_refresh)).access_token


async def test_logout_with_a_mismatched_sub_does_not_revoke_the_other_users_token() -> None:
    svc, repo = _service()
    forged, b_refresh, b_jti = await _mismatched_token(svc, repo)

    assert await svc.logout(refresh_token=forged) is False
    row = await repo.get_refresh_token(UUID(b_jti))
    assert row is not None and row.revoked_at is None
    assert (await svc.refresh(refresh_token=b_refresh)).access_token


async def test_refresh_refuses_an_access_token() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.access_token)


# ── logout ───────────────────────────────────────────────────────────────────

async def test_logout_revokes_so_a_later_refresh_is_refused() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)

    assert await svc.logout(refresh_token=first.refresh_token) is True
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.refresh_token)


async def test_logout_is_idempotent() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    assert await svc.logout(refresh_token=first.refresh_token) is True
    assert await svc.logout(refresh_token=first.refresh_token) is False


@pytest.mark.parametrize(
    "bad",
    ["", "garbage", "not.a.jwt", "eyJhbGciOiJIUzI1NiJ9.e30.xxxx"],
)
async def test_logout_never_raises_for_garbage(bad: str) -> None:
    svc, _ = _service()
    assert await svc.logout(refresh_token=bad) is False


async def test_logout_refuses_tokens_this_server_did_not_sign() -> None:
    svc, repo = _service()
    first = await _logged_in(svc)
    user = await repo.get_by_email("a@example.com")
    assert user is not None
    # A refresh token for a REAL user and a REAL stored jti would still be
    # useless if signed with any key but ours.
    forged = create_refresh_token(
        user_id=str(user.user_id), token_id=uuid4(), secret="not-the-server-key", ttl_days=1
    )
    assert await svc.logout(refresh_token=forged) is False
    # And the user's real session is untouched.
    assert (await svc.refresh(refresh_token=first.refresh_token)).access_token


async def test_logout_with_an_access_token_revokes_nothing() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    assert await svc.logout(refresh_token=first.access_token) is False
    assert (await svc.refresh(refresh_token=first.refresh_token)).access_token


async def test_logout_only_revokes_the_presented_token() -> None:
    """User A logging out must not touch user B's session, in another org."""
    svc, _ = _service()
    a = await _logged_in(svc, org="org_a", email="a@example.com")
    b = await _logged_in(svc, org="org_b", email="b@example.com")

    assert await svc.logout(refresh_token=a.refresh_token) is True
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=a.refresh_token)
    assert (await svc.refresh(refresh_token=b.refresh_token)).access_token


async def test_logout_of_one_session_leaves_the_users_other_session() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    second = await svc.login(email="a@example.com", password="hunter2pw")

    assert await svc.logout(refresh_token=first.refresh_token) is True
    assert (await svc.refresh(refresh_token=second.refresh_token)).access_token


# ── HTTP route ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", SECRET)
    config_module._settings = None
    with TestClient(create_app()) as c:
        yield c
    config_module._settings = None


def _login(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_r", "email": "r@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "r@example.com", "password": "hunter2pw"}
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, str] = resp.json()
    return body


def test_route_logout_then_refresh_is_401(client: TestClient) -> None:
    pair = _login(client)

    out = client.post("/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert out.status_code == 204
    assert out.content == b""

    again = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert again.status_code == 401


@pytest.mark.parametrize("token", ["garbage", "not.a.jwt"])
def test_route_logout_is_204_for_garbage(client: TestClient, token: str) -> None:
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": token})
    assert resp.status_code == 204


def test_route_logout_is_204_when_already_revoked(client: TestClient) -> None:
    pair = _login(client)
    body = {"refresh_token": pair["refresh_token"]}
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204


def test_route_logout_rejects_a_malformed_body(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout", json={}).status_code == 422
    assert (
        client.post(
            "/api/v1/auth/logout", json={"refresh_token": "x", "user_id": "someone"}
        ).status_code
        == 422
    )


def test_route_logout_needs_no_access_token(client: TestClient) -> None:
    """No Authorization header at all: possession of the refresh token is the
    whole authorization, because the access token is often expired by logout."""
    pair = _login(client)
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert resp.status_code == 204


def test_route_refresh_401_detail_is_identical_for_every_refusal(client: TestClient) -> None:
    """POST /refresh is not an oracle: whatever the reason, the 401 body is the
    same fixed detail, and it never echoes the presented token."""
    pair = _login(client)
    sub = decode_token(pair["access_token"], SECRET)["sub"]

    bad_signature = create_refresh_token(
        user_id=sub, token_id=uuid4(), secret="not-the-server-key", ttl_days=1
    )
    unknown_jti = create_refresh_token(
        user_id=sub, token_id=uuid4(), secret=SECRET, ttl_days=1
    )
    revoked = pair["refresh_token"]
    assert client.post("/api/v1/auth/logout", json={"refresh_token": revoked}).status_code == 204

    cases = {
        "bad_signature": bad_signature,
        "access_token_as_refresh": pair["access_token"],
        "revoked": revoked,
        "unknown_jti": unknown_jti,
        "garbage": "garbage",
    }
    details: dict[str, object] = {}
    for name, token in cases.items():
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert resp.status_code == 401, (name, resp.text)
        body = resp.json()
        assert set(body) == {"detail"}, (name, body)
        assert token not in resp.text, name
        details[name] = body["detail"]

    assert set(details.values()) == {REFRESH_REFUSED_DETAIL}, details


@pytest.fixture()
def client_no_raise(monkeypatch: pytest.MonkeyPatch):
    """Like `client`, but a server error comes back as the HTTP response the
    caller would see instead of being re-raised into the test."""
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", SECRET)
    config_module._settings = None
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    config_module._settings = None


def test_route_logout_is_non_2xx_when_the_revoke_write_fails(
    client_no_raise: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BFF fails closed on a non-2xx logout. A failed DB write must surface
    as one, never be swallowed into the "nothing to revoke" 204."""
    pair = _login(client_no_raise)
    repo = client_no_raise.app.state.container.user_auth._repo  # type: ignore[attr-defined]

    async def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(repo, "consume_refresh_token", _boom)

    resp = client_no_raise.post(
        "/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]}
    )
    assert not 200 <= resp.status_code < 300, resp.status_code
    assert resp.status_code >= 500


# ── access-token TTL cap (owner ruling OD3-AMEND) ────────────────────────────

@pytest.fixture()
def no_ttl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKYLIZE_JWT_ACCESS_TOKEN_TTL_MINUTES", raising=False)


@pytest.mark.parametrize("ttl", [16, 30])
def test_settings_refuses_an_access_token_ttl_above_15(no_ttl_env: None, ttl: int) -> None:
    with pytest.raises(ValidationError, match="JWT_ACCESS_TOKEN_TTL_MINUTES"):
        Settings(jwt_secret=SECRET, jwt_access_token_ttl_minutes=ttl)


def test_settings_refuses_an_access_token_ttl_above_15_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKYLIZE_JWT_ACCESS_TOKEN_TTL_MINUTES", "30")
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SECRET)


def test_settings_accepts_15_and_the_default_is_within_the_cap(no_ttl_env: None) -> None:
    assert Settings(jwt_secret=SECRET, jwt_access_token_ttl_minutes=15).jwt_access_token_ttl_minutes == 15
    default = Settings(jwt_secret=SECRET).jwt_access_token_ttl_minutes
    assert default <= 15
    assert Settings.model_fields["jwt_access_token_ttl_minutes"].default <= 15
