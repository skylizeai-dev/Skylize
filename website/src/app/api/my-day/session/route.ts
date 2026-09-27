// POST   /api/my-day/session — log in with real credentials -> 204 + sealed cookie
// DELETE /api/my-day/session — log out -> 204 + cleared cookie
//
// THIS IS A REAL PER-USER LOGIN, not the console's shared-password gate. It
// exchanges the employee's own email + password for their own Skylize token pair
// (`POST /api/v1/auth/login`) and seals that pair into the httpOnly
// `skylize_myday` cookie. Every later /api/my-day/* call forwards THAT user's
// access JWT, so the backend resolves their real org_id / user_id / roles.
//
// NEITHER THE PASSWORD NOR EITHER TOKEN IS EVER RETURNED TO THE BROWSER. The
// response is 204 with no body; the tokens exist only inside the AES-256-GCM
// seal, behind httpOnly, and in server memory for the duration of a request.
//
// This route does NOT use `mydayRoute`: that wrapper requires an existing
// session, and this endpoint is how one comes to exist. It therefore does its
// own method and body handling, and is the only file in this path that may run
// without a session.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { z } from "zod";

import { MyDayApiError, backendLogin } from "@/lib/myday/client";
import { getMyDaySessionConfig } from "@/lib/myday/config";
import { errorResponse } from "@/lib/myday/handler";
import {
  clearedMydaySessionCookie,
  mydaySessionCookie,
  readAccessTokenExpiryMs,
  sealSession,
} from "@/lib/myday/session";

const loginSchema = z.strictObject({
  email: z.string().email().max(320),
  password: z.string().min(1).max(128),
});

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    let raw: unknown;
    try {
      raw = await request.json();
    } catch {
      return errorResponse(400, "Request body must be valid JSON.");
    }

    const parsed = loginSchema.safeParse(raw);
    if (!parsed.success) {
      // Deliberately not itemised: a precise complaint about which field failed
      // on a login form is a small probing aid and buys the user nothing.
      return errorResponse(400, "Provide an email address and a password.");
    }

    const pair = await backendLogin(parsed.data.email, parsed.data.password);
    const { sessionSecret } = getMyDaySessionConfig();
    const sealed = await sealSession(
      {
        accessToken: pair.access_token,
        refreshToken: pair.refresh_token,
        accessExpiresAtMs: readAccessTokenExpiryMs(pair.access_token),
      },
      sessionSecret,
    );

    // 204, no body: nothing about the tokens reaches the client.
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(mydaySessionCookie(sealed));
    return response;
  } catch (error) {
    if (error instanceof MyDayApiError) {
      if (error.status === 401) {
        return errorResponse(401, "Invalid email or password.");
      }
      if (error.status === 429) {
        return errorResponse(429, "Too many attempts — try again shortly.");
      }
      return errorResponse(error.status >= 500 ? 502 : error.status, error.message);
    }
    console.error("[api/my-day/session] unhandled login error:", error);
    return errorResponse(500, "Internal server error.");
  }
}

export async function DELETE(): Promise<NextResponse> {
  // Clears the browser's copy. The backend refresh-token row stays revocable
  // independently (user_refresh_tokens, migration 0008) — a server-side
  // revocation-on-logout call is a separate, reviewable addition, deliberately
  // not improvised here.
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}
