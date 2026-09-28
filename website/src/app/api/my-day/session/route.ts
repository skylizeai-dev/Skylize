// POST /api/my-day/session   — My Day login: email + password -> 204 + encrypted
//                               `skylize_myday` cookie.
// DELETE /api/my-day/session  — My Day logout: 204 + cleared cookie.
//
// The My Day analogue of /api/console/session (console/session/route.ts), but:
//   * it authenticates a REAL user (email + password) against the backend's
//     human-user login, not the interim shared Console password; and
//   * the cookie it sets is AES-256-GCM ciphertext of the returned token pair,
//     not a signed expiry.
//
// Both methods are `requireAuth: false`: POST IS the login (no session yet), and
// DELETE must succeed even when the presented cookie is already invalid.
//
// Fail-closed by construction: this route reaches the backend ONLY through
// `mydayLogin` (myday-client.ts), which forwards no service key. It imports no
// Console module and no service-key-bearing module.

import { NextResponse } from "next/server";
import { z } from "zod";

import { MyDayApiError, mydayLogin } from "@/lib/skylize/myday-client";
import { getMyDayConfig } from "@/lib/skylize/myday-config";
import { errorResponse, mydayRoute } from "@/lib/skylize/myday-handler";
import {
  clearedMydaySessionCookie,
  deriveMyDayKeyMaterial,
  encryptSession,
  mydaySessionCookie,
} from "@/lib/skylize/myday-session";

// Strict object (reject unknown keys), with the same sensible bounds Console's
// login uses for its password field (console/session/route.ts:20-22). The email
// is validated as an email; the backend re-validates both (LoginRequest,
// auth.py:60-63), so this is a fast client-facing guard, not the sole check.
const loginSchema = z.strictObject({
  email: z.string().email().max(320),
  password: z.string().min(1).max(1024),
});

export const POST = mydayRoute<z.infer<typeof loginSchema>>({
  method: "POST",
  requireAuth: false,
  schema: loginSchema,
  handler: async ({ body }) => {
    let tokenPair;
    try {
      tokenPair = await mydayLogin(body.email, body.password);
    } catch (error) {
      // A backend 401 here is a bad-credentials rejection of the SUBMITTED
      // email/password (InvalidCredentialsError, auth.py:157-158) — surface it
      // as a clean 401 with a non-confirming message. Any other MyDayApiError
      // (5xx/network/timeout/rate-limit) is rethrown to `mydayRoute`'s uniform
      // mapping (mapMyDayError).
      if (error instanceof MyDayApiError && error.status === 401) {
        return errorResponse(401, "Invalid email or password.");
      }
      throw error;
    }

    const { cookieSecret } = getMyDayConfig();
    const keyMaterial = await deriveMyDayKeyMaterial(cookieSecret);
    const cookieValue = await encryptSession(tokenPair, keyMaterial);

    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(mydaySessionCookie(cookieValue));
    return response;
  },
});

export const DELETE = mydayRoute({
  method: "DELETE",
  requireAuth: false,
  handler: async () => {
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(clearedMydaySessionCookie());
    return response;
  },
});
