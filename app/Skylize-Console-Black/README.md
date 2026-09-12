# Skylize Console (Black)

Dark "Precision Altitude" ops console — ported from the Claude Design prototype
(`console-v2-src.html`) into a real React + Vite app. 151 agents / 15 departments,
full nav: Dashboard, Command, Starmap, Workflows, Approvals, Analytics, Models,
Knowledge, Integrations, Developers, Audit Log, Security, Team, Billing, Settings.

## Run locally

```
npm install
npm run dev
```

Open the printed localhost URL. `npm run build` produces a static `dist/`.

## Backend calls

This app holds **no credential**. It is a static bundle, so anything compiled
into it is served to every visitor -- and `vite.config.js` enforces that: the
build FAILS if any `VITE_*` variable is named like a secret (key / token /
secret / password / credential / private / auth), whether it comes from the
environment or a `.env` file.

Backend reads and writes therefore go through the server-side BFF in
`website/` (`website/src/app/api/console/*`), which holds the service API key
server-side and forwards to the FastAPI backend. Calls from here use RELATIVE,
same-origin paths (`/api/console/...`), so there is no CORS to open and no
origin baked into the bundle.

* **Dev / preview** -- Vite proxies `/api/console/*` to the BFF. It defaults to
  `http://localhost:3000`; override with `CONSOLE_BFF_ORIGIN` (a build/dev-time
  variable for the proxy, deliberately NOT a `VITE_` one, so it never reaches
  the bundle). Run the BFF with `npm run dev` in `website/`.
* **Production** -- `dist/` must be served such that `/api/console/*` reaches
  the BFF: either same-origin hosting, or a reverse proxy in front of both.
  Deploying `dist/` to an origin with no route to the BFF leaves every backend
  call returning the console's fail-closed state.

Currently wired: the org autonomy mode on Settings (GET on mount, PUT on
change). Every other screen is still seeded sample data.

## Structure

- `src/data/agentNetworkData.js` — the 151-agent / 15-department dataset (ported verbatim from the source).
- `src/hooks/useConsoleState.js` — all app state + the view-model every screen reads from (`vm`).
- `src/lib/autonomyClient.js` — the console's only backend calls (org autonomy mode), aimed at the BFF. See "Backend calls".
- `src/lib/style.jsx` — `sx()` parses literal CSS strings into React style objects; `<Interactive>` handles hover/focus states. Every screen uses these so styles stay byte-for-byte faithful to the original design.
- `src/screens/*.jsx` — one component per nav item.
- `src/components/` — TopBar, Sidebar, Toast.

Accent color and motion intensity are set in `src/App.jsx` (`DEFAULT_PROPS`).
