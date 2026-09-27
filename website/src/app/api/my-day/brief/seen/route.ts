// POST /api/my-day/brief/seen -> backend POST /api/v1/me/brief/seen, as THIS user.
//
// ADVANCES THE CURSOR PERMANENTLY, and that is the whole reason this is an
// explicit action rather than something a render does. `WorkJournal.mark_seen`
// moves `journal_cursor.last_seen_seq` forward, and `unseen` only ever returns
// `seq > last_seen_seq` (app/principal/journal.py:106-121) — so everything up to
// `to_seq` is dropped from every future brief. A UI that called this on mount
// would silently eat a night of work and then show an empty brief.
//
// `to_seq` comes from the brief's own `head_seq`, so the caller acknowledges
// exactly what it rendered and nothing newer that may have landed since.

import { NextResponse } from "next/server";
import { z } from "zod";

import { mydayFetch } from "@/lib/myday/client";
import { mydayRoute } from "@/lib/myday/handler";

// Mirrors the backend's `MarkSeenRequest` (edge/routes/brief.py:69-72):
// one field, `to_seq`, non-negative.
const seenSchema = z.strictObject({
  to_seq: z.number().int().min(0),
});

export const POST = mydayRoute<z.infer<typeof seenSchema>>({
  method: "POST",
  schema: seenSchema,
  handler: async ({ accessToken, body }) => {
    await mydayFetch<null>("/api/v1/me/brief/seen", accessToken, {
      method: "POST",
      body,
    });
    // The backend answers 204; mirror that rather than inventing a body.
    return new NextResponse(null, { status: 204 });
  },
});
