// Cross-site request guard for the My Day BFF (/api/my-day/*).
//
// WHY THIS EXISTS. The `skylize_myday` cookie is SameSite=Lax, which still lets a
// top-level cross-site form POST carry it AND keep the cookie a response sets.
// `request.json()` ignores Content-Type, so an attacker's
// `<form enctype="text/plain">` can submit a body that parses as
// `{"email":..., "password":...}` and log the victim into the ATTACKER's My Day
// account (login CSRF; the BLOCKING finding N1 of the PR #27 security review).
// Every state-changing My Day request therefore has to prove it came from this
// site's own pages.
//
// THE RULE (fail closed):
//   1. `Sec-Fetch-Site` present -> accept ONLY "same-origin". Browsers set this
//      header themselves and page script cannot forge it. "same-site" (a sibling
//      subdomain), "cross-site", and "none" (typed URL, bookmark) are refused.
//   2. Absent -> fall back to `Origin`, which must equal the request's own origin
//      (scheme + host + port). The literal "null" origin is refused.
//   3. Both absent -> refused. A browser sends at least one of them on every
//      POST/PUT/DELETE, so a request with neither is not one of our pages.
//
// Only consulted for state-changing methods (see `isStateChangingMethod`); reads
// stay unaffected. My Day only: Console has its own gate and does not import
// this module.

import type { NextRequest } from "next/server";

export type MyDayCsrfVerdict = { ok: true } | { ok: false; reason: string };

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** True for every method that can change state (POST, PUT, PATCH, DELETE, ...). */
export function isStateChangingMethod(method: string): boolean {
  return !SAFE_METHODS.has(method.toUpperCase());
}

/**
 * Decide whether a request came from this site's own origin. Never throws.
 * `reason` is for server logs only and is never sent to the client.
 */
export function verifySameOrigin(request: NextRequest): MyDayCsrfVerdict {
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite !== null) {
    return fetchSite.trim().toLowerCase() === "same-origin"
      ? { ok: true }
      : { ok: false, reason: `sec-fetch-site=${fetchSite}` };
  }

  const origin = request.headers.get("origin");
  if (origin !== null) {
    return origin === request.nextUrl.origin
      ? { ok: true }
      : { ok: false, reason: `origin mismatch (${origin})` };
  }

  return { ok: false, reason: "no sec-fetch-site and no origin header" };
}
