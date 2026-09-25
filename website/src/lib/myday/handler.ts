// Shared route helper for every /api/my-day/* handler: method guard, session
// resolution, transparent token refresh, and a uniform error envelope.
//
// FAIL CLOSED, ON EVERY PATH. There are exactly four ways a request can fail to
// produce a usable user identity, and all four end the same way — 401 plus a
// CLEARED cookie, so the browser is pushed back to login:
//
//   1. no cookie present
//   2. cookie present but will not open (tampered, wrong secret, malformed)
//   3. access token expired AND the refresh attempt failed
//   4. the backend itself answered 401 even after a fresh refresh
//
// NONE of those paths substitutes another credential, and none can: this module
// imports no service key and lib/myday/client.ts holds none. That is the
// property the design calls "impossible by construction" — the wrong-identity
// bug this whole path exists to fix cannot be silently reintroduced by a
// fallback, because there is nothing to fall back to.
//
// SEPARATE FROM THE CONSOLE HELPER on purpose. `lib/skylize/handler.ts` maps a
// backend 401 to 502 ("the SERVICE credential was rejected" — an infrastructure
// fault the user cannot fix). Here a 401 means "THIS USER's session is over",
// which is a 401 the login screen must act on. Sharing one mapper would make one
// of the two cases lie.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import type { ZodType } from "zod";

import { MyDayApiError, backendRefresh } from "./client";
import { getMyDaySessionConfig } from "./config";
import {
  MYDAY_SESSION_COOKIE_NAME,
  type MyDaySession,
  clearedMydaySessionCookie,
  mydaySessionCookie,
  openSession,
  readAccessTokenExpiryMs,
  sealSession,
} from "./session";

type HttpMethod = "GET" | "POST" | "DELETE";

/**
 * Refresh this far before the access token actually expires, so a request that
 * spends a few seconds in flight cannot arrive at the backend already stale.
 * The access TTL is 30 minutes (config.py:110), so this costs one extra refresh
 * per half hour of continuous use at most.
 */
const REFRESH_SKEW_MS = 60_000;

export interface MyDayRequestContext<TBody> {
  request: NextRequest;
  /** Zod-validated body when a schema was given; undefined otherwise. */
  body: TBody;
  /**
   * The caller's OWN access JWT, already refreshed if it was near expiry. Pass
   * this to `mydayFetch` — it is the only credential this path has.
   */
  accessToken: string;
}

export interface MyDayRouteOptions<TBody> {
  method: HttpMethod;
  schema?: ZodType<TBody>;
  handler: (context: MyDayRequestContext<TBody>) => Promise<NextResponse>;
}

/** Uniform error envelope, matching the console's `{ error }` shape. */
export function errorResponse(status: number, message: string): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

/**
 * 401 with the cookie cleared. The single exit used by every failure mode above,
 * so a dead session can never linger in the browser and be retried forever.
 */
function unauthenticated(message: string): NextResponse {
  const response = errorResponse(401, message);
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}

interface ResolvedSession {
  session: MyDaySession;
  /** Set when a refresh produced a new pair that must be written back. */
  reseal: boolean;
}

/**
 * Open the cookie and, if the access token is at or near expiry, spend the
 * refresh token for a new pair.
 *
 * Returns null when no usable identity could be established — the caller turns
 * that into `unauthenticated(...)`. A refresh failure is TERMINAL rather than
 * retried: the backend revokes the consumed refresh token before minting the
 * replacement (user_service.py:200-206), so a failed refresh means the old token
 * is already spent and the session genuinely cannot continue.
 */
async function resolveSession(request: NextRequest): Promise<ResolvedSession | null> {
  const sealed = request.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value;
  if (!sealed) return null;

  const { sessionSecret } = getMyDaySessionConfig();
  const session = await openSession(sealed, sessionSecret);
  if (session === null) return null;

  if (session.accessExpiresAtMs - REFRESH_SKEW_MS > Date.now()) {
    return { session, reseal: false };
  }

  try {
    const pair = await backendRefresh(session.refreshToken);
    return {
      session: {
        accessToken: pair.access_token,
        refreshToken: pair.refresh_token,
        accessExpiresAtMs: readAccessTokenExpiryMs(pair.access_token),
      },
      reseal: true,
    };
  } catch {
    // Includes a 401 from /auth/refresh (token revoked, expired, or user
    // deactivated). No fallback exists and none is attempted.
    return null;
  }
}

export function mydayRoute<TBody = undefined>(
  options: MyDayRouteOptions<TBody>,
): (request: NextRequest) => Promise<NextResponse> {
  const { method, schema, handler } = options;

  return async function route(request: NextRequest): Promise<NextResponse> {
    try {
      if (request.method !== method) {
        return errorResponse(405, "Method not allowed.");
      }

      const resolved = await resolveSession(request);
      if (resolved === null) {
        return unauthenticated("Sign in to see your day.");
      }

      let body = undefined as TBody;
      if (schema) {
        let raw: unknown;
        try {
          raw = await request.json();
        } catch {
          return errorResponse(400, "Request body must be valid JSON.");
        }
        const parsed = schema.safeParse(raw);
        if (!parsed.success) {
          const issues = parsed.error.issues
            .map((issue) => {
              const at = issue.path.map(String).join(".");
              return at ? `${at}: ${issue.message}` : issue.message;
            })
            .join("; ");
          return errorResponse(400, `Invalid request body — ${issues}`);
        }
        body = parsed.data;
      }

      let response: NextResponse;
      try {
        response = await handler({
          request,
          body,
          accessToken: resolved.session.accessToken,
        });
      } catch (error) {
        if (error instanceof MyDayApiError && error.status === 401) {
          // The backend rejected a token we believed was live — treat the
          // session as over rather than retrying with anything else.
          return unauthenticated("Your session ended. Sign in again.");
        }
        throw error;
      }

      // Persist a rotated pair onto the successful response. Done here, once,
      // so no individual route has to remember to re-seal.
      if (resolved.reseal) {
        const { sessionSecret } = getMyDaySessionConfig();
        response.cookies.set(
          mydaySessionCookie(await sealSession(resolved.session, sessionSecret)),
        );
      }

      return response;
    } catch (error) {
      if (error instanceof MyDayApiError) {
        if (error.status === 429) {
          return errorResponse(429, "Rate limited — try again shortly.");
        }
        if (error.status === 504) {
          return errorResponse(504, error.message);
        }
        if (error.status >= 500 || error.status < 400) {
          return errorResponse(502, error.message);
        }
        // Meaningful 4xx (403/404/409/422) pass through with the backend's own
        // detail — e.g. a 403 from `/cowork/turns` for a role that lacks it.
        return errorResponse(error.status, error.message);
      }
      // Config errors and unexpected failures: loud server-side (they never
      // contain the token), opaque to the browser.
      console.error("[api/my-day] unhandled route error:", error);
      return errorResponse(500, "Internal server error.");
    }
  };
}
