# Deploying the website to Railway

The website (marketing site + operator console + its BFF) is a single Next.js
app that needs a Node runtime — static export is not an option: the console
BFF route handlers hold the server-only backend key, `src/proxy.ts` gates
`/console/**`, and the console page renders per-request. It deploys to
Railway next to the backend service.

## One-time service setup

1. In the Railway project that hosts the backend, add a new service from this
   GitHub repo.
2. Set the service **Root Directory** to `website/`. Build and start commands
   come from `website/railway.json` (`npm run build` / `npm run start`).
3. Set the environment variables below.
4. Attach the public domain (e.g. `skylize.ai`). The console lives on the
   same origin under `/console` — the session cookie is host-only, so no
   cross-domain setup is needed.

## Environment variables

All server-only; none may ever gain a `NEXT_PUBLIC_` prefix. See
`.env.local.example` for the same list with full commentary.

| Variable | Required | Purpose |
| --- | --- | --- |
| `SKYLIZE_BACKEND_URL` | yes | Backend origin. Prefer the Railway-internal hostname so BFF→backend traffic stays on the private network. |
| `SKYLIZE_SERVICE_API_KEY` | yes | The issued `sky.` key the BFF presents as `X-API-Key`. |
| `SKYLIZE_CONSOLE_PASSWORD` | yes | Interim single-owner console passphrase. |
| `SKYLIZE_CONSOLE_SESSION_SECRET` | yes | HMAC secret for the `skylize_console` cookie; rotate to invalidate sessions. |
| `N8N_API_URL` | no | n8n instance for the console workflow builder; route answers 503 while unset. |
| `N8N_API_KEY` | no | API key for that instance. |

The only build-time public var is `NEXT_PUBLIC_SITE_URL` (canonical site URL
for metadata/sitemap; falls back to `https://skylize.ai`).

## Health check

`railway.json` points the health check at `/` — the console health endpoint
(`/api/console/health`) sits behind the auth gate and is not usable for
platform probes.

## Related

- `website/docs/CONSOLE_BFF.md` — console/BFF architecture, key issuance,
  tenant bootstrap.
- The backend does not need CORS changes for this: all browser traffic goes
  through the same-origin BFF, and CORS on the FastAPI gateway stays disabled
  by default.
