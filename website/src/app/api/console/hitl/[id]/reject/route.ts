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

export const POST = consoleRoute<z.infer<typeof verdictSchema>, { id: string }>({
  method: "POST",
  schema: verdictSchema,
  handler: async ({ body, params }) => {
    if (!z.uuid().safeParse(params.id).success) {
      return errorResponse(400, "Invalid HITL id — expected a UUID.");
    }
    const result = await skylizeFetch<BackendHitlRejectResponse>(
      `/api/v1/hitl/${params.id}/reject`,
      { method: "POST", body },
    );
    return NextResponse.json(result);
  },
});
