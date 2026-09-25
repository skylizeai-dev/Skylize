// GET /api/my-day/brief -> backend GET /api/v1/me/brief, as THIS user.
//
// The backend scopes the brief to `principal_id = ctx.user_id` and refuses to
// take a principal from the caller (edge/routes/brief.py:3-8, :81), so the only
// thing that decides whose brief this is, is which access token gets forwarded.
// That is exactly why this path forwards the user's own JWT rather than the
// service key: with the service key every employee would read the SAME journal.
//
// SINCE-LAST-SEEN DELTA, NOT A WINDOW. `WorkJournal.unseen`
// (app/principal/journal.py:106-121) returns entries with `seq >
// cursor.last_seen_seq`, and `POST /me/brief/seen` advances that cursor
// permanently. So this endpoint answers "what changed since you last looked",
// and anything acknowledged is gone from every later response. A caller must not
// treat it as a scrollable history.
//
// Forwarded verbatim. `done_while_away` will be EMPTY in any deployment that has
// not had an autonomous schedule installed — the write exists
// (app/autonomy/runner.py:365) but the runner is inert by construction
// (bootstrap.py:998-1001) until an operator installs one. That emptiness is the
// truth, and this route does not paper over it.

import { NextResponse } from "next/server";

import { mydayFetch } from "@/lib/myday/client";
import { mydayRoute } from "@/lib/myday/handler";

export const GET = mydayRoute({
  method: "GET",
  handler: async ({ accessToken }) => {
    const brief = await mydayFetch<unknown>("/api/v1/me/brief", accessToken);
    return NextResponse.json(brief.body);
  },
});
