# Skylize - My Day Server-Side Session Revocation (Design)

> **Status: APPROVED by the owner 2026-10-14, with the two decisions recorded in
> 2.0.3 and 7.0.** Build scope: sections 2.0.1-2.0.3 only, no migration.
> **Date compiled:** 2026-10-14, against `origin/main` @ `7604fa8`.
> **Resolves:** finding N2 (BLOCKING for My Day Stage 2) of the PR #27 security
> review: logout revokes nothing server-side, and refresh rotation has a race.
> **Companion:** login CSRF (finding N1) is fixed separately in PR #31; that guard
> also protects the logout `DELETE` described here.
>
> **How to read this file.** Every claim is marked:
> - `[CODE-VERIFIED]` - read from `origin/main` @ `7604fa8`, cites `file:line`.
> - `[PROPOSED]` - the design choice this document asks the owner to approve.
> - `[OWNER-DECISION-REQUIRED]` - a choice with a real trade-off, left open.

---

## 1.0 - The gap, precisely

### 1.0.1 Logout revokes nothing

- `[CODE-VERIFIED]` The BFF logout only clears the cookie. `DELETE /api/my-day/session`
  builds a 204 and sets `clearedMydaySessionCookie()`, with no backend call
  (`website/src/app/api/my-day/session/route.ts:70-77`).
- `[CODE-VERIFIED]` The backend has no logout or revoke route. `auth.py` defines only
  `register` (`:88`), `login` (`:145`), `refresh` (`:165`) and `me` (`:183`).
- `[CODE-VERIFIED]` The only caller of `revoke_refresh_token` anywhere in `src/` is
  refresh rotation itself (`src/skylize/app/auth/user_service.py:206`).

Consequence: after logout, the refresh token that was inside the cookie stays valid
server-side until its natural expiry, 14 days by default
(`src/skylize/config.py:111`). Anyone who captured the cookie before logout (and
holds the cookie key, or replays the cookie itself) can keep minting fresh access
tokens for two weeks.

### 1.0.2 The rotation race

`[CODE-VERIFIED]` `UserAuthService.refresh` (`user_service.py:189-212`) is
check-then-act across two separate statements:

1. `get_refresh_token(jti)` reads the row (`:199`, `dal/users.py:109-121`);
2. it rejects if `revoked_at` is set or the row is expired (`:200-203`);
3. `revoke_refresh_token(jti)` runs an UNCONDITIONAL
   `UPDATE user_refresh_tokens SET revoked_at=now() WHERE token_id=$1`
   (`:206`, `dal/users.py:123-128`);
4. it mints a new pair (`:212`).

**Concrete scenario.** Two requests carry the same refresh token R (a browser
double-submit, two tabs refreshing at once, or an attacker replaying a stolen R at
the same moment as the victim):

| t | Request A | Request B |
|---|---|---|
| 1 | reads R: `revoked_at IS NULL` | |
| 2 | | reads R: `revoked_at IS NULL` |
| 3 | `UPDATE ... SET revoked_at=now()` | |
| 4 | | `UPDATE ... SET revoked_at=now()` (still succeeds; no condition) |
| 5 | mints pair P1 | mints pair P2 |

Both succeed. One refresh token has become **two live token families**. If B was
an attacker, revoking the victim's family later does not touch the attacker's.

---

## 2.0 - The fix

### 2.0.1 Atomic single-use consume `[PROPOSED]`

Replace the read-then-unconditional-update with one conditional statement whose
row count decides the winner:

```sql
UPDATE user_refresh_tokens
   SET revoked_at = now()
 WHERE token_id   = $1
   AND revoked_at IS NULL
   AND expires_at  > now()
RETURNING user_id
```

- Exactly one concurrent caller gets a row back; every other caller gets none and
  is refused. Postgres row locking under `UPDATE` makes this hold at READ COMMITTED,
  the default; no explicit transaction or `SELECT ... FOR UPDATE` is needed.
- New port method on `UserRepository` (`src/skylize/dal/ports.py:277` area):
  `consume_refresh_token(token_id: UUID) -> UUID | None`, returning the owning
  `user_id` on success and `None` otherwise. It must be implemented in both
  `dal/users.py` (Postgres) and `dal/memory.py:392` (in-memory backend and tests).
  The in-memory version needs an `asyncio.Lock` to give the same single-winner
  guarantee.
- `refresh()` keeps its signature decode and `type == "refresh"` check, then calls
  `consume_refresh_token(jti)` instead of `get_refresh_token` + `revoke_refresh_token`.
  It also checks that the returned `user_id` equals the token's `sub` claim;
  a mismatch is refused (defence in depth: a forged `jti` pointing at another user's
  row cannot rotate it, because the signature already binds `sub` and `jti`).
- The existing `revoke_refresh_token` stays in the port for now (one caller fewer);
  removing it is a cleanup, not part of this fix.

### 2.0.2 New endpoint: `POST /api/v1/auth/logout` `[PROPOSED]`

| Property | Design |
|---|---|
| Path, method | `POST /api/v1/auth/logout` in `src/skylize/edge/routes/auth.py` |
| Body | `{"refresh_token": "<jwt>"}`, `extra="forbid"`, same shape as `RefreshRequest` (`auth.py:66-68`) |
| Identity | **Proof of possession of the refresh token itself.** No access token required: at logout time the 30-minute access token (`config.py:110`) is often already expired, and requiring it would make logout fail exactly when it matters. |
| What it revokes | Only the `jti` inside a refresh token whose HS256 signature verifies under the server secret and whose `type == "refresh"`. No `user_id` or `token_id` is accepted from the body. |
| Mechanism | The same atomic `UPDATE ... WHERE revoked_at IS NULL` as 2.0.1 (the result is ignored). |
| Response | **Always `204`** for a schema-valid body: valid token revoked, already revoked, expired, unknown `jti`, or bad signature. It never reveals whether a token existed or was live (no token-validity oracle). `422` only for a malformed body. |
| Idempotent | Yes. A second logout with the same token is a no-op `204`. |
| Rate limit | `enforce_anonymous_rate_limit`, like `login`/`refresh` (`auth.py:146-147,166-167`). |
| Logging | Log `user_id` and `jti` on a successful revoke; never log the token. |

### 2.0.3 BFF logout `[PROPOSED]`

`DELETE /api/my-day/session` stays `requireAuth: false` (it must work with a dead
session) and becomes:

1. Read the `skylize_myday` cookie and `decryptSession` it
   (`website/src/lib/skylize/myday-session.ts`). The refresh token is inside the
   sealed pair; it never leaves the server and is never shown to the browser.
2. If it decrypts, call a new `mydayLogout(refresh_token)` in
   `website/src/lib/skylize/myday-client.ts`, which POSTs `/api/v1/auth/logout`
   with no `Authorization` header and no service key (the fail-closed rule from the
   Stage 1 design, section 5.0, still holds).
3. **Always** clear the cookie and return `204`, whether or not step 1 or 2 worked.
   The user's browser is logged out locally in every case.
4. The CSRF guard from PR #31 already refuses a cross-site `DELETE` before any of
   this runs.

`[OWNER-DECIDED 2026-10-14]` **If the backend call fails** (network error, timeout,
5xx, or any non-204 answer): the BFF still clears the cookie and returns `204`, and it
**logs the failure** at error level (never silently swallowed). The log line names the
failure class and status, never the token.

**The trade-off, stated plainly.** When the backend revocation fails, the refresh
token stays valid server-side until it expires (up to 14 days, `config.py:111`). The
risk is bounded by the cookie having been deleted from this browser: it only matters
to someone who already captured the cookie, or the cookie key, before logout. The
user is never shown a logout that "failed", and the error log is the operator's
signal that a token outlived its session. The rejected alternative was returning
`502` with the cookie cleared, which would surface an error for an outcome the user
cannot act on.

If the cookie does not decrypt (absent, tampered, or wrong key), there is no refresh
token to send. The BFF clears the cookie and returns `204` without calling the
backend; that is the normal path, not a failure.

### 2.0.4 Absolute session lifetime in the BFF `[PROPOSED - NOT IN THIS BUILD]`

Not in the approved build list (logout endpoint, BFF logout, race fix, tests).
Kept here as a proposal for a separate small change.

`[CODE-VERIFIED]` The cookie payload carries `iat`, but nothing checks it
(`myday-session.ts:201`). Add a check in `readMyDaySession`
(`myday-handler.ts`): reject a session older than the cookie `maxAge` (14 days,
`myday-session.ts:26`). BFF-only, no migration. It caps how long a replayed cookie
is useful even once refresh-on-401 is wired.

---

## 3.0 - Schema: is `user_refresh_tokens` enough?

`[CODE-VERIFIED]` Columns: `token_id` (PK), `user_id` (FK, `ON DELETE CASCADE`),
`expires_at`, `revoked_at`, `created_at`; index on `user_id`
(`migrations/versions/0008_users.py:52-62`). The app role has `SELECT, INSERT, UPDATE`
(`:70`).

**Sections 2.0.1-2.0.4 need NO migration.** The atomic consume and logout only use
`token_id`, `expires_at` and `revoked_at`, and `UPDATE` is already granted.
This build stays migration-free.

Reuse detection would need a migration; per the owner's decision it is not part of
this build. See section 7.0.

"Log out everywhere" (`revoke all tokens for user_id`) is possible with the existing
`idx_refresh_tokens_user`, but needs an authenticated route; it is out of scope here.

---

## 4.0 - Security model

- **Who can revoke what.** Only the holder of a validly signed refresh token, and
  only that token's own `jti`. Neither the logout route nor the BFF accepts a user or
  token id from the caller, so no user can revoke another user's sessions, inside or
  across orgs.
- **What logout does not do.** It does not invalidate the access token already
  issued: access tokens are stateless HS256 JWTs (`app/auth/tokens.py:16-33`) and
  stay valid until they expire, at most 30 minutes (`config.py:110`). An access-token
  denylist is out of scope; record this as an accepted residual risk.
- **Rate limiting.** Logout uses the anonymous peer-address limiter. All My Day
  traffic reaches the backend from the BFF's address, so it shares one bucket with
  My Day logins (review finding N5); this design does not make that worse.
- **No new trust in headers.** The BFF still builds its outbound headers from scratch
  (`myday-client.ts`), so nothing from the browser is forwarded.

---

## 5.0 - Console is unaffected

- Console authenticates to the backend with the shared service key
  (`website/src/lib/skylize/client.ts:142-151`) and never calls `/api/v1/auth/*`.
- Console's own session is the interim HMAC-signed `skylize_console` cookie
  (`website/src/lib/skylize/session.ts`), which holds no refresh token.
- No Console file changes in this design. The only shared backend change is in
  `refresh()`, which Console does not call.

---

## 6.0 - Implementation plan (after approval)

| Step | Files | Migration |
|---|---|---|
| 1. Atomic `consume_refresh_token`; `refresh()` uses it | `dal/ports.py`, `dal/users.py`, `dal/memory.py`, `app/auth/user_service.py` | No |
| 2. `POST /api/v1/auth/logout` | `edge/routes/auth.py`, `app/auth/user_service.py` | No |
| 3. BFF `DELETE` calls logout | `myday-client.ts`, `app/api/my-day/session/route.ts` | No |

Not in this build: 2.0.4 (`iat` lifetime, separate small change) and reuse
detection (section 7.0, needs a migration).

**Tests the implementation must carry:**
- a real-Postgres integration test that fires two concurrent refreshes with the same
  token (for example `asyncio.gather`) and asserts exactly one succeeds;
- logout followed by refresh with the same token gets `401`;
- logout is `204` for unknown, expired, already-revoked and bad-signature tokens;
- a BFF test showing `DELETE` calls `mydayLogout` with the decrypted refresh token
  and clears the cookie even when the backend call fails;
- the existing My Day import-isolation test still passes (no service-key reach).

---

## 7.0 - Next phase: refresh-token reuse detection (NOT in this build)

`[OWNER-DECIDED 2026-10-14]` Out of scope for this build, which stays migration-free.
Recorded here so it is not lost.

**What it is worth.** The atomic consume (2.0.1) stops two concurrent refreshes from
both succeeding, but it cannot tell WHO won. If a thief replays a stolen refresh token
before the victim does, the thief's rotation wins and keeps a live token family; the
victim's next refresh is refused and they simply log in again, while the thief stays
in for up to 14 days. Reuse detection closes that: when an already-consumed token is
presented again, one of its two holders must be a thief, so the WHOLE family is
revoked and both are forced back to login.

**Why it needs a migration.** `user_refresh_tokens` has no lineage
(`0008_users.py:52-62`): nothing links a token to the one it replaced. It needs, for
example:
- `family_id UUID NOT NULL` (the first token's id, copied on every rotation), and
- an index on `family_id`.
A refused consume of an already-revoked token could then run
`UPDATE user_refresh_tokens SET revoked_at = now()
 WHERE family_id = $1 AND revoked_at IS NULL`.
Existing rows need a backfill (for example `family_id = token_id`), which is safe
because each existing row becomes its own family.

**Why it is a separate piece of work.**
- It changes a table on the login path, so it needs its own migration review,
  a downgrade path, and a backfill plan; none of that is needed for this build.
- It changes what a refused refresh does (from "refuse this token" to "revoke the
  family"), which can log out a legitimate user on a benign double-submit. That
  behaviour needs its own decision and tests.
- The race fix and logout are useful on their own and should not wait for it.

---

## Sign-off

- **Approved:** owner, 2026-10-14.
- **Decisions:** 2.0.3, backend logout failure means cookie cleared, `204`, failure
  logged at error level; 7.0, reuse detection deferred to a later, separate change.
- **Build scope:** section 6.0 steps 1-3 plus tests, no migration, Console untouched.
