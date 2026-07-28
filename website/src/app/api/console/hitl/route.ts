// GET /api/console/hitl -> backend GET /api/v1/hitl, verbatim.
//
// Pending human-in-the-loop items for the service principal's org (org
// scoping is the backend's, from the authenticated key — never a parameter).
// limit/offset are validated to the backend's documented bounds and forwarded.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendHitlListResponse } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const params = request.nextUrl.searchParams;
    const limit = Number(params.get("limit") ?? "50");
    const offset = Number(params.get("offset") ?? "0");
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      return errorResponse(400, "limit must be an integer between 1 and 200.");
    }
    if (!Number.isInteger(offset) || offset < 0) {
      return errorResponse(400, "offset must be a non-negative integer.");
    }

    const items = await skylizeFetch<BackendHitlListResponse>(
      `/api/v1/hitl?limit=${limit}&offset=${offset}`,
    );
    return NextResponse.json(items);
  },
});
