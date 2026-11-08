// POST /api/my-day/session   — My Day login: email + password -> 204 + encrypted
//                               `skylize_myday` cookie.
// DELETE /api/my-day/session  — My Day logout: revoke the refresh token on the
//                               backend, then 204 + cleared cookie (502 and
//                               the cookie kept if revocation fails twice).
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

/** Backend logout attempts: the first plus exactly ONE retry. */
const LOGOUT_ATTEMPTS = 2;

/**
 * Revoke `refreshToken` server-side, retrying exactly once on any failure (the
 * backend logout is idempotent: 204 for any valid body). Resolves true on
 * success, false when both attempts failed. Never logs the token.
 */
async function revokeWithOneRetry(refreshToken: string): Promise<boolean> {
  for (let attempt = 1; attempt <= LOGOUT_ATTEMPTS; attempt += 1) {
    try {
      await mydayLogout(refreshToken);
      return true;
    } catch (error) {
      // The status and message carry no secret (MyDayApiError never echoes a token).
      const detail = {
        attempt,
        status: error instanceof MyDayApiError ? error.status : "unknown",
        message: error instanceof Error ? error.message : String(error),
      };
      if (attempt < LOGOUT_ATTEMPTS) {
        console.warn("[api/my-day] server-side logout failed; retrying once:", detail);
      } else {
        console.error(
          "[api/my-day] server-side logout failed; cookie kept so the client can retry:",
          detail,
        );
      }
    }
  }
  return false;
}

// Logout FAILS CLOSED (owner instruction, superseding the 2026-10-14 decision in
// myday_session_revocation_design.md 2.0.3 that the cookie was ALWAYS cleared
// and the answer ALWAYS 204). The refresh token is revoked server-side BEFORE
// the cookie is cleared:
//   * decryptable cookie -> mydayLogout, retried exactly once on failure.
//       success      -> 204 + cleared cookie;
//       both failed  -> 502 JSON error, cookie NOT cleared (the client can retry
//                       DELETE; clearing it would orphan a still-valid refresh
//                       token server-side), logged at error level, no token.
//   * no cookie, or a cookie that does not decrypt -> nothing to revoke:
//       204 + cleared cookie, no backend call (unchanged).
export const DELETE = mydayRoute({
  method: "DELETE",
  requireAuth: false,
  handler: async ({ request }) => {
    const refreshToken = await sealedRefreshToken(request);
    if (refreshToken !== null && !(await revokeWithOneRetry(refreshToken))) {
      return errorResponse(502, "Logout could not be completed. Please try again.");
    }
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(clearedMydaySessionCookie());
    return response;
  },
});
