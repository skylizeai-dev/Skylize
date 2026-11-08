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
// Refresh-on-401 (OD3-AMEND) is handled centrally, not here: the call goes
// through `withAccessToken` (myday-handler.ts, `createAccessTokenRunner`), which
// on a backend 401 refreshes the token pair once, re-seals the cookie, and
// retries this call once; any further 401 is a My Day 401 + cleared cookie.

import { NextResponse } from "next/server";

import { mydayGet } from "@/lib/skylize/myday-client";
import type { MyDayUser } from "@/lib/skylize/myday-client";
import { mydayRoute } from "@/lib/skylize/myday-handler";

export const GET = mydayRoute({
  method: "GET",
  handler: async ({ withAccessToken }) => {
    const user = await withAccessToken((accessToken) =>
      mydayGet<MyDayUser>("/api/v1/auth/me", accessToken),
    );
    return NextResponse.json(user);
  },
});
