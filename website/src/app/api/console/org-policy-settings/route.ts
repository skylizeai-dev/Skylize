// GET  /api/console/org-policy-settings -> backend GET /api/v1/org-policy-settings
// PUT  /api/console/org-policy-settings -> backend PUT /api/v1/org-policy-settings
//
// WHY THIS ROUTE EXISTS. Same reasoning as /api/console/autonomy
// (app/api/console/autonomy/route.ts): the guardrails/retention portion of the
// Org Settings screen lives in a static SPA (app/Skylize-Console-Black) that
// can hold no credential of its own, so the SPA calls this handler same-origin
// and the SERVER attaches the service API key, exactly as every other
// /api/console/* route does. The key never reaches the browser.
//
// AUTH TO THE BACKEND is the service API key in `X-API-Key`
// (lib/skylize/client.ts). The backend's PUT is owner-only
// (edge/routes/org_policy_settings.py, the same `require_role("owner")` the
// autonomy PUT and the kill switch use -- this is the same authority class:
// org-wide policy), so the key this deployment holds must be issued with the
// `owner` scope -- a key's scopes BECOME its roles (app/auth/service.py:95).
//
// The GET is deliberately NOT projected down to bare values: each guardrail
// carries an `enforced` flag alongside its `value`, and `configured`
// distinguishes "the owner chose these settings" from "nobody has chosen
// yet" -- both are things the Settings screen must be able to say out loud.
// See dal/org_policy_settings.py and edge/routes/org_policy_settings.py for
// why none of the four guardrails is `enforced: true` today.
//
// NO REGION FIELD. Verified against real infra
// (migrations/versions/0035_org_policy_settings.py cites the exact files)
// that region is a per-environment Terraform variable, not a per-org concept,
// so the backend response has no region field and this proxy invents none.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import { RETENTION_DAYS_MAX, RETENTION_DAYS_MIN } from "@/lib/skylize/types";
import type {
  BackendOrgPolicySettingsResponse,
  ConsoleOrgPolicySettingsResponse,
  SetOrgPolicySettingsInput,
} from "@/lib/skylize/types";

// Validated HERE as well as at the backend so an out-of-range retention value
// costs a 400 at the edge instead of a round trip that comes back 422.
const setOrgPolicySettingsSchema = z.strictObject({
  spend_cap_alert_enabled: z.boolean(),
  email_domain_restriction_enabled: z.boolean(),
  pii_redaction_enabled: z.boolean(),
  silent_fallback_suppressed: z.boolean(),
  retention_days: z.number().int().min(RETENTION_DAYS_MIN).max(RETENTION_DAYS_MAX),
});

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const result = await skylizeFetch<BackendOrgPolicySettingsResponse>(
      "/api/v1/org-policy-settings",
    );
    const payload: ConsoleOrgPolicySettingsResponse = result;
    return NextResponse.json(payload);
  },
});

export const PUT = consoleRoute<z.infer<typeof setOrgPolicySettingsSchema>>({
  method: "PUT",
  schema: setOrgPolicySettingsSchema,
  handler: async ({ body }) => {
    const input: SetOrgPolicySettingsInput = body;
    const result = await skylizeFetch<BackendOrgPolicySettingsResponse>(
      "/api/v1/org-policy-settings",
      {
        method: "PUT",
        body: input,
      },
    );
    // The backend echoes what it actually stored (and each guardrail's real
    // enforcement status). Forward THAT, never the requested value: the
    // console must render what was persisted, not what was requested.
    const payload: ConsoleOrgPolicySettingsResponse = result;
    return NextResponse.json(payload);
  },
});
