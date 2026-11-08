# Skylize - My Day Server-Side Session Revocation (Design)

> **Status: APPROVED by the owner 2026-10-14, with the two decisions recorded in
> 2.0.3 and 7.0.** Build scope: sections 2.0.1-2.0.3 only, no migration.
> **Date compiled:** 2026-10-14, against `origin/main` @ `7604fa8`.
> **Resolves:** finding N2 (BLOCKING for My Day Stage 2) of the PR #27 security
> review: logout revokes nothing server-side, and refresh rotation has a race.
> **Companion:** login CSRF (finding N1) is fixed separately in PR #31; that guard
> also protects the logout `DELETE` described here.
> **[Corrected 2026-11-08, see section 8.0:** the access-token TTL, refresh-on-401
> and logout semantics have changed since approval. Stale passages below keep
> their original wording and carry an inline bracketed correction.**]**
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
  [Corrected 2026-11-08, see section 8.0: true only at `7604fa8`. The BFF logout now
  revokes server-side first, with one retry, and reports full vs partial logout.]
- `[CODE-VERIFIED]` The backend has no logout or revoke route. `auth.py` defines only
  `register` (`:88`), `login` (`:145`), `refresh` (`:165`) and `me` (`:183`).
- `[CODE-VERIFIED]` The only caller of `revoke_refresh_token` anywhere in `src/` is
  refresh rotation itself (`src/skylize/app/auth/user_service.py:206`).

Consequence: after logout, the refresh token that was inside the cookie stays valid
server-side until its natural expiry, 14 days by default
(`src/skylize/config.py:111`) [Corrected 2026-11-08, see section 8.0: still 14
days, now at `src/skylize/config.py:119`]. Anyone who captured the cookie before logout (and
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
   AND user_id    = $2
   AND revoked_at IS NULL
   AND expires_at  > now()
RETURNING token_id
```

- Exactly one concurrent caller gets a row back; every other caller gets none and
  is refused. Postgres row locking under `UPDATE` makes this hold at READ COMMITTED,
  the default; no explicit transaction or `SELECT ... FOR UPDATE` is needed.
- New port method on `UserRepository` (`src/skylize/dal/ports.py`):
  `consume_refresh_token(token_id: UUID, user_id: UUID) -> bool`, True only for the
  single winner. It is implemented in both `dal/users.py` (Postgres) and
  `dal/memory.py` (in-memory backend and tests). The in-memory version needs no
  lock: it has no `await` between check and write, so nothing can interleave on its
  single event loop.
- `refresh()` keeps its signature decode and `type == "refresh"` check, then calls
  `consume_refresh_token(jti, sub)` instead of `get_refresh_token` + `revoke_refresh_token`.
- **Ownership is inside the same condition (`user_id = $2`).** A token whose `sub`
  does not own the row its `jti` names is refused and revokes nothing. An earlier
  draft checked ownership AFTER consuming, which would have revoked the other user's
  row as a side effect of the refusal; the owner's review asked for that to be pinned
  by a test, and it is (`test_refresh_refuses_a_sub_that_does_not_own_the_jti_without_consuming_it`,
  `test_mismatched_sub_is_refused_and_does_not_consume`). Forging such a token needs
  the server key, so this is defence in depth, not a reachable attack without it.
- The existing `revoke_refresh_token` stays in the port for now (one caller fewer);
  removing it is a cleanup, not part of this fix.

### 2.0.2 New endpoint: `POST /api/v1/auth/logout` `[PROPOSED]`

| Property | Design |
|---|---|
| Path, method | `POST /api/v1/auth/logout` in `src/skylize/edge/routes/auth.py` |
| Body | `{"refresh_token": "<jwt>"}`, `extra="forbid"`, same shape as `RefreshRequest` (`auth.py:66-68`) |
| Identity | **Proof of possession of the refresh token itself.** No access token required: at logout time the 30-minute access token (`config.py:110`) is often already expired, and requiring it would make logout fail exactly when it matters. [Corrected 2026-11-08, see section 8.0: the access token is now 15 minutes, capped and refused above that at boot (`config.py:17,118,373-387`).] |
| What it revokes | Only the `jti` inside a refresh token whose HS256 signature verifies under the server secret and whose `type == "refresh"`. No `user_id` or `token_id` is accepted from the body. |
| Mechanism | The same atomic `UPDATE ... WHERE revoked_at IS NULL` as 2.0.1 (the result is ignored). |
| Response | **Always `204`** for a schema-valid body: valid token revoked, already revoked, expired, unknown `jti`, or bad signature. It never reveals whether a token existed or was live (no token-validity oracle). `422` only for a malformed body. [Corrected 2026-11-08, see section 8.0: a failure of the revoke WRITE itself is not caught and surfaces as a 5xx (`auth.py:224-229`), so the BFF can tell "revoked" from "not recorded".] |
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
   [Corrected 2026-11-08, see section 8.0: the cookie is still ALWAYS cleared (OD20),
   but the status is no longer always `204`. Step 2 is retried once; if it still
   fails, or a config error prevents step 1, the answer is `200` with a "partial
   logout" body. `204` now means the server-side session was ended, or there was
   nothing to revoke.]
4. The CSRF guard from PR #31 already refuses a cross-site `DELETE` before any of
   this runs.

`[OWNER-DECIDED 2026-10-14]` **If the backend call fails** (network error, timeout,
5xx, or any non-204 answer): the BFF still clears the cookie and returns `204`, and it
**logs the failure** at error level (never silently swallowed). The log line names the
failure class and status, never the token.
[Corrected 2026-11-08, see section 8.0: superseded in two steps. The interim T2
change (`2b6bfbe`) answered `502` and KEPT the cookie; OD20 reverses that and
restores "always clear", but the BFF now retries the backend call once and, if it
still fails, answers `200` with a partial-logout body instead of `204`. The failure
is logged as `myday_logout_partial` with attempt count and status only.]

**The trade-off, stated plainly.** When the backend revocation fails, the refresh
token stays valid server-side until it expires (up to 14 days, `config.py:111`
[Corrected 2026-11-08, see section 8.0: now `config.py:119`]). The
risk is bounded by the cookie having been deleted from this browser: it only matters
to someone who already captured the cookie, or the cookie key, before logout. The
user is never shown a logout that "failed", and the error log is the operator's
signal that a token outlived its session. The rejected alternative was returning
`502` with the cookie cleared, which would surface an error for an outcome the user
cannot act on.
[Corrected 2026-11-08, see section 8.0: under OD20 the user IS told, without an
error status, when the server-side session could not be ended: `200` plus
`{"logout":"partial","serverSessionEnded":false,...}`. The local logout still
always succeeds.]

If the cookie does not decrypt (absent, tampered, or wrong key), there is no refresh
token to send. The BFF clears the cookie and returns `204` without calling the
backend; that is the normal path, not a failure.
[Corrected 2026-11-08, see section 8.0: this holds only under a VALID My Day config.
If a cookie is present but the config is broken, decryption cannot be attempted and
a live token may exist server-side. That is a PARTIAL logout (`200`, body, cookie
cleared), logged as `myday_config_error` (OD21), never a `204`.]

### 2.0.4 Absolute session lifetime in the BFF `[PROPOSED - NOT IN THIS BUILD]`

Not in the approved build list (logout endpoint, BFF logout, race fix, tests).
Kept here as a proposal for a separate small change.

`[CODE-VERIFIED]` The cookie payload carries `iat`, but nothing checks it
(`myday-session.ts:201`). Add a check in `readMyDaySession`
(`myday-handler.ts`): reject a session older than the cookie `maxAge` (14 days,
`myday-session.ts:26`). BFF-only, no migration. It caps how long a replayed cookie
is useful even once refresh-on-401 is wired.
[Corrected 2026-11-08, see section 8.0: refresh-on-401 IS now wired in the BFF.
The `iat` lifetime check itself is still not built.]

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
  [Corrected 2026-11-08, see section 8.0: at most 15 minutes. The TTL is capped
  at 15 and a higher value is refused at boot (`config.py:17,118,373-387`), which
  bounds this post-logout window.]
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
  [Corrected 2026-11-08, see section 8.0: the tests also prove the single retry,
  the `200` partial body on a failure that persists, and the config-error case.]
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
  [Corrected 2026-11-08, see section 8.0: 2.0.3 is now governed by OD20. The cookie
  is still always cleared, but a persistent failure answers `200` with a partial body
  after one retry. It is not a `204`.]
- **Build scope:** section 6.0 steps 1-3 plus tests, no migration, Console untouched.

---

## 8.0 - Correction 2026-11-08 (OD3-AMEND, OD20-OD22)

Appended, not rewritten (OD22): the earlier sections keep their 2026-10-14 wording,
and each stale passage carries an inline `[Corrected 2026-11-08 ...]` note pointing
here. Line numbers are those of the `feat/myday-revocation` branch as of this
correction.

### 8.0.1 Access-token TTL (OD3-AMEND) `[CODE-VERIFIED]`

- The access-token TTL defaults to **15 minutes** (`src/skylize/config.py:118`) and is
  **capped at 15** (`_MAX_ACCESS_TOKEN_TTL_MINUTES`, `config.py:17`). A higher value is
  **refused at boot** by the `_cap_access_token_ttl` validator (`config.py:373-387`).
  Every "30 minutes" above is superseded. The refresh TTL is still 14 days
  (`config.py:119`).
- This bounds the residual risk in 4.0: an access token already issued outlives
  logout by at most 15 minutes.

### 8.0.2 `/refresh` refusals are indistinguishable `[CODE-VERIFIED]`

- Every refusal from `POST /api/v1/auth/refresh` answers `401` with one fixed detail,
  `REFRESH_REFUSED_DETAIL = "invalid refresh token"`
  (`src/skylize/edge/routes/auth.py:40`, raised at `:192`). The specific reason is
  logged server-side only. The BFF branches on the status, never on the detail.

### 8.0.3 BFF refresh-on-401 (OD3-AMEND) `[CODE-VERIFIED]`

All of it lives in one place, `createAccessTokenRunner`
(`website/src/lib/skylize/myday-handler.ts:239`). Handlers reach it through
`withAccessToken`, for example `website/src/app/api/my-day/me/route.ts:25`.
- When an authenticated backend call returns `401`, the BFF calls `mydayRefresh`
  **exactly once** per request (`myday-handler.ts:264`). It is a POST and is never
  auto-retried (`myday-client.ts:104`, retries are GET-only).
- If the refresh succeeds, the cookie is re-sealed with the NEW pair and set on
  whatever response goes out (`myday-handler.ts:310`). The original call is retried
  **once** (`:287`). A second `401` gives `401` plus a cleared cookie, with no second
  refresh.
- A refresh that gets `401` gives `401` plus a cleared cookie. Any other refresh
  failure gives `502` plus a cleared cookie (`:267`).
- Known two-tab race: the loser's refresh `401` clears the cookie the winner just set,
  forcing a re-login. This fails closed and is documented at `myday-handler.ts:201`.

### 8.0.4 BFF logout semantics (OD20) `[CODE-VERIFIED]`

`DELETE /api/my-day/session` (`website/src/app/api/my-day/session/route.ts:179`;
rationale comment at `:162`). The **cookie is ALWAYS cleared**, which restores the
2026-10-14 "always close" rule with explicit reporting:
- **Single retry:** `revokeWithOneRetry` makes at most 2 attempts (`route.ts:118,127`).
- **FULL logout:** `204` with an empty body and a cleared cookie (`route.ts:147`).
  This happens when revocation succeeded, or when there was nothing to revoke: no
  cookie, or a cookie that does not decrypt under a VALID config.
- **PARTIAL logout:** `200` with a cleared cookie and
  `{"logout":"partial","serverSessionEnded":false,"message":"Signed out on this device; we could not end the session on the server."}`
  (`route.ts:154`). The body and its type are exported from
  `website/src/lib/skylize/myday-logout.ts` (`MYDAY_PARTIAL_LOGOUT_BODY`,
  `MyDayPartialLogoutBody`). A partial logout happens when:
  - revocation fails after the retry. It is logged at error level under
    `myday_logout_partial` with attempt count and status only, never the token
    (`route.ts:198`);
  - a cookie is present but `getMyDayConfig()` throws, so revocation cannot even be
    attempted. It is logged under `myday_config_error` (`route.ts:102,186`);
  - a cookie is present but reading it throws for any other reason. It is logged
    under `myday_logout_partial` with `reason: "cookie-read-error"` and the error
    name only (`route.ts:102,190`).
- **Superseded interim:** the T2 change `2b6bfbe` ("logout fails closed": `502` and
  KEEP the cookie on failure) did exist on this branch. OD20 supersedes it.

### 8.0.5 Config errors are logged and distinguishable (OD21) `[CODE-VERIFIED]`

- Every `getMyDayConfig` failure is a `MyDayConfigError`
  (`website/src/lib/skylize/myday-config.ts:36`). Its messages name variables, never
  values. The one message that used to echo the backend URL no longer does
  (`myday-config.ts:110`).
- `readMyDaySession` no longer collapses a config error into a silent `401`
  (`myday-handler.ts:135`). `mydayRoute` handles it in one place
  (`myday-handler.ts:343`): the error is logged under `myday_config_error` with the
  message only (`:110`), the answer is an opaque `500`, and the user's cookie is
  **not** cleared (`:118`). The login path, which fails inside `mydayLogin`, goes
  through the same branch and the same tag.
- An absent, tampered or expired cookie under a valid config is still `401` plus a
  cleared cookie, and is not logged with the config tag.
- Boot-time validation of the My Day config is out of scope here (tracked as T5).
