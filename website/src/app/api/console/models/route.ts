// GET /api/console/models -> backend GET /api/v1/models
//
// WHY THIS ROUTE EXISTS. The Models screen lives in a static SPA
// (app/Skylize-Console-Black) that can hold no credential of its own: anything
// baked into a Vite build ships to every visitor. So the SPA calls this handler
// same-origin and the SERVER attaches the service API key, exactly as every
// other /api/console/* route does. The key never reaches the browser.
//
// AUTH TO THE BACKEND is the service API key in `X-API-Key`
// (lib/skylize/client.ts). The backend's GET is owner-or-admin
// (edge/routes/models.py, `require_any_role_or_user("owner", "admin")` -- the
// same read role the autonomy GET uses), and a key's scopes BECOME its roles
// (app/auth/service.py:95), so the key this deployment already holds for the
// autonomy route clears this one too. No backend auth change was needed.
//
// READ ONLY. There is no PUT here because there is no backend PUT: writing
// routing changes which priced model an org's spend is incurred against, and
// that setter is not yet exposed.
//
// NOTHING IS ENRICHED ON THE WAY THROUGH. The response is forwarded field for
// field. In particular this handler does NOT fill in a latency figure, a
// context-window size or a traffic-share percentage -- no backend source exists
// for the first two, and the third is derivable from ai_cost_ledger rather than
// configured anywhere. A default supplied here would be indistinguishable to
// the browser from a measured value.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type {
  BackendModelsResponse,
  ConsoleModelsResponse,
} from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const result = await skylizeFetch<BackendModelsResponse>("/api/v1/models");
    // `pricing` stays null wherever the backend sent null. model_pricing is
    // seeded empty by design, so null is the expected value and means "nobody
    // has priced this model" -- coercing it to 0 would turn an absence into a
    // free-of-charge claim.
    const payload: ConsoleModelsResponse = {
      catalogue: result.catalogue,
      routing: result.routing,
      pricing_configured: result.pricing_configured,
    };
    return NextResponse.json(payload);
  },
});
