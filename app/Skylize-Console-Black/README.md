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

Open the printed localhost URL. `npm run build` produces a static `dist/` you
can deploy anywhere.

## Structure

- `src/data/agentNetworkData.js` — the 151-agent / 15-department dataset (ported verbatim from the source).
- `src/hooks/useConsoleState.js` — all app state + the view-model every screen reads from (`vm`).
- `src/lib/style.jsx` — `sx()` parses literal CSS strings into React style objects; `<Interactive>` handles hover/focus states. Every screen uses these so styles stay byte-for-byte faithful to the original design.
- `src/screens/*.jsx` — one component per nav item.
- `src/components/` — TopBar, Sidebar, Toast.

Accent color and motion intensity are set in `src/App.jsx` (`DEFAULT_PROPS`).
