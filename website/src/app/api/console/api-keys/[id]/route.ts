// DELETE /api/console/api-keys/{key_id} -> backend DELETE /api/v1/api-keys/{key_id}.
//
// The backend answers 204 with NO body on success and 404 for a key that is
// not in this org (api_keys.py `revoke_key`). `skylizeFetch` resolves a 204 as
// null rather than trying to parse it, so a completed revocation is not
// reported as a malformed body.
//
// Revocation is FINAL and is never auto-retried (client.ts `canRetry` is
// GET-only): retrying a DELETE that actually succeeded would come back 404 and
// turn a completed revocation into a reported failure.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";

export const DELETE = consoleRoute<undefined, { id: string }>({
  method: "DELETE",
  handler: async ({ params }) => {
    if (!z.uuid().safeParse(params.id).success) {
      return errorResponse(400, "Invalid key id — expected a UUID.");
    }
    await skylizeFetch<null>(`/api/v1/api-keys/${params.id}`, {
      method: "DELETE",
    });
    return new NextResponse(null, { status: 204 });
  },
});
