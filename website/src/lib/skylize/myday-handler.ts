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
// any failure fail closed with 401 AND a cleared cookie. This mirrors Console,
// where the route handler is authoritative and the proxy is defense-in-depth
// (proxy.ts:11-13).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import type { ZodType } from "zod";

import { MyDayApiError } from "./myday-client";
import { getMyDayConfig } from "./myday-config";
import {
  MYDAY_SESSION_COOKIE_NAME,
  clearedMydaySessionCookie,
  decryptSession,
  deriveMyDayKeyMaterial,
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
   */
  session: MyDaySessionPayload | null;
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
 * Read and decrypt the `skylize_myday` cookie. Returns the session payload, or
 * `null` on any failure (absent cookie, config error, un-decryptable/expired/
 * tampered value). `decryptSession` never throws; a config error (missing env)
 * is caught here and also collapses to null so a fail-closed 401 results rather
 * than a 500 leaking configuration state.
 */
async function readMyDaySession(
  request: NextRequest,
): Promise<MyDaySessionPayload | null> {
  const cookieValue = request.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value;
  if (cookieValue === undefined || cookieValue === "") return null;
  try {
    const { cookieSecret } = getMyDayConfig();
    const keyMaterial = await deriveMyDayKeyMaterial(cookieSecret);
    return await decryptSession(cookieValue, keyMaterial);
  } catch {
    return null;
  }
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
    try {
      if (request.method !== method) {
        return errorResponse(405, "Method not allowed.");
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
      return await handler({ request, body, params, session });
    } catch (error) {
      if (error instanceof MyDayApiError) {
        return mapMyDayError(error);
      }
      // Config errors and other unexpected failures: loud in server logs (which
      // never contain the bearer token — MyDayApiError carries no secret and we
      // log the raw error, not request headers), opaque to the browser.
      console.error("[api/my-day] unhandled route error:", error);
      return errorResponse(500, "Internal server error.");
    }
  };
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
