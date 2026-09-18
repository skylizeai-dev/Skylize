// GET  /api/console/api-keys -> backend GET  /api/v1/api-keys
// POST /api/console/api-keys -> backend POST /api/v1/api-keys
//
// THE MINT-WITHOUT-REVOKE ASYMMETRY, AND WHY IT DOES NOT BITE HERE.
// The three backend verbs do not resolve their caller the same way: POST goes
// through `require_any_role_or_user`, while GET and DELETE still go through
// `require_any_role` (api_keys.py:68, 87, 103 — "widening them was not in the
// approved scope"). That difference is REAL and still unresolved on main.
//
// It does not disable this screen, because the difference is about which
// CREDENTIAL can authenticate, not which role is required. `_or_user`
// additionally accepts a human's Skylize access JWT; every verb still accepts
// an X-API-Key caller through `get_context`. This BFF authenticates with the
// service API KEY (client.ts), never a JWT, so all three verbs resolve.
//
// WHAT IT DOES MEAN: a future console that forwards a logged-in human's JWT
// instead of the service key would be able to MINT a key and then not list or
// revoke it. That is a real trap for the OIDC epic and is flagged, not fixed
// here — fixing it means widening two backend routes, which is out of scope.
//
// THE SECRET. POST's response carries `api_key` in plaintext, the only time it
// ever exists (api_keys.py:6-8). It is forwarded to the browser once, exactly
// as the backend returned it; it is never logged here and the console must not
// persist it.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type {
  BackendApiKey,
  BackendIssuedApiKey,
  IssueApiKeyInput,
} from "@/lib/skylize/types";

// Mirrors IssueKeyRequest (extra="forbid"): name 1..120, optional scopes,
// optional expiry 1..365 days.
const issueKeySchema = z.strictObject({
  name: z.string().min(1).max(120),
  scopes: z.array(z.string().min(1).max(120)).max(50).optional(),
  expires_in_days: z.int().min(1).max(365).nullable().optional(),
});

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const keys = await skylizeFetch<BackendApiKey[]>("/api/v1/api-keys");
    return NextResponse.json(keys);
  },
});

export const POST = consoleRoute<z.infer<typeof issueKeySchema>>({
  method: "POST",
  schema: issueKeySchema,
  handler: async ({ body }) => {
    const input: IssueApiKeyInput = body;
    const issued = await skylizeFetch<BackendIssuedApiKey>("/api/v1/api-keys", {
      method: "POST",
      body: input,
    });
    return NextResponse.json(issued);
  },
});
