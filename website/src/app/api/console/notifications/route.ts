// GET /api/console/notifications -> backend GET /api/v1/notifications, verbatim.
//
// ORG-SCOPED, NOT PER-USER (migration 0034). Every row this forwards is
// addressed to the whole org's owner/admin readers, not to a named person —
// `read_at` means "somebody with access acknowledged this", not "this signed-in
// user did". The browser must not render it as a per-user inbox.
//
// WHAT CAN ACTUALLY APPEAR HERE. Only two `kind` values have a real producer:
// "hitl.approval_requested" and "governance.action_denied", both written by
// AgentExecutionService at the moment the governance gate already knows the
// fact (edge/routes/notifications.py carries the full account). A fresh org's
// list is legitimately EMPTY until one of those two things happens — this route
// must never be given a mock or seeded fallback to paper over that.
//
// `before` is the same keyset-cursor convention the audit route uses: the
// backend requires it to be timezone-aware and answers 422 otherwise, so it is
// validated here rather than spent on a round trip.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendNotificationListResponse } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const params = request.nextUrl.searchParams;
    const limit = Number(params.get("limit") ?? "50");
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      return errorResponse(400, "limit must be an integer between 1 and 200.");
    }

    const query = new URLSearchParams({ limit: String(limit) });

    const unreadOnly = params.get("unread_only");
    if (unreadOnly !== null) {
      if (unreadOnly !== "true" && unreadOnly !== "false") {
        return errorResponse(400, "unread_only must be 'true' or 'false'.");
      }
      query.set("unread_only", unreadOnly);
    }

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

    const page = await skylizeFetch<BackendNotificationListResponse>(
      `/api/v1/notifications?${query.toString()}`,
    );
    return NextResponse.json(page);
  },
});
