// GET /api/console/audit -> backend GET /api/v1/audit, verbatim.
//
// WHAT THIS FEED IS NOT. The console's audit screen was designed around two
// columns the backend has never had, and forwarding is only safe because the
// browser side now renders neither as designed:
//
//   * ACTOR. There is no human-actor column in `audit_log`. The row carries
//     `source_agent_id`, which is an AGENT id or null (audit.py:36). Rendering
//     it under a heading that reads as a person would attribute an agent's
//     action to a human, which is precisely the claim a governance audit trail
//     exists to make impossible.
//   * SIGNATURE. `inputs_hash`/`outputs_hash` are SHA-256 CONTENT hashes of the
//     payloads (audit.py:8-10). A hash proves a payload has not been altered;
//     it proves nothing about WHO produced the row, which is what "signature"
//     asserts. Labelling a content hash as a signature would be a false
//     cryptographic claim in the one screen that must not make one.
//
// `before` is the keyset cursor from the previous page's `next_before`. The
// backend requires it to be timezone-aware and answers 422 otherwise
// (audit.py:53-54), so it is validated here rather than spent on a round trip.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendAuditListResponse } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const params = request.nextUrl.searchParams;
    const limit = Number(params.get("limit") ?? "50");
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      return errorResponse(400, "limit must be an integer between 1 and 200.");
    }

    const query = new URLSearchParams({ limit: String(limit) });
    const before = params.get("before");
    if (before !== null) {
      // Number.isNaN over an invalid Date is the only reliable parse check, and
      // the offset test is what enforces the backend's tz-aware requirement: a
      // bare "2026-01-01T00:00:00" parses fine but carries no zone.
      if (Number.isNaN(Date.parse(before))) {
        return errorResponse(400, "before must be an ISO 8601 timestamp.");
      }
      if (!/(Z|[+-]\d{2}:?\d{2})$/.test(before)) {
        return errorResponse(
          400,
          "before must be timezone-aware (end with Z or a UTC offset).",
        );
      }
      query.set("before", before);
    }

    const page = await skylizeFetch<BackendAuditListResponse>(
      `/api/v1/audit?${query.toString()}`,
    );
    return NextResponse.json(page);
  },
});
