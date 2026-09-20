// GET /api/console/workflows/runs -> backend GET /api/v1/workflows/runs, verbatim.
//
// A NEW path. This is NOT the existing `api/console/workflows/route.ts` — that
// file is dormant n8n admin plumbing gated by `SKYLIZE_ENABLE_N8N_ADMIN` (false)
// with an explicit note that it has no governance gate. It predates this route,
// is untouched by it, and is not workflow infrastructure in the sense this file
// is: this route forwards to the real Python backend's real run-history table
// (`workflow_runs`, migration 0033).
//
// WHAT THIS FEED IS NOT. `failure_stage` on a run is where it STOPPED, not how
// far it got — the live orchestrator records no per-stage progress, so this
// route (and any screen built on it) must not present these rows as a
// stage-by-stage pipeline. See `edge/routes/workflows.py`'s module docstring.
//
// `before` is the keyset cursor from the previous page's `next_before`, same
// contract as `api/console/audit/route.ts`: the backend requires it to be
// timezone-aware and answers 422 otherwise, so it is validated here rather than
// spent on a round trip.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendWorkflowRunListResponse } from "@/lib/skylize/types";

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

    const page = await skylizeFetch<BackendWorkflowRunListResponse>(
      `/api/v1/workflows/runs?${query.toString()}`,
    );
    return NextResponse.json(page);
  },
});
