// POST /api/console/notifications/{id}/read -> backend POST
// /api/v1/notifications/{id}/read.
//
// Idempotent acknowledgement, ORG-scoped: a second call on an already-read row
// is a no-op that returns the row with its ORIGINAL read_at, because the first
// acknowledgement is the one that happened (edge/routes/notifications.py). No
// request body — there is nothing to configure about "mark this read".
//
// 404 covers both "no such id" and "that id belongs to another org" — RLS makes
// the two indistinguishable at the backend, which is correct: confirming the
// existence of another tenant's row would itself be a cross-tenant leak.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendNotification } from "@/lib/skylize/types";

export const POST = consoleRoute<undefined, { id: string }>({
  method: "POST",
  handler: async ({ params }) => {
    if (!z.uuid().safeParse(params.id).success) {
      return errorResponse(400, "Invalid notification id — expected a UUID.");
    }
    const result = await skylizeFetch<BackendNotification>(
      `/api/v1/notifications/${params.id}/read`,
      { method: "POST" },
    );
    return NextResponse.json(result);
  },
});
