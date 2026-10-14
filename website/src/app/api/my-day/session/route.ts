// POST /api/my-day/session   — My Day login: email + password -> 204 + encrypted
//                               `skylize_myday` cookie.
// DELETE /api/my-day/session  — My Day logout: revoke the refresh token on the
//                               backend, then 204 + cleared cookie.
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
// `mydayLogin` and `mydayLogout` (myday-client.ts), which forward no service
// key. It imports no Console module and no service-key-bearing module.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { z } from "zod";

import { MyDayApiError, mydayLogin, mydayLogout } from "@/lib/skylize/myday-client";
import { getMyDayConfig } from "@/lib/skylize/myday-config";
import { errorResponse, mydayRoute } from "@/lib/skylize/myday-handler";
import {
  MYDAY_SESSION_COOKIE_NAME,
  clearedMydaySessionCookie,
  decryptSession,
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

/**
 * The refresh token sealed inside the presented cookie, or null when there is no
 * cookie or it does not decrypt (absent, tampered, wrong key, config missing).
 * Never throws: logout must work with a dead session.
 */
async function sealedRefreshToken(request: NextRequest): Promise<string | null> {
  const value = request.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value;
  if (!value) return null;
  try {
    const { cookieSecret } = getMyDayConfig();
    const session = await decryptSession(value, await deriveMyDayKeyMaterial(cookieSecret));
    return session?.refresh_token ?? null;
  } catch {
    return null;
  }
}

// Owner decision 2026-10-14 (myday_session_revocation_design.md, 2.0.3): the
// cookie is ALWAYS cleared and the answer is ALWAYS 204, even when the backend
// revocation fails. A failure is logged at error level, never swallowed. The
// trade-off: that refresh token then stays valid server-side until it expires,
// a risk bounded by the cookie being gone from this browser.
export const DELETE = mydayRoute({
  method: "DELETE",
  requireAuth: false,
  handler: async ({ request }) => {
    const refreshToken = await sealedRefreshToken(request);
    if (refreshToken !== null) {
      try {
        await mydayLogout(refreshToken);
      } catch (error) {
        // Never log the token; the status and message carry no secret.
        const status = error instanceof MyDayApiError ? error.status : "unknown";
        console.error(
          "[api/my-day] server-side logout failed; refresh token stays valid until expiry:",
          { status, message: error instanceof Error ? error.message : String(error) },
        );
      }
    }
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(clearedMydaySessionCookie());
    return response;
  },
});
