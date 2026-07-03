import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { extractBearerToken } from "@/lib/console-auth";

/**
 * Gate every /api/console/* route so no request without a bearer access token
 * reaches a console route handler — closing the whole class, not one route.
 *
 * Per Next.js 16 guidance, Proxy (formerly Middleware) performs only the fast
 * optimistic check (token present) and strips spoofable identity headers.
 * Authoritative token->org validation happens in each route handler via
 * lib/console-auth (authenticateConsoleRequest).
 */
export function proxy(request: NextRequest) {
  const token = extractBearerToken(request.headers.get("authorization"));
  if (!token) {
    return NextResponse.json(
      { error: "Authentication required." },
      { status: 401 },
    );
  }

  // A client must never be able to forge identity headers that downstream
  // server code might trust; strip them before the request proceeds.
  const headers = new Headers(request.headers);
  headers.delete("x-skylize-user");
  headers.delete("x-skylize-org");
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: "/api/console/:path*",
};
