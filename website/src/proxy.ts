import type { NextRequest } from "next/server";

import { consoleProxyGate } from "@/lib/skylize/proxy-gate";

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
  return consoleProxyGate(request);
}

export const config = {
  matcher: ["/console/:path*", "/api/console/:path*"],
};
