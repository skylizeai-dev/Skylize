// Shared route helper for EVERY /api/console/* handler: method guard, session
// requirement, zod body validation, and uniform error mapping. Routes contain
// only their business logic; the guard class lives here exactly once.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import type { ZodType } from "zod";

import { SkylizeApiError } from "./client";
import { getConsoleAuthConfig } from "./config";
import { SESSION_COOKIE_NAME, verifySessionToken } from "./session";

type HttpMethod = "GET" | "POST" | "DELETE";

/**
 * The second argument Next.js passes to a dynamic route handler.
 *
 * Shape per this version's own docs (node_modules/next/dist/docs/01-app/
 * 03-api-reference/03-file-conventions/route.md, "context (optional)"):
 * `params` is a PROMISE resolving to the dynamic segments. It is optional here
 * because static routes are declared with the same helper.
 */
export interface ConsoleRouteContext<TParams> {
  params: Promise<TParams>;
}

export interface ConsoleRequestContext<TBody, TParams = Record<string, never>> {
  request: NextRequest;
  /** Zod-validated body when a schema was given; undefined otherwise. */
  body: TBody;
  /**
   * The framework-supplied dynamic segments, already awaited. `consoleRoute`
   * used to drop the route-context argument entirely, which forced each dynamic
   * route to re-derive its own `[id]` from `request.nextUrl.pathname` with a
   * bespoke regex — three separate regexes that had to stay in step with three
   * folder paths. Empty object for a static route.
   */
  params: TParams;
}

export interface ConsoleRouteOptions<TBody, TParams = Record<string, never>> {
  method: HttpMethod;
  /** When present, the JSON body is parsed and validated before the handler runs. */
  schema?: ZodType<TBody>;
  /** Default true. Only the login/logout endpoint opts out. */
  requireAuth?: boolean;
  handler: (
    context: ConsoleRequestContext<TBody, TParams>,
  ) => Promise<NextResponse>;
}

/** Uniform error envelope: NextResponse.json({ error }, { status }). */
export function errorResponse(status: number, message: string): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

async function hasValidSession(request: NextRequest): Promise<boolean> {
  const token = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  if (!token) return false;
  const { cookieSecret } = getConsoleAuthConfig();
  return verifySessionToken(token, cookieSecret);
}

/**
 * Translate a backend failure into the console's uniform error envelope.
 * A backend 401/403 means OUR service credential was rejected — that must
 * surface as an upstream failure (502), never as a console 401, because the
 * UI treats 401 strictly as "session expired, go to login".
 */
function mapSkylizeError(error: SkylizeApiError): NextResponse {
  if (error.status === 401 || error.status === 403) {
    return errorResponse(502, "The backend rejected the server's service credential.");
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
  // Remaining 4xx (400/404/409/422) are meaningful to the console as-is,
  // e.g. 404 "tenant not registered".
  return errorResponse(error.status, error.message);
}

export function consoleRoute<TBody = undefined, TParams = Record<string, never>>(
  options: ConsoleRouteOptions<TBody, TParams>,
): (
  request: NextRequest,
  context?: ConsoleRouteContext<TParams>,
) => Promise<NextResponse> {
  const { method, schema, requireAuth = true, handler } = options;

  return async function route(
    request: NextRequest,
    context?: ConsoleRouteContext<TParams>,
  ): Promise<NextResponse> {
    try {
      if (request.method !== method) {
        return errorResponse(405, "Method not allowed.");
      }

      if (requireAuth && !(await hasValidSession(request))) {
        return errorResponse(401, "Authentication required.");
      }

      // Awaited once, here, so handlers see plain values. Static routes get an
      // empty object rather than a rejected access.
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

      return await handler({ request, body, params });
    } catch (error) {
      if (error instanceof SkylizeApiError) {
        return mapSkylizeError(error);
      }
      // Config errors and other unexpected failures: loud in server logs
      // (they never contain secrets), opaque to the browser.
      console.error("[api/console] unhandled route error:", error);
      return errorResponse(500, "Internal server error.");
    }
  };
}
