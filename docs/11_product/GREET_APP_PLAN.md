# Greet App Plan — GaaS (Greeting-as-a-Service)

**Status:** Draft plan · **Date:** 2026-06-05 · **Scope:** Full-stack feature on the Skylize platform · **Deliverable:** plan only (no code yet)

## 1. Goal

Add a "Greet" capability to Skylize, exposed as **Greeting-as-a-Service (GaaS)**: a backend endpoint that produces a personalized greeting for the authenticated caller (and optionally a named recipient), plus a frontend page that calls it and renders the result.

MVP delivers a deterministic, context-aware greeting (uses the caller's verified `org_id` / `user_id` and time-of-day). A later phase routes greeting generation through the agent orchestrator for LLM-personalized copy.

## 2. Where it fits in the existing architecture

The codebase already has a clean edge → app → dal spine. The greet feature mirrors the existing `tenants` slice exactly.

| Layer | Existing example | New file(s) for greet |
|-------|------------------|-----------------------|
| Edge route (FastAPI router) | [tenants.py](../../src/skylize/edge/routes/tenants.py) | `src/skylize/edge/routes/greet.py` |
| Router registration | [gateway.py](../../src/skylize/edge/gateway.py) `include_router(...)` | add `app.include_router(greet.router)` |
| App service (business logic) | `src/skylize/app/tenants/service.py` | `src/skylize/app/greet/service.py` (+ `__init__.py`) |
| Request context / deps | `edge/deps.py` (`get_context`, `get_container`) | reuse as-is |
| Container wiring | `bootstrap.py` (`Container`) | add `greet` provider |
| Frontend page | [page.tsx](../../frontend/src/app/page.tsx) + `components/sections` | `frontend/src/app/greet/page.tsx` + `components/greet/*` |
| Frontend API client | `frontend/src/lib/` | `frontend/src/lib/greet.ts` |

Conventions to follow (observed in `tenants.py`):
- `APIRouter(prefix="/api/v1/greet", tags=["greet"])`
- Pydantic request/response models with `model_config = ConfigDict(extra="forbid")`
- Identity comes from `RequestContext` via `Depends(get_context)` — never trust client-supplied identity.
- Errors raised as a typed `GreetError` in the service, mapped to `HTTPException` in the route.

## 3. API contract (MVP)

`POST /api/v1/greet`

Request:
```json
{ "recipient_name": "Ada",         // optional, 1..200 chars; defaults to caller
  "style": "friendly" }            // optional enum: friendly | formal | playful
```

Response `200`:
```json
{ "message": "Good evening, Ada — welcome back to Skylize.",
  "style": "friendly",
  "org_id": "org_123",
  "generated_at": "2026-06-05T20:45:00Z" }
```

Errors: `422` validation (handled by Pydantic), `400`/`409` via `GreetError` for business rules.

Optional `GET /api/v1/greet/health` returning `{ "ok": true }` for a trivial smoke check.

## 4. Phases

### Phase 0 — Scaffolding & contract (no logic)
- Create `app/greet/` package with empty `service.py` exposing `GreetService` stub + `GreetError`.
- Create `edge/routes/greet.py` with router, request/response models, and a hardcoded greeting.
- Register router in `gateway.py`.
- **Exit:** `POST /api/v1/greet` returns a static 200 through the gateway; OpenAPI shows the route.

### Phase 1 — Deterministic greeting logic
- `GreetService.greet(ctx, recipient_name, style)` builds the message from: time-of-day (server UTC → bucketed), `recipient_name or ctx.user_id`, and `style` template map.
- Wire `GreetService` into `Container` in `bootstrap.py`; inject via `get_container`.
- **Exit:** message varies correctly by style, recipient, and time bucket.

### Phase 2 — Frontend page
- Add `frontend/src/lib/greet.ts` — typed `fetch` wrapper hitting the gateway base URL (read from existing env config used by other frontend calls; confirm pattern).
- Add `frontend/src/app/greet/page.tsx` — input for recipient name, style selector (shadcn `Select`), a "Greet me" button, and a card showing the returned message.
- Reuse shadcn components already vendored (`components.json` present).
- **Exit:** visiting `/greet` and submitting renders the live backend greeting.

### Phase 3 — Tests
- Backend: `tests/` — unit test for `GreetService` (style/time/recipient matrix) + an API test through the FastAPI test client (mirror existing tenants tests).
- Frontend: a render/interaction test for the greet page if a test setup exists; otherwise a `lib/greet.ts` unit test.
- **Exit:** `pytest` green; lint (`ruff`) + `mypy` clean (project already enforces both — see `pyproject.toml`).

### Phase 4 (optional, later) — Agent-personalized greetings
- Add `style: "ai"` that routes through the orchestrator (`app/orchestrator/`) to an LLM provider for bespoke copy, with the deterministic greeting as fallback.
- Add persistence: log greetings to a `greet_log` table via a new Alembic migration in `migrations/` if product wants history/analytics.

## 5. Open questions (resolve before Phase 1)
1. Is GaaS a **public/external** product surface or **internal** to Skylize tenants? (Affects auth strictness + rate limiting via `edge/rate_limit.py`.)
2. Should greetings be **persisted** (history/audit) or **stateless**? MVP assumes stateless.
3. Confirm the **frontend → backend base URL/env** convention (none of the existing `components/sections` appear to call the API yet — verify before Phase 2).
4. Phase 4 LLM personalization — in scope now, or defer?

## 6. Risks / notes
- The frontend currently looks like a **marketing landing page** (Hero, Pricing, Testimonials) with no API calls yet — Phase 2 may be the first real client→gateway integration, so CORS and base-URL config likely need first-time setup.
- Keep identity server-derived (`RequestContext`); do not add a client-supplied `user_id`.
- No git repo detected at the workspace root — confirm version control before landing code.
