// POST /api/my-day/session   — My Day login: email + password -> 204 + encrypted
//                               `skylize_myday` cookie.
// DELETE /api/my-day/session  — My Day logout (OD20): revoke the refresh token
//                               on the backend, then ALWAYS clear the cookie.
//                               204 = full logout; 200 + partial body = this
//                               device signed out, server session not ended.
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
import { MyDayConfigError, getMyDayConfig } from "@/lib/skylize/myday-config";
import {
  errorResponse,
  logMyDayConfigError,
  mydayRoute,
} from "@/lib/skylize/myday-handler";
import {
  MYDAY_LOGOUT_PARTIAL_TAG,
  MYDAY_PARTIAL_LOGOUT_BODY,
  MYDAY_PARTIAL_LOGOUT_STATUS,
} from "@/lib/skylize/myday-logout";
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

/** What the presented cookie yields for logout purposes. */
type SealedRefresh =
  | { kind: "none" } // no cookie, or it does not decrypt under a VALID config
  | { kind: "token"; refreshToken: string }
  | { kind: "config-error"; error: MyDayConfigError } // cannot even try to decrypt
  | { kind: "read-error"; errorName: string }; // reading the cookie threw unexpectedly

/**
 * The refresh token sealed inside the presented cookie. NEVER throws: logout
 * must work with a dead session, and OD20 requires the cookie to be cleared on
 * every path. Neither failure below is collapsed into "none": with a cookie
 * present, a live token may still exist server-side, so both are PARTIAL
 * logouts, never full ones.
 *   * config error (MyDayConfigError)        -> "config-error" (OD21 tag);
 *   * anything else thrown while reading it  -> "read-error" (only the error
 *     NAME is kept: a message could carry data, and the cookie never is).
 */
async function sealedRefreshToken(request: NextRequest): Promise<SealedRefresh> {
  const value = request.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value;
  if (!value) return { kind: "none" };
  try {
    const { cookieSecret } = getMyDayConfig();
    const session = await decryptSession(value, await deriveMyDayKeyMaterial(cookieSecret));
    return session === null
      ? { kind: "none" }
      : { kind: "token", refreshToken: session.refresh_token };
  } catch (error) {
    if (error instanceof MyDayConfigError) return { kind: "config-error", error };
    return { kind: "read-error", errorName: error instanceof Error ? error.name : typeof error };
  }
}

/** Backend logout attempts: the first plus exactly ONE retry. */
const LOGOUT_ATTEMPTS = 2;

type RevokeResult = { ok: true } | { ok: false; attempts: number; status: number | "unknown" };

/**
 * Revoke `refreshToken` server-side, retrying exactly once on any failure (the
 * backend logout is idempotent: 204 for any valid body). Logs only the attempt
 * number and status, never the token or any message text.
 */
async function revokeWithOneRetry(refreshToken: string): Promise<RevokeResult> {
  let status: number | "unknown" = "unknown";
  for (let attempt = 1; attempt <= LOGOUT_ATTEMPTS; attempt += 1) {
    try {
      await mydayLogout(refreshToken);
      return { ok: true };
    } catch (error) {
      status = error instanceof MyDayApiError ? error.status : "unknown";
      if (attempt < LOGOUT_ATTEMPTS) {
        console.warn("[api/my-day] server-side logout failed; retrying once:", {
          attempt,
          status,
        });
      }
    }
  }
  return { ok: false, attempts: LOGOUT_ATTEMPTS, status };
}

/** 204, empty body, cleared cookie: the server session is ended (or never existed). */
function fullLogoutResponse(): NextResponse {
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}

/** 200 + MYDAY_PARTIAL_LOGOUT_BODY, cleared cookie: this device only. */
function partialLogoutResponse(): NextResponse {
  const response = NextResponse.json(MYDAY_PARTIAL_LOGOUT_BODY, {
    status: MYDAY_PARTIAL_LOGOUT_STATUS,
  });
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}

// OD20 (owner ruling 2026-11-08): the cookie is ALWAYS cleared, even when the
// server-side revocation fails. This RESTORES the 2026-10-14 "always close"
// rule (myday_session_revocation_design.md 2.0.3) and SUPERSEDES the interim
// T2 behaviour (2b6bfbe: 502 and keep the cookie on failure), now with EXPLICIT
// reporting of which kind of logout happened (contract: myday-logout.ts):
//   * decryptable cookie -> mydayLogout, retried exactly once on failure.
//       success      -> FULL: 204 + cleared cookie;
//       both failed  -> PARTIAL: 200 + partial body + cleared cookie, logged at
//                       error level as `myday_logout_partial` (attempts +
//                       status only, never the token).
//   * cookie present but getMyDayConfig() throws -> revocation cannot even be
//       attempted -> PARTIAL, logged as `myday_config_error` (OD21).
//   * cookie present but reading it throws for any other reason -> PARTIAL,
//       logged as `myday_logout_partial` with reason "cookie-read-error" and
//       the error name only (never the cookie, a token, or the message).
//   * no cookie, or a cookie that does not decrypt under a valid config ->
//       nothing to revoke -> FULL: 204 + cleared cookie, no backend call.
export const DELETE = mydayRoute({
  method: "DELETE",
  requireAuth: false,
  handler: async ({ request }) => {
    const sealed = await sealedRefreshToken(request);
    if (sealed.kind === "none") return fullLogoutResponse();
    if (sealed.kind === "config-error") {
      logMyDayConfigError(sealed.error, "DELETE /api/my-day/session (logout partial)");
      return partialLogoutResponse();
    }
    if (sealed.kind === "read-error") {
      console.error(`[api/my-day] ${MYDAY_LOGOUT_PARTIAL_TAG}:`, {
        reason: "cookie-read-error",
        error: sealed.errorName,
      });
      return partialLogoutResponse();
    }
    const result = await revokeWithOneRetry(sealed.refreshToken);
    if (result.ok) return fullLogoutResponse();
    console.error(`[api/my-day] ${MYDAY_LOGOUT_PARTIAL_TAG}:`, {
      attempts: result.attempts,
      status: result.status,
    });
    return partialLogoutResponse();
  },
});
