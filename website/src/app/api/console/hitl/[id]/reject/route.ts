// POST /api/console/hitl/{id}/reject -> backend POST /api/v1/hitl/{id}/reject.
//
// Rejection records the human verdict and executes NOTHING. Distinguishing
// backend statuses pass through verbatim via the shared error mapping:
//   200 rejected (HitlRejectResponse body)
//   404 not found in this org
//   409 already actioned (detail names the existing status)
//   410 expired

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendHitlRejectResponse } from "@/lib/skylize/types";

// Mirrors the backend's HitlVerdictRequest (extra="forbid", note <= 2000).
const verdictSchema = z.strictObject({
  note: z.string().max(2000).optional(),
});

function hitlIdFromPath(pathname: string): string | null {
  const match = pathname.match(/\/api\/console\/hitl\/([^/]+)\/reject\/?$/);
  if (!match) return null;
  const candidate = decodeURIComponent(match[1]);
  return z.uuid().safeParse(candidate).success ? candidate : null;
}

export const POST = consoleRoute<z.infer<typeof verdictSchema>>({
  method: "POST",
  schema: verdictSchema,
  handler: async ({ request, body }) => {
    const hitlId = hitlIdFromPath(request.nextUrl.pathname);
    if (hitlId === null) {
      return errorResponse(400, "Invalid HITL id — expected a UUID.");
    }
    const result = await skylizeFetch<BackendHitlRejectResponse>(
      `/api/v1/hitl/${hitlId}/reject`,
      { method: "POST", body },
    );
    return NextResponse.json(result);
  },
});
