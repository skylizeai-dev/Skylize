// GET /api/console/security -> backend GET /api/v1/security/activity.
//
// Server-side only, like every other /api/console/* handler: the console SPA
// (app/Skylize-Console-Black) is a static bundle and can hold no credential, so
// it calls this route same-origin and `skylizeFetch` attaches the service API
// key here, on the server. The key never reaches the browser.
//
// WHAT THIS FEED IS NOT — and this matters more on this screen than on any
// other, because a security page is read as an assurance:
//
//   * THERE IS NO POSTURE SCORE. The mock showed a hardcoded 94. No scoring
//     methodology exists anywhere in this system, so any number rendered in
//     that spot would be invented by the UI and then trusted as a measurement.
//     The backend deliberately does not return one (edge/routes/security.py).
//   * THERE IS NO CONTROL INVENTORY. The mock listed eight controls — SSO,
//     SCIM, encryption at rest, data residency, HITL, PII redaction, sandbox
//     isolation, pen test. Not one of them has a table, a flag or a probe
//     behind it. A control shown as enabled because a card exists for it is
//     how a control gets believed in without being implemented.
//   * THERE ARE NO COMPLIANCE BADGES. The mock showed SOC 2, ISO 27001, GDPR
//     and HIPAA-READY. A compliance claim is an assertion about an audit an
//     auditor performed; emitting one from an API is a false claim about a
//     third party, not a display shortcut.
//
// The console must render the ABSENCE of those three, not fill it. This route
// forwards exactly what the backend measured and nothing more.
//
// `window_hours` and `limit` are validated here rather than spent on a round
// trip: the backend's Query constraints are ge=1/le=2160 and ge=1/le=200
// respectively (edge/routes/security.py), and an out-of-range value would come
// back 422 otherwise.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendSecurityActivityResponse } from "@/lib/skylize/types";

const MAX_WINDOW_HOURS = 24 * 90;
const DEFAULT_WINDOW_HOURS = 24;
const MAX_LIMIT = 200;
const DEFAULT_LIMIT = 20;

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const params = request.nextUrl.searchParams;

    const windowHours = Number(
      params.get("window_hours") ?? String(DEFAULT_WINDOW_HOURS),
    );
    if (
      !Number.isInteger(windowHours) ||
      windowHours < 1 ||
      windowHours > MAX_WINDOW_HOURS
    ) {
      return errorResponse(
        400,
        `window_hours must be an integer between 1 and ${MAX_WINDOW_HOURS}.`,
      );
    }

    const limit = Number(params.get("limit") ?? String(DEFAULT_LIMIT));
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      return errorResponse(
        400,
        `limit must be an integer between 1 and ${MAX_LIMIT}.`,
      );
    }

    const query = new URLSearchParams({
      window_hours: String(windowHours),
      limit: String(limit),
    });

    const activity = await skylizeFetch<BackendSecurityActivityResponse>(
      `/api/v1/security/activity?${query.toString()}`,
    );
    // Forwarded verbatim. The response is already narrow — counts plus the rows
    // behind them — and every field is a measurement, so there is nothing here
    // to project away and nothing to add.
    return NextResponse.json(activity);
  },
});
