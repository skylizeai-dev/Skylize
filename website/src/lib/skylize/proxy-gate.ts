// Edge gate for the console — the logic behind src/proxy.ts (Next.js 16's
// middleware file convention). Optimistic, fail-closed session check for
// everything under /console and /api/console, with the two contract-mandated
// exemptions. Route handlers re-verify authoritatively via handler.ts, so
// this layer is defense-in-depth plus the login redirect for pages.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE_NAME, verifySessionToken } from "./session";

/** Paths reachable without a session (per the frozen contract). */
const PUBLIC_EXACT_PATHS = new Set(["/console/login", "/api/console/session"]);

/**
 * Identity-ish headers a client must never be able to smuggle past the edge;
 * stripped from every proxied console request before it reaches server code.
 */
const SPOOFABLE_HEADERS = ["x-skylize-user", "x-skylize-org"] as const;

function passThrough(request: NextRequest): NextResponse {
  const headers = new Headers(request.headers);
  for (const name of SPOOFABLE_HEADERS) headers.delete(name);
  return NextResponse.next({ request: { headers } });
}

export async function consoleProxyGate(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;

  if (PUBLIC_EXACT_PATHS.has(pathname)) {
    return passThrough(request);
  }

  // Fail closed: no cookie, no configured secret, or a bad signature all
  // land in the same unauthenticated branch — never an exception, never a pass.
  const token = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const secret = process.env.SKYLIZE_CONSOLE_SESSION_SECRET;
  const authenticated =
    token !== undefined && secret !== undefined && secret !== ""
      ? await verifySessionToken(token, secret)
      : false;

  if (authenticated) {
    return passThrough(request);
  }

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Authentication required." }, { status: 401 });
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/console/login";
  loginUrl.search = "";
  return NextResponse.redirect(loginUrl);
}
