import type { NextRequest } from "next/server";

import { consoleProxyGate } from "@/lib/skylize/proxy-gate";
import { mydayProxyGate } from "@/lib/skylize/myday-handler";

/**
 * Next.js 16 renamed the `middleware` file convention to `proxy` — this file
 * IS the console-BFF contract's "middleware.ts" deliverable (a root
 * middleware.ts alongside it would be a build error).
 *
 * Auth = the signed `skylize_console` session cookie. This is the fast,
 * fail-closed edge check; every /api/console route handler re-verifies the
 * session authoritatively via lib/skylize/handler.ts, so the gate here is
 * defense-in-depth plus the /console/login redirect for pages.
 */
export async function proxy(request: NextRequest) {
  // My Day is a fully isolated namespace with its OWN self-guarding route
  // handlers (mydayRoute) and its OWN encrypted cookie. It must NOT run through
  // consoleProxyGate, whose Console-cookie check would 401 every My Day request.
  // This early return is a pure addition placed BEFORE the Console branch, which
  // is left byte-for-byte unchanged; mydayProxyGate only strips spoofable
  // identity headers (defense-in-depth), exactly as the Console gate does.
  if (request.nextUrl.pathname.startsWith("/api/my-day")) {
    return mydayProxyGate(request);
  }
  return consoleProxyGate(request);
}

export const config = {
  matcher: ["/console/:path*", "/api/console/:path*", "/api/my-day/:path*"],
};
