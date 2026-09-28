// GET /api/my-day/me — the authenticated per-user, per-org read: forward the My
// Day session's access JWT to the backend's GET /api/v1/auth/me and return the
// current user (auth.py:183-202). This is the read the isolation proof (design
// section 6.0) asserts against: org A's session must see only A's identity.
//
// `requireAuth: true` (the default), so `mydayRoute` has already decrypted the
// `skylize_myday` cookie and provides `session` — or returned 401 + cleared
// cookie before this handler ran. The backend request carries the USER'S OWN
// access token as Bearer (mydayGet), never a service key.
//
// Stage-1 scope: NO refresh-on-401. If the backend rejects the access token
// (expired/invalid), `mydayGet` throws MyDayApiError(401), which `mydayRoute`'s
// `mapMyDayError` turns into a My Day 401 + cleared cookie — a clean forced
// re-login, which the design says is the correct Stage 1 behaviour.

import { NextResponse } from "next/server";

import { mydayGet } from "@/lib/skylize/myday-client";
import type { MyDayUser } from "@/lib/skylize/myday-client";
import { errorResponse, mydayRoute } from "@/lib/skylize/myday-handler";

export const GET = mydayRoute({
  method: "GET",
  handler: async ({ session }) => {
    // Non-null under requireAuth: true (mydayRoute guarantees it); this guard
    // satisfies the type and would only ever fire on a wrapper contract change.
    if (session === null) {
      return errorResponse(401, "Authentication required.");
    }
    const user = await mydayGet<MyDayUser>(
      "/api/v1/auth/me",
      session.access_token,
    );
    return NextResponse.json(user);
  },
});
