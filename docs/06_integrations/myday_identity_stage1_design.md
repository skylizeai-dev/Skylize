# Skylize - My Day Identity Path Design (Stage 1)

> **Status: APPROVED - owner-decided.**
> **Date compiled:** 2026-09-27
> **Provenance note (read first).** The design in this document was approved by the
> repo owner in a chat session on 2026-09-24. That approval was never previously
> committed to the repository, and no prior Stage 0 design file for the My Day
> identity path exists in `docs/`, in git history around that date, or in the ADRs
> (the 2026-09-24-era commits were all Console screen-wiring). **This document is the
> first written record of that approval**, authored 2026-09-27 during Stage 1
> implementation, and re-confirmed by the owner in the same session before code was
> written. It exists to create an auditable design of record; it does not re-open the
> decision.
> **Scope of this document: Stage 1 (the identity path) ONLY.** Stage 2 (wiring the
> `my-day` frontend to a `useMyDayState` hook) is a separate, later task and is out of
> scope here. This document does not by itself unblock Stage 2.
>
> **How to read this file.** Every concrete claim is marked with one of:
> - `[CODE-VERIFIED]` - extracted from the current codebase at commit `4667fb6`
>   (origin/main, post-#25/#26). Ground truth. Cites `file:line`.
> - `[OWNER-DECIDED]` - a design choice the owner made and approved. Not re-litigated.
> - `[DERIVED]` - follows by construction from a `[CODE-VERIFIED]` fact plus an
>   `[OWNER-DECIDED]` choice; the derivation is shown.

---

## 1.0 - Problem and shape

### 1.0.1 What Stage 1 delivers

A browser-facing **identity path for the My Day surface** that is cryptographically and
architecturally isolated from the Console's session gate. Concretely:

- A new session cookie, `skylize_myday`, distinct in NAME and SECRET from Console's
  `skylize_console`.
- AES-256-GCM encryption of the cookie payload (the user's backend token pair), because
  that payload holds a real bearer credential and needs confidentiality, not just
  integrity.
- A new, fully isolated route namespace `/api/my-day/*`.
- A fail-closed JWT lifecycle: an expired or invalid token yields `401` and a cleared
  cookie, and the My Day code path can NEVER fall back to Console's shared service key.
- No new database table: the refresh-token state Stage 1 relies on already exists.

### 1.0.2 Why a parallel scheme rather than reusing Console's

`[CODE-VERIFIED]` Console's cookie is **signing-only**. `createSessionToken` /
`verifySessionToken` use HMAC-SHA256 over a payload that is `SIGNING_CONTEXT.<expiresAtMs>`
- an expiry, not a secret (`website/src/lib/skylize/session.ts:9-13,52-79`). The cookie's
job is to prove "this browser passed the shared password", and it carries no confidential
material, so integrity alone is sufficient there.

`[OWNER-DECIDED]` My Day's cookie carries a REAL bearer token (the backend access +
refresh pair). A signed-but-readable cookie would expose that bearer token to anyone who
can read the cookie value. Therefore My Day's cookie MUST be encrypted (confidential), not
merely signed. Reusing Console's signing-only scheme is rejected for this reason. This is
the single deciding constraint for the whole "new scheme" decision.

---

## 2.0 - The backend surface Stage 1 builds on

Everything in this section is `[CODE-VERIFIED]`. Stage 1 adds NO backend endpoints; it is a
browser-facing BFF (backend-for-frontend) layer over endpoints that already exist.

### 2.0.1 The human-user auth endpoints

`[CODE-VERIFIED]` `src/skylize/edge/routes/auth.py`:

| Endpoint | Line | Behaviour |
|---|---|---|
| `POST /api/v1/auth/register` | `auth.py:88-142` | Create a NEW org + its owner. |
| `POST /api/v1/auth/login` | `auth.py:145-162` | email + password -> `{access_token, refresh_token}`. |
| `POST /api/v1/auth/refresh` | `auth.py:165-180` | rotate refresh token -> new pair. |
| `GET  /api/v1/auth/me` | `auth.py:183-202` | current user (requires valid access token). |

### 2.0.2 How the backend authenticates a My Day user request

`[CODE-VERIFIED]` The backend's human-user path reads
`Authorization: Bearer <access_jwt>`, decodes it (HS256), rejects a missing / malformed /
non-`access` / invalid token with `401`, and projects `user_id`, `org_id`, `roles` into a
`RequestContext` (`src/skylize/edge/deps.py:50-78`, `get_current_user`).

`[DERIVED]` Therefore My Day's BFF authenticates to the backend by **forwarding the user's
own access JWT as `Authorization: Bearer <...>`**. It does NOT, and must not, send the
service API key. This single fact is the mechanical basis of the fail-closed guarantee in
section 5.0.

### 2.0.3 The token lifecycle and where refresh state lives

`[CODE-VERIFIED]` Tokens are our own HS256 pair
(`src/skylize/app/auth/tokens.py:16-51`): an `access` token (default TTL 30 min,
`src/skylize/config.py:110`) and a `refresh` token (default TTL 14 days,
`src/skylize/config.py:111`) carrying a `jti`.

`[CODE-VERIFIED]` Refresh is ROTATING and server-tracked. `UserAuthService.refresh` decodes
the refresh token, looks the `jti` up in the store, rejects a revoked / missing / expired
row, then revokes the consumed token before minting the next pair
(`src/skylize/app/auth/user_service.py:189-212,219-241`).

`[CODE-VERIFIED]` That server-side state is the **`user_refresh_tokens`** table, created by
migration `0008_users.py:52-62` (columns include `expires_at`, `revoked_at`; indexed on
`user_id`; `SELECT/INSERT/UPDATE` granted to the app role at
`migrations/versions/0008_users.py:70`).

`[DERIVED - satisfies the "no new DB table" requirement]` Stage 1 introduces NO migration.
All refresh-token persistence it needs is already provided by `user_refresh_tokens`; the
BFF simply calls `/api/v1/auth/refresh`, which drives that table.

---

## 3.0 - The cookie: `skylize_myday`

### 3.0.1 Name and secret separation `[OWNER-DECIDED]`

- Cookie name: `skylize_myday` (distinct from `skylize_console`,
  `website/src/lib/skylize/session.ts:9`).
- Cookie secret: a NEW env var (proposed `SKYLIZE_MYDAY_SESSION_SECRET`), distinct from
  `SKYLIZE_CONSOLE_SESSION_SECRET` (`website/src/lib/skylize/config.ts:92`). Reusing
  Console's secret is forbidden - a shared secret defeats the isolation this whole design
  exists to provide.

### 3.0.2 Payload and encryption `[OWNER-DECIDED]`

The cookie value is the AES-256-GCM ciphertext of the JSON token pair
`{ access_token, refresh_token }` (plus whatever minimal metadata the implementation needs,
e.g. an issued-at). AES-256-GCM is authenticated encryption: it provides BOTH
confidentiality (the bearer token is not readable from the cookie) AND integrity (a tampered
cookie fails decryption). The Web Crypto `crypto.subtle` AES-GCM primitive is available in
both the Next.js route-handler runtime and the proxy/edge runtime, matching Console's
"Web Crypto only" constraint (`website/src/lib/skylize/session.ts:6-7`).

`[DERIVED]` A random 96-bit IV per encryption is stored alongside the ciphertext in the
cookie value (standard AES-GCM construction); the 128-bit GCM auth tag is what makes tamper
detection - and therefore fail-closed verification - automatic.

### 3.0.3 Cookie attributes `[DERIVED from Console's precedent]`

`httpOnly: true`, `secure` everywhere except `next dev`, `sameSite: "lax"`, `path: "/"`,
with a `maxAge` bounded by the refresh-token TTL. This mirrors the Console cookie descriptor
(`website/src/lib/skylize/session.ts:105-132`), which is the right posture for a
browser session cookie; only the name, secret, and payload/crypto differ.

---

## 4.0 - The route namespace: `/api/my-day/*`

### 4.0.1 Isolation from Console `[OWNER-DECIDED]`

`/api/my-day/*` is a NEW route tree with its own handler helper (a My-Day analogue of
`consoleRoute`, `website/src/lib/skylize/handler.ts:102-159`) that verifies the
`skylize_myday` cookie, NOT the Console cookie. It does not import, extend, or route through
`consoleProxyGate` (`website/src/lib/skylize/proxy-gate.ts:27-55`).

### 4.0.2 The one Console-side change `[OWNER-DECIDED, bounded]`

`[CODE-VERIFIED]` Console's proxy matcher today is
`matcher: ["/console/:path*", "/api/console/:path*"]`
(`website/src/proxy.ts:19-21`).

The ONLY permitted change to any existing Console file is a **pure addition** of the
`/api/my-day/:path*` prefix to that matcher array, so the proxy runtime invokes the gate for
My Day requests. **Hard gate:** if wiring My Day into the proxy requires restructuring the
existing matcher or the `proxy()` body (e.g. branching `consoleProxyGate` vs a My Day gate),
that exceeds approved scope - STOP and report. The approved shape is one added array element
plus, at most, a dispatch that leaves the Console branch byte-for-byte unchanged.

### 4.0.3 Login / logout / refresh endpoints `[DERIVED]`

Minimal Stage 1 surface, each a `/api/my-day/*` route:

- `POST /api/my-day/session` - email + password -> call `POST /api/v1/auth/login` ->
  encrypt the returned pair into the `skylize_myday` cookie -> `204`.
- `DELETE /api/my-day/session` - clear the cookie -> `204`.
- At least one authenticated read (e.g. `GET /api/my-day/me` proxying `GET /api/v1/auth/me`)
  so the isolation test in section 6.0 has a real per-user, per-org read to assert against.

The public (pre-auth) exemption set for the My Day gate is `POST /api/my-day/session` only,
mirroring Console's `/api/console/session` exemption
(`website/src/lib/skylize/proxy-gate.ts:13`).

---

## 5.0 - The fail-closed guarantee (security-critical)

This is the property the whole design exists to protect: **the My Day code path can never
authenticate to the backend with Console's shared service key.** It is enforced BY
CONSTRUCTION, not by convention.

### 5.0.1 Where the service key lives `[CODE-VERIFIED]`

- `getBackendConfig()` returns `serviceApiKey` from `SKYLIZE_SERVICE_API_KEY`
  (`website/src/lib/skylize/config.ts:63-85`).
- `skylizeFetch` is the ONLY consumer that attaches it, as the `X-API-Key` header
  (`website/src/lib/skylize/client.ts:142-151`).

### 5.0.2 The construction `[OWNER-DECIDED + DERIVED]`

The My Day code path (its cookie module, its handler helper, and its route files) MUST NOT
import:

- `website/src/lib/skylize/client.ts` (holds `skylizeFetch` / the `X-API-Key` attachment), or
- `getBackendConfig` from `website/src/lib/skylize/config.ts` (the only getter that returns
  `serviceApiKey`).

Instead, My Day has its OWN backend-call helper that forwards the user's access JWT as
`Authorization: Bearer <...>` and has no code path that can read `serviceApiKey`. Because a
credential that is never imported cannot be sent, the fallback is impossible rather than
merely discouraged.

### 5.0.3 The proof obligation `[HARD GATE]`

Stage 1 is NOT complete until this is demonstrated, not assumed. The implementer must show -
by import-graph inspection - that no module reachable from `/api/my-day/*` transitively
imports `client.ts` or `getBackendConfig`. If such a reachable import exists, even
indirectly, STOP and report; the design's central property is unmet.

---

## 6.0 - The isolation proof

### 6.0.1 Two orgs, two owners `[OWNER-DECIDED, forced by the backend]`

`[CODE-VERIFIED]` There is deliberately NO way to add a second user to an existing org over
HTTP; `register` mints an owner for a NEW org or refuses (`src/skylize/edge/routes/auth.py:96-142`,
`src/skylize/app/auth/user_service.py:55-99`). The governed invite flow is tracked separately
(E20) and stays closed.

`[DERIVED]` Therefore the multi-tenant isolation proof uses **two different orgs, each with
its own owner** (not two users in one org). Register org A's owner and org B's owner, log
each in through `/api/my-day/session`, and assert that A's My Day session, presented to a
per-user/per-org read, sees only A's identity/data and never B's, and vice versa. This is a
real end-to-end assertion against the running backend, not a mocked one.

### 6.0.2 Test truthfulness `[HARD GATE]`

Per repo TESTING discipline, a Postgres-backed integration test that SKIPS for want of env
vars proves nothing. The isolation proof must be reported as VERIFIED only if it actually
RAN against a real Postgres with the correct non-superuser app role; otherwise it is reported
explicitly as UNVERIFIED.

---

## 7.0 - File-ownership split (implementation)

Disjoint ownership, no overlap:

- **Subagent A** - the identity/cookie backend as NEW files under
  `website/src/lib/skylize/` (a My-Day analogue of `session.ts` + `config.ts` +
  `handler.ts`). Reads Console's files as reference; writes none of them.
- **Subagent B** - the NEW `/api/my-day/*` route files, plus the single additive line in
  `website/src/proxy.ts` (section 4.0.2). Confirms the line is truly additive first.
- **Subagent C** - tests only, including the live two-org isolation proof (section 6.0).
  Reads A's and B's output; writes only test files.

Any subagent that finds it needs a file outside its ownership STOPS and reports rather than
crossing the boundary.

---

## 8.0 - Explicitly out of scope

- Stage 2: wiring the `my-day` frontend page (`website/src/app/(site)/my-day/page.tsx`,
  today a static tour page) to a `useMyDayState` hook.
- The governed invite flow (a second user in one org), E20 - stays closed.
- Any change to Console behaviour beyond the one additive matcher element in section 4.0.2.
- Any new backend endpoint or database migration.

---

## Sign-off

- **Design approved:** repo owner, chat session 2026-09-24; re-confirmed 2026-09-27.
- **First committed record:** 2026-09-27 (this document), during Stage 1 implementation.
- **Build authorization:** Stage 1 only, per section 7.0 ownership split and the hard gates
  in sections 4.0.2, 5.0.3, and 6.0.2.
