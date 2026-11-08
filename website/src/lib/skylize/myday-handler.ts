// Shared route helper for EVERY /api/my-day/* handler: method guard, My Day
// session requirement, zod body validation, and uniform error mapping — the
// My Day analogue of `handler.ts`'s `consoleRoute` (handler.ts:102-159).
//
// It is a DELIBERATE, isolated copy of that shape, not a reuse of it. It MUST
// NOT import `handler.ts`, `client.ts`, or `getBackendConfig`/`getConsoleAuthConfig`
// from `config.ts`: `handler.ts` imports `getConsoleAuthConfig` from `config.ts`
// and `SkylizeApiError`/`BackendErrorCode` from `client.ts`, so importing it
// would transitively pull the service-key-bearing modules into the My Day import
// graph and break the fail-closed guarantee (design section 5.0). This file's
// entire reachable import set is the myday-* modules plus next/server + zod.
//
// The authoritative My Day auth check is HERE (and re-run in the edge gate below
// only as defense-in-depth): read the `skylize_myday` cookie, decrypt it, and on
// any failure fail closed with 401 AND a cleared cookie — except a My Day
// CONFIG error, which is an operator fault: logged as `myday_config_error`,
// answered 500, cookie left alone (OD21). This mirrors Console,
// where the route handler is authoritative and the proxy is defense-in-depth
// (proxy.ts:11-13).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import type { ZodType } from "zod";

import { MyDayApiError, mydayRefresh } from "./myday-client";
import { MYDAY_CONFIG_ERROR_TAG, MyDayConfigError, getMyDayConfig } from "./myday-config";
import { isStateChangingMethod, verifySameOrigin } from "./myday-csrf";
import {
  MYDAY_SESSION_COOKIE_NAME,
  clearedMydaySessionCookie,
  decryptSession,
  deriveMyDayKeyMaterial,
  encryptSession,
  mydaySessionCookie,
} from "./myday-session";
import type { MyDaySessionPayload } from "./myday-session";

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

/**
 * The second argument Next.js passes to a dynamic route handler: `params` is a
 * PROMISE resolving to the dynamic segments (route.md, "context (optional)").
 * Optional here because static routes are declared with the same helper.
 */
export interface MyDayRouteContext<TParams> {
  params: Promise<TParams>;
}

export interface MyDayRequestContext<TBody, TParams = Record<string, never>> {
  request: NextRequest;
  /** Zod-validated body when a schema was given; undefined otherwise. */
  body: TBody;
  /** Framework-supplied dynamic segments, already awaited. Empty for static routes. */
  params: TParams;
  /**
   * The decrypted My Day session. `mydayRoute` guarantees it is NON-NULL when
   * `requireAuth` is true (the default) — the wrapper returns 401 before calling
   * the handler otherwise — so an authenticated handler can read
   * `session.access_token` directly. It is `null` only on the login/logout
   * route, which opts out of auth (`requireAuth: false`).
   *
   * Do NOT pass `session.access_token` to the backend directly: use
   * `withAccessToken`, which owns refresh-on-401.
   */
  session: MyDaySessionPayload | null;
  /**
   * Run ONE backend call with the session's access token, transparently
   * refreshing once on a backend 401 (OD3-AMEND; see `createAccessTokenRunner`).
   * Every authenticated backend call a handler makes MUST go through this so the
   * refresh logic lives in exactly one place. Throws on an unauthenticated route.
   */
  withAccessToken: <T>(call: (accessToken: string) => Promise<T>) => Promise<T>;
}

export interface MyDayRouteOptions<TBody, TParams = Record<string, never>> {
  method: HttpMethod;
  /** When present, the JSON body is parsed and validated before the handler runs. */
  schema?: ZodType<TBody>;
  /** Default true. Only the login/logout endpoint (POST/DELETE session) opts out. */
  requireAuth?: boolean;
  handler: (
    context: MyDayRequestContext<TBody, TParams>,
  ) => Promise<NextResponse>;
}

/**
 * Uniform error envelope for the My Day path: NextResponse.json({ error }, { status }).
 *
 * This is My Day's OWN error responder — it does NOT import handler.ts's
 * `errorResponse` (that would pull `client.ts` into the graph). It intentionally
 * omits the optional `code` field: the My Day surface has no backend error-code
 * contract to forward, so the body is always just `{ error }`.
 */
export function errorResponse(status: number, message: string): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

/** The 401 + cleared-cookie response used on every fail-closed session outcome. */
function unauthenticatedResponse(): NextResponse {
  const response = errorResponse(401, "Authentication required.");
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}

/**
 * Log a My Day configuration error under the greppable MYDAY_CONFIG_ERROR_TAG
 * (OD21). Only the message is logged: every MyDayConfigError message names
 * variables, never values (myday-config.ts), so no secret reaches the log.
 */
export function logMyDayConfigError(error: MyDayConfigError, during: string): void {
  console.error(`[api/my-day] ${MYDAY_CONFIG_ERROR_TAG}:`, {
    during,
    message: error.message,
  });
}

/** The opaque 500 for a config error. It does NOT touch the user's cookie. */
function configErrorResponse(error: MyDayConfigError, during: string): NextResponse {
  logMyDayConfigError(error, during);
  return errorResponse(500, "Internal server error.");
}

/**
 * Read and decrypt the `skylize_myday` cookie. Returns the session payload, or
 * `null` when the cookie is absent or does not decrypt (tampered, wrong key,
 * malformed): that is a USER-side session failure, answered 401 + clear, and
 * deliberately not logged.
 *
 * A CONFIG error is NOT collapsed into null (OD21): `getMyDayConfig` throws a
 * MyDayConfigError, which propagates to `mydayRoute`, is logged under
 * MYDAY_CONFIG_ERROR_TAG and answered 500 WITHOUT clearing the cookie. An
 * operator misconfiguration must neither be silent nor log everyone out.
 * `decryptSession` itself never throws.
 */
async function readMyDaySession(
  request: NextRequest,
): Promise<MyDaySessionPayload | null> {
  const cookieValue = request.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value;
  if (cookieValue === undefined || cookieValue === "") return null;
  const { cookieSecret } = getMyDayConfig(); // throws MyDayConfigError: not caught here
  const keyMaterial = await deriveMyDayKeyMaterial(cookieSecret);
  return decryptSession(cookieValue, keyMaterial);
}

/**
 * Translate a My Day backend failure into the uniform error envelope.
 *
 * CONTRAST WITH CONSOLE (handler.ts:78-100, `mapSkylizeError`): the Console BFF
 * forwards the shared SERVICE key, so a backend 401/403 there means "the
 * server's service credential was rejected" and must surface as 502 — NEVER a
 * console 401, which the UI reads strictly as "session expired, go to login".
 *
 * My Day is the OPPOSITE. It forwards the USER'S OWN access JWT
 * (myday-client.ts). A backend 401/403 therefore genuinely means "this user's
 * token is expired/invalid" — i.e. the session really has expired — so it maps
 * to a My Day 401 AND clears the cookie, forcing a clean re-login. It is NOT a
 * 502. Backend 5xx / network failures / timeouts (502/504 from MyDayApiError)
 * are upstream failures and pass through as-is; other 4xx (400/404/409/422) are
 * meaningful to the caller and pass through unchanged.
 */
function mapMyDayError(error: MyDayApiError): NextResponse {
  if (error.status === 401 || error.status === 403) {
    return unauthenticatedResponse();
  }
  if (error.status === 429) {
    return errorResponse(429, "Backend rate limit exceeded — try again shortly.");
  }
  if (error.status === 504) {
    return errorResponse(504, error.message);
  }
  if (error.status >= 500 || error.status < 400) {
    return errorResponse(502, error.message);
  }
  // Remaining 4xx (400/404/409/422) are meaningful to the caller as-is.
  return errorResponse(error.status, error.message);
}

// ── refresh-on-401 (OD3-AMEND) ──────────────────────────────────────────────
//
// The backend access token is short-lived (its TTL is being lowered to 15 min);
// the refresh token in the same cookie lives 14 days. So a backend 401 on the
// ACCESS token is the normal "access expired" signal, not the end of the
// session. For authenticated routes the rule, applied per request, is:
//
//   1. backend call -> 401: call mydayRefresh(refresh_token) EXACTLY ONCE.
//      (mydayRefresh is a POST; myday-client.ts never auto-retries a POST, so
//      the refresh token is presented at most once per request.)
//   2. refresh OK: re-seal the cookie with the NEW pair. The old refresh token
//      is now consumed server-side, so the new pair is set on the outgoing
//      response WHATEVER that response is (200, 404, 500, ...) — dropping it
//      would log the user out. Then retry the original call ONCE with the new
//      access token.
//   3. retry -> 401 again: 401 + cleared cookie. No second refresh, no loop.
//   4. refresh fails: backend 401 -> 401 + cleared cookie; any other failure
//      (5xx, network, timeout, malformed body) -> 502 + cleared cookie. Only
//      the backend STATUS is inspected, never its `detail` text.
//
// Only 401 triggers a refresh. A 403 (valid token, not permitted) and every
// other status go straight to `mapMyDayError` unchanged.
//
// KNOWN RACE (documented, deliberately NOT solved): two tabs whose access token
// expired at the same moment both get a 401 and both refresh with the SAME old
// refresh token. The backend rotates it for the first (the winner, whose
// response sets the new cookie); the second (the loser) gets a refresh 401 and
// its response CLEARS the cookie — possibly after the winner's response set it.
// The user is then forced to re-login. This fails CLOSED (no token is reused,
// no session is extended), so it is accepted rather than papered over.

/** Thrown inside a handler when the session must end (refresh failed/rejected). */
class MyDaySessionEndedError extends Error {
  readonly status: 401 | 502;

  constructor(status: 401 | 502) {
    super(status === 401 ? "My Day session ended." : "My Day session refresh failed upstream.");
    this.name = "MyDaySessionEndedError";
    this.status = status;
  }
}

/** Per-request refresh bookkeeping read by `applySessionOutcome`. */
interface SessionOutcome {
  /** Set when the session must end: the response becomes this status + cleared cookie. */
  ended: 401 | 502 | null;
  /** Sealed cookie value for the NEW pair after a successful refresh. */
  resealedCookie: string | null;
}

function sessionEndedResponse(status: 401 | 502): NextResponse {
  if (status === 401) return unauthenticatedResponse();
  const response = errorResponse(502, "Session refresh failed upstream — sign in again.");
  response.cookies.set(clearedMydaySessionCookie());
  return response;
}

/**
 * Build the `withAccessToken` runner for one request. All refresh-on-401 logic
 * lives here; route handlers only ever say "run this call with the token".
 */
function createAccessTokenRunner(
  session: MyDaySessionPayload,
  outcome: SessionOutcome,
): <T>(call: (accessToken: string) => Promise<T>) => Promise<T> {
  let accessToken = session.access_token;
  let refreshAttempted = false;

  return async function withAccessToken<T>(
    call: (token: string) => Promise<T>,
  ): Promise<T> {
    if (outcome.ended !== null) throw new MyDaySessionEndedError(outcome.ended);
    try {
      return await call(accessToken);
    } catch (error) {
      if (!(error instanceof MyDayApiError) || error.status !== 401) throw error;
      if (refreshAttempted) {
        // Already refreshed once in this request: never refresh again.
        outcome.ended = 401;
        throw new MyDaySessionEndedError(401);
      }
    }

    refreshAttempted = true;
    let pair;
    try {
      pair = await mydayRefresh(session.refresh_token);
    } catch (error) {
      const rejected = error instanceof MyDayApiError && error.status === 401;
      outcome.ended = rejected ? 401 : 502;
      if (!rejected) {
        // Never log a token: MyDayApiError carries only a status and message.
        console.error("[api/my-day] session refresh failed upstream:", {
          status: error instanceof MyDayApiError ? error.status : "unknown",
          message: error instanceof Error ? error.message : String(error),
        });
      }
      throw new MyDaySessionEndedError(outcome.ended);
    }

    // Seal immediately so that whatever happens next, the response carries it.
    const { cookieSecret } = getMyDayConfig();
    outcome.resealedCookie = await encryptSession(
      pair,
      await deriveMyDayKeyMaterial(cookieSecret),
    );
    accessToken = pair.access_token;

    try {
      return await call(accessToken);
    } catch (error) {
      if (error instanceof MyDayApiError && error.status === 401) {
        outcome.ended = 401;
        throw new MyDaySessionEndedError(401);
      }
      throw error; // any other failure: the re-sealed pair still rides along
    }
  };
}

/** Throwing stand-in for unauthenticated routes, which have no token to use. */
async function noAccessToken<T>(): Promise<T> {
  throw new Error("withAccessToken used on a requireAuth:false My Day route.");
}

/**
 * Apply the request's refresh outcome to the final response. An ended session
 * REPLACES the response (the handler cannot override it, even by swallowing the
 * error). A re-sealed pair is set on whatever response goes out — unless that
 * response already sets the session cookie explicitly (e.g. mapMyDayError's
 * 403 -> 401 + clear), in which case that explicit decision stands.
 */
function applySessionOutcome(
  response: NextResponse,
  outcome: SessionOutcome,
): NextResponse {
  if (outcome.ended !== null) return sessionEndedResponse(outcome.ended);
  if (
    outcome.resealedCookie !== null &&
    response.cookies.get(MYDAY_SESSION_COOKIE_NAME) === undefined
  ) {
    response.cookies.set(mydaySessionCookie(outcome.resealedCookie));
  }
  return response;
}

export function mydayRoute<TBody = undefined, TParams = Record<string, never>>(
  options: MyDayRouteOptions<TBody, TParams>,
): (
  request: NextRequest,
  context?: MyDayRouteContext<TParams>,
) => Promise<NextResponse> {
  const { method, schema, requireAuth = true, handler } = options;

  return async function route(
    request: NextRequest,
    context?: MyDayRouteContext<TParams>,
  ): Promise<NextResponse> {
    const outcome: SessionOutcome = { ended: null, resealedCookie: null };
    let response: NextResponse;
    try {
      response = await dispatch(request, context, outcome);
    } catch (error) {
      if (error instanceof MyDaySessionEndedError) {
        response = sessionEndedResponse(error.status);
      } else if (error instanceof MyDayConfigError) {
        // OD21: logged under its own tag, 500, cookie untouched. Covers the
        // session read here AND the login path (myday-client.ts myDayFetch).
        response = configErrorResponse(error, `${request.method} ${request.nextUrl.pathname}`);
      } else if (error instanceof MyDayApiError) {
        response = mapMyDayError(error);
      } else {
        // Config errors and other unexpected failures: loud in server logs
        // (which never contain the bearer token — MyDayApiError carries no
        // secret and we log the raw error, not request headers), opaque to the
        // browser.
        console.error("[api/my-day] unhandled route error:", error);
        response = errorResponse(500, "Internal server error.");
      }
    }
    return applySessionOutcome(response, outcome);
  };

  async function dispatch(
    request: NextRequest,
    context: MyDayRouteContext<TParams> | undefined,
    outcome: SessionOutcome,
  ): Promise<NextResponse> {
    if (request.method !== method) {
      return errorResponse(405, "Method not allowed.");
    }

    // Cross-site guard (myday-csrf.ts): every state-changing request must come
    // from this site's own origin, checked BEFORE the session or the body is
    // read. The 403 deliberately does NOT clear the cookie: if it did, any
    // cross-site page could log a victim out by POSTing here.
    if (isStateChangingMethod(request.method)) {
      const verdict = verifySameOrigin(request);
      if (!verdict.ok) {
        console.warn("[api/my-day] cross-site request refused:", verdict.reason);
        return errorResponse(403, "Cross-site request refused.");
      }
    }

    // Fail-closed session lifecycle (design section 5.0): an absent, invalid,
    // tampered, or expired cookie yields 401 AND a cleared cookie. Only the
    // login/logout route (requireAuth:false) skips this.
    let session: MyDaySessionPayload | null = null;
    if (requireAuth) {
      session = await readMyDaySession(request);
      if (session === null) {
        return unauthenticatedResponse();
      }
    }

    // Awaited once, here, so handlers see plain values.
    const params = ((await context?.params) ?? {}) as TParams;

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

    // `session` is non-null whenever requireAuth is true (guarded above); the
    // handler for an authenticated route can narrow it once or read directly.
    const withAccessToken =
      session === null ? noAccessToken : createAccessTokenRunner(session, outcome);
    return await handler({ request, body, params, session, withAccessToken });
  }
}

// ── edge gate (defense-in-depth only) ───────────────────────────────────────

/**
 * Identity-ish headers a client must never smuggle past the edge — the same set
 * the Console gate strips (proxy-gate.ts:19). Duplicated here rather than
 * imported so the My Day edge path never reaches proxy-gate.ts (and through it
 * session.ts / the Console secret).
 */
const SPOOFABLE_HEADERS = ["x-skylize-user", "x-skylize-org"] as const;

/**
 * The My Day edge gate invoked from `proxy.ts` for `/api/my-day/*` requests.
 *
 * My Day route handlers are self-guarding: `mydayRoute` performs the
 * AUTHORITATIVE cookie decrypt + 401 + clear. So — exactly as Console's proxy is
 * only defense-in-depth over its self-verifying handlers (proxy.ts:11-13) — the
 * correct My Day edge behaviour is minimal: strip spoofable identity headers and
 * pass the request through. It does NOT check the cookie or redirect (My Day has
 * no login *page* in Stage 1, and the API routes return their own 401), and it
 * deliberately does NOT run `consoleProxyGate`, which would apply the WRONG
 * (Console) cookie check and 401 every My Day request.
 *
 * Web-Crypto/edge-safe: it touches no Node APIs and reads no env.
 */
export function mydayProxyGate(request: NextRequest): NextResponse {
  const headers = new Headers(request.headers);
  for (const name of SPOOFABLE_HEADERS) headers.delete(name);
  return NextResponse.next({ request: { headers } });
}
