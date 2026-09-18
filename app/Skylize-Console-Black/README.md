# Skylize Console (Black)

Dark "Precision Altitude" ops console — ported from the Claude Design prototype
(`console-v2-src.html`) into a real React + Vite app. Full nav: Dashboard,
Command, Starmap, Workflows, Approvals, Analytics, Models, Knowledge,
Integrations, Developers, Audit Log, Security, Team, Billing, Settings.

NOTE ON AGENT COUNTS. The bundled `agentNetworkData.js` fixture describes 151
agents across 18 departments and is now used ONLY for the Starmap's geometry.
The governed registry holds **23 agents across 9 departments**
(`src/skylize/contracts/mvp/__init__.py`, `ALL_MVP_CONTRACTS`), and every count
an operator reads comes from `GET /api/v1/agents` at runtime.

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

### What is wired to the backend

Each of these does a GET on mount through the BFF, with real loading and
failure states. NO screen falls back to sample data when a call fails -- an
unreachable backend is stated, never papered over, because "the queue is empty"
and "I cannot see the queue" are opposite facts.

| Screen | Backend |
| --- | --- |
| Settings · autonomy | `GET`/`PUT /api/v1/autonomy` |
| Settings · danger zone | `POST /api/v1/kill-switch/engage` (tenant scope; the operator supplies the reason) |
| Command | `POST /api/v1/cowork/turns` |
| Approvals | `GET /api/v1/hitl`, `POST /api/v1/hitl/{id}/approve\|reject` |
| Audit Log | `GET /api/v1/audit` |
| Developers · API keys | `GET`/`POST`/`DELETE /api/v1/api-keys` |
| Team · members | `GET /api/v1/tenants/me/users` |
| Dashboard · agent + approval counts | `GET /api/v1/agents`, `GET /api/v1/hitl` |

### What the UI dropped, and why

The design asked for several things the platform does not have. They were
REMOVED or RELABELLED rather than filled with plausible-looking values, because
a governance console that invents a governance fact is worse than one that
admits a gap:

* **Approvals** — no risk HIGH/MED/LOW tier, no money amount, no delegation
  chain. `HitlItemResponse` has none of the three; the row shows the recorded
  `trigger_reason` and `status` instead.
* **Audit Log** — "ACTOR" became **SOURCE AGENT** (`source_agent_id` is an
  agent id or null; there is no human-actor column), and "SIGNATURE" became
  **INPUTS HASH** (`inputs_hash` is a SHA-256 content hash, not a signature).
* **Team** — no email, display name, last-active or MFA column; the backend
  returns `{user_id, role}` only. The role permission matrix is labelled
  "designed, not built".
* **Command** — the attachment, agent/department tag and connector pickers are
  visibly DISABLED. The turn API accepts `message` only and fixes the agent and
  principal itself, so a picker that still worked would imply a routing
  decision the backend never made. The delegation rail and per-turn token
  counts are likewise not reported by the API and say so.

### Still NOT wired (no backend exists)

Models, Billing, Security posture, Knowledge, Workflows list/history,
Notifications, the permission matrix and org policy writes are design comps.
They need product/backend decisions first, not wiring.

## Structure

- `src/data/agentNetworkData.js` — the generated 151-agent / 18-department
  fixture. STARMAP GEOMETRY ONLY; it is not the governed roster (see the note
  at the top).
- `src/lib/consoleClient.js` — every backend call except autonomy, aimed at the
  BFF. Returns only fields the backend actually sends.
- `src/hooks/useConsoleState.js` — all app state + the view-model every screen reads from (`vm`).
- `src/lib/autonomyClient.js` — the org autonomy mode calls. See "Backend calls".
- `src/lib/style.jsx` — `sx()` parses literal CSS strings into React style objects; `<Interactive>` handles hover/focus states. Every screen uses these so styles stay byte-for-byte faithful to the original design.
- `src/screens/*.jsx` — one component per nav item.
- `src/components/` — TopBar, Sidebar, Toast.

Accent color and motion intensity are set in `src/App.jsx` (`DEFAULT_PROPS`).
