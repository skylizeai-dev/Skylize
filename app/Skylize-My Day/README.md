# Skylize My Day

Light "Precision Altitude" agent daily-briefing app — ported from the Claude
Design prototype (`Skylize My Day.dc.html`) into a real React + Vite app.
Screens: Morning Brief, Co-work, What it can do, Agent map, Past tasks, Paused
item, Mobile glance.

## Run locally

```
npm install
npm run dev
```

Open the printed localhost URL. `npm run build` produces a static `dist/` you
can deploy anywhere.

## Trying the different scenarios

The prototype's six demo states (typical / day one / quiet night / all paused
/ long absence / loading) are driven by a `?scenario=` query param instead of
the design tool's dev-only property panel:

```
http://localhost:5173/?scenario=day-one
http://localhost:5173/?scenario=quiet-night
http://localhost:5173/?scenario=everything-blocked
http://localhost:5173/?scenario=long-absence
http://localhost:5173/?scenario=loading
```

(omit the param, or use `typical`, for the default state)

## Structure

- `src/hooks/useMyDayState.js` — all app state, the six scenario definitions, and the view-model every screen reads from (`vm`).
- `src/lib/style.jsx` — `sx()` parses literal CSS strings into React style objects; `<Interactive>` handles hover/focus states.
- `src/screens/*.jsx` — one component per nav item plus the mobile glance and paused-item detail views.
- `src/components/` — TopBar, Sidebar.

Accent color and the signed-in person's name are set in `src/App.jsx`
(`DEFAULT_PROPS`).
