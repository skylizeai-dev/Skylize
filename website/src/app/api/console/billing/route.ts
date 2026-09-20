// GET /api/console/billing -> backend GET /api/v1/billing/usage
//
// WHY THIS ROUTE EXISTS. The Billing screen lives in a static SPA
// (app/Skylize-Console-Black) that can hold no credential of its own: anything
// baked into a Vite build ships to every visitor. So the SPA calls this handler
// same-origin and the SERVER attaches the service API key, exactly as every
// other /api/console/* route does. The key never reaches the browser.
//
// AUTH TO THE BACKEND is the service API key in `X-API-Key`
// (lib/skylize/client.ts). The backend read is owner-or-admin
// (edge/routes/billing.py, `require_any_role_or_user("owner", "admin")` -- the
// same read role as the autonomy GET and the audit feed), so the key this
// deployment already holds clears it: a key's scopes BECOME its roles
// (app/auth/service.py:95).
//
// READ-ONLY, deliberately. There is no PUT/POST here because there is nothing a
// console user may write: the org spend ceiling is set only through the audited
// operator seam (OrgSpendCeilingDAL.set_ceiling), never over HTTP.
//
// WHAT THIS PAYLOAD DOES NOT CARRY, and why it is forwarded verbatim. The
// Billing screen was designed with a plan tier, an invoice table, a seat count
// and an agent-slot cap. NONE of those have a backing table, a migration or a
// row anywhere in this repo, so the backend response has NO FIELD for any of
// them -- not a null, which would read as "we have this and it is unset". Their
// structural absence IS the signal, and `unavailable_sections` names them so
// the screen can say "not available yet" from data rather than a hard-coded
// string. This handler therefore projects NOTHING away: inventing a field here
// would be exactly the fabrication the backend refused to commit.
//
// MONEY UNITS. Every `*_micros` field is MICRO-currency (millionths of one
// currency unit), the unit ai_cost_ledger stores (ADR-0006). It is NOT cents.
// Forwarded unconverted so the single conversion happens at the one place that
// formats it for a human; a component that reads one of these as cents is off
// by 10,000x. `ceiling_micros` is a GOVERNANCE cap set by ops, NOT a plan
// allowance -- it is honest to show "spent X of ceiling Y", not to call Y a plan.
//
// A ZERO IS A REAL ANSWER. model_pricing ships EMPTY by design (migration 0012),
// so a deployment whose prices ops has not seeded records no ledger rows and
// this endpoint truthfully reports zero spend and an empty breakdown. The screen
// must render that as "nothing spent", never as a loading or error state.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { ConsoleBillingUsage } from "@/lib/skylize/types";

// Mirrors the backend's Query(ge=1, le=36) bound so a bad value costs a 400 at
// the edge instead of a round trip that comes back 422.
const DEFAULT_PERIODS = 12;
const MAX_PERIODS = 36;

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const raw = request.nextUrl.searchParams.get("periods");
    const periods = raw === null ? DEFAULT_PERIODS : Number(raw);
    if (!Number.isInteger(periods) || periods < 1 || periods > MAX_PERIODS) {
      return errorResponse(
        400,
        `periods must be an integer between 1 and ${MAX_PERIODS}.`,
      );
    }

    const query = new URLSearchParams({ periods: String(periods) });
    const usage = await skylizeFetch<ConsoleBillingUsage>(
      `/api/v1/billing/usage?${query.toString()}`,
    );
    return NextResponse.json(usage);
  },
});
