// POST /api/console/hitl/{id}/approve -> backend POST /api/v1/hitl/{id}/approve.
//
// Approval EXECUTES the deferred work, so the timeout allows a full LLM run.
// The distinguishing backend statuses pass through to the browser verbatim via
// the shared error mapping (they are all plain 4xx):
//   200 approved+executed (HitlApproveResponse body)
//   404 not found in this org
//   409 already actioned (detail names the existing status) / not replayable
//   410 expired
//   422 stored input failed re-validation against the CURRENT agent schema.
//       Under owner decision D4 this is a PERMANENT failure: the backend moved
//       the row to the terminal 'expired' status. It is NOT released to pending
//       and cannot be approved again.
//   502 execution failed after the claim (backend 502 -> mapped 502, message
//       kept). NOTE for the UI: 502 reaching the browser does NOT imply this
//       case — mapSkylizeError also returns 502 when the backend rejects the
//       server's service credential and for any backend 5xx, and skylizeFetch
//       synthesises 502 for an unreachable backend. Only the backend's own
//       HitlExecutionFailed 502 means the row was claimed and released.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendHitlApproveResponse } from "@/lib/skylize/types";

// Mirrors the backend's HitlVerdictRequest (extra="forbid", note <= 2000).
const verdictSchema = z.strictObject({
  note: z.string().max(2000).optional(),
});

const APPROVE_TIMEOUT_MS = 120_000;

/** consoleRoute passes only the request, so the dynamic segment is read from
 *  the pathname; anything that is not a UUID is refused before the backend. */
function hitlIdFromPath(pathname: string): string | null {
  const match = pathname.match(/\/api\/console\/hitl\/([^/]+)\/approve\/?$/);
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
    const result = await skylizeFetch<BackendHitlApproveResponse>(
      `/api/v1/hitl/${hitlId}/approve`,
      { method: "POST", body, timeoutMs: APPROVE_TIMEOUT_MS },
    );
    return NextResponse.json(result);
  },
});
