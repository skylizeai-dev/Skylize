// GET  /api/console/autonomy -> backend GET /api/v1/autonomy
// PUT  /api/console/autonomy -> backend PUT /api/v1/autonomy
//
// WHY THIS ROUTE EXISTS. The autonomy control lives in a static SPA
// (app/Skylize-Console-Black) that can hold no credential of its own: anything
// baked into a Vite build ships to every visitor. So the SPA calls this handler
// same-origin and the SERVER attaches the service API key, exactly as every
// other /api/console/* route does. The key never reaches the browser.
//
// AUTH TO THE BACKEND is the service API key in `X-API-Key`
// (lib/skylize/client.ts). The backend's PUT is owner-only
// (edge/routes/autonomy.py:84, the same `require_role("owner")` the kill switch
// uses), so the key this deployment holds must be issued with the `owner`
// scope -- a key's scopes BECOME its roles (app/auth/service.py:95). That path
// needed no backend change; it is proven end-to-end in
// tests/unit/test_autonomy_api_key_auth.py.
//
// The GET is deliberately NOT projected down to just `mode`: `configured`
// distinguishes "the owner chose observe" from "nobody has chosen yet", which
// is the one thing the Settings screen has to be able to say out loud.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import { AUTONOMY_MODES } from "@/lib/skylize/types";
import type {
  BackendAutonomyResponse,
  ConsoleAutonomyResponse,
  SetAutonomyModeInput,
} from "@/lib/skylize/types";

// Validated HERE as well as at the backend so an unknown mode costs a 400 at
// the edge instead of a round trip that comes back 422. `AUTONOMY_MODES` is the
// single list both this schema and the browser read, so the two cannot drift.
const setAutonomySchema = z.strictObject({
  mode: z.enum(AUTONOMY_MODES),
});

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const result = await skylizeFetch<BackendAutonomyResponse>("/api/v1/autonomy");
    const payload: ConsoleAutonomyResponse = {
      mode: result.mode,
      configured: result.configured,
    };
    return NextResponse.json(payload);
  },
});

export const PUT = consoleRoute<z.infer<typeof setAutonomySchema>>({
  method: "PUT",
  schema: setAutonomySchema,
  handler: async ({ body }) => {
    const input: SetAutonomyModeInput = body;
    const result = await skylizeFetch<BackendAutonomyResponse>("/api/v1/autonomy", {
      method: "PUT",
      body: input,
    });
    // The backend echoes the mode it actually stored. Forward THAT, never the
    // requested value: the console must render what was persisted.
    const payload: ConsoleAutonomyResponse = {
      mode: result.mode,
      configured: result.configured,
    };
    return NextResponse.json(payload);
  },
});
