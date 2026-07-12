# Operator Console ↔ BFF Contract

How the single-owner operator console talks to the Skylize backend, and what
it takes to bring the loop live. The console UI (this work line) owns
`src/app/console/**` and `src/components/console/**`; the BFF work line owns
`src/app/api/console/**`, `src/lib/skylize/**`, and the proxy. The two meet
**only** over same-origin HTTP.

## Topology

```
┌───────────┐   same-origin fetch    ┌──────────────────────┐   Bearer sky.<key>   ┌─────────────────┐
│  Browser   │ ───────────────────▶ │  Next.js BFF          │ ──────────────────▶ │  Skylize backend │
│  /console  │   /api/console/*     │  route handlers       │   SKYLIZE_BACKEND_   │  (Railway)       │
│            │ ◀─────────────────── │  + proxy gate         │   URL                │                  │
└───────────┘   JSON view models    └──────────────────────┘ ◀────────────────── └─────────────────┘
      ▲                                      │
      └── httpOnly cookie "skylize_console" ─┘   (set by POST /api/console/session;
          the browser NEVER sees the sky. key — it lives only in server env)
```

Rules the console UI codes to:

- **All fetches are same-origin** (`/api/console/...`). The Railway URL never
  appears in client code or in server components — only the BFF touches it.
- **Auth is invisible to the UI.** The login route sets the httpOnly
  `skylize_console` cookie; every subsequent request carries it
  automatically. Server components forward the incoming `cookie` header when
  they fetch the BFF.
- The proxy (BFF-owned) protects `/console/**` and `/api/console/**`
  **except** `/console/login` and `/api/console/session`.

## Frozen endpoint contract

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /api/console/session` | `{ password }` | `204` + sets cookie, or `401` |
| `DELETE /api/console/session` | — | clears the session cookie |
| `GET /api/console/health` | — | `{ status, backend }` |
| `GET /api/console/tenant` | — | `{ org_id, display_name, status }` |
| `POST /api/console/workflows/creative` | `{ product, audience, count? }` | `{ status, hooks: string[] }` |
| `POST /api/console/kill-switch` | `{ scope_type, scope_id, reason }` | `{ status }` |

The console defines its own small view-model interfaces for these shapes
(see `src/app/console/page.tsx`, `src/components/console/*.tsx`) rather than
importing BFF types — the HTTP contract, not shared code, is the boundary.

## Server env (`.env.local`)

See [.env.local.example](../.env.local.example). Four vars, all server-only:
`SKYLIZE_BACKEND_URL`, `SKYLIZE_SERVICE_API_KEY`, `SKYLIZE_CONSOLE_PASSWORD`,
`SKYLIZE_CONSOLE_SESSION_SECRET`. **`SKYLIZE_SERVICE_API_KEY` must never be
`NEXT_PUBLIC_`-prefixed or imported into a client component.**

## Bootstrap sequence (first live round-trip)

1. **Register the tenant** on the backend (Railway) — this yields the
   `org_id` the console's tenant card displays.
2. **Issue a service key** (`sky.` prefix) scoped to that tenant and put it
   in the website's server env as `SKYLIZE_SERVICE_API_KEY`.
3. **Turn off dev auth on Railway**: set `SKYLIZE_DEV_AUTH=false` so the
   backend starts enforcing real key auth.
4. Set the remaining website vars (`SKYLIZE_BACKEND_URL`,
   `SKYLIZE_CONSOLE_PASSWORD`, `SKYLIZE_CONSOLE_SESSION_SECRET`), deploy,
   then log in at `/console/login` and run the creative workflow — hooks
   rendering in the browser proves browser → BFF → backend → browser.

## Interim gate → OIDC

The password gate is a deliberate stopgap for the single-owner phase: one
shared passphrase, one signed httpOnly cookie, no user records. When the
console goes multi-user it is replaced by OIDC (login page swaps to the IdP
redirect; `POST/DELETE /api/console/session` become the code-exchange and
logout endpoints; the cookie carries the verified identity). Nothing in the
console UI stores auth state client-side, so the swap is confined to the
session route and the login page.
