// In-process single-flight for the My Day refresh-on-401 path (myday-handler.ts
// `createAccessTokenRunner`).
//
// BEST-EFFORT OPTIMISATION: this module is not a correctness boundary.
// See docs/06_integrations/myday_session_revocation_design.md section 9.0.3.
// The BFF runs on Vercel Production, i.e. many instances / serverless
// invocations, each with its own module memory (owner ruling R-A). This map can
// only coalesce refreshes that happen to land in the SAME instance while one is
// in flight; across instances it does nothing, and nothing may rely on it.
// Correctness is the backend's: the rotation is single-winner and atomic
// (consume + insert in one transaction), and a refused refresh of a token
// consumed less than `REFRESH_REUSE_GRACE_SECONDS` (10 s) ago is a plain 401
// that writes and revokes nothing (src/skylize/app/auth/user_service.py).
// A distributed (Redis/DB) lock is deliberately not pursued here.
//
// WHAT IT SAVES, WHEN IT APPLIES. Two tabs whose access token expired at the
// same moment both get a backend 401 and both refresh with the SAME refresh
// token from the same cookie. If both requests reach this instance while the
// first refresh is in flight, they share ONE upstream call and both get the
// new pair, so neither response clears the cookie. In every other case (the
// requests reach different instances, or one arrives after the shared refresh
// settled and its entry is gone), the second refresh reaches the backend, is
// refused with a plain 401, and that request ends its session exactly as it
// would without this module.
//
// TOKEN HANDLING.
//   * The map key is a SHA-256 digest of the refresh token, never the raw token,
//     so no bearer credential is held as a key.
//   * An entry lives exactly as long as its in-flight promise: it is deleted in
//     `finally`, on resolve AND on reject, so a failure cannot poison a later
//     attempt and nothing outlives the request that created it.
//   * Nothing here logs; callers must not log the token either.
//
// IMPORT ISOLATION. This module imports nothing (Web Crypto is a global, the
// same choice myday-session.ts makes), so it cannot widen the My Day import graph
// checked by myday-import-isolation.test.ts.

const inFlight = new Map<string, Promise<unknown>>();

/**
 * Run `run` at most once at a time per `key`. A caller that arrives while a run
 * for the same key is in flight gets that run's promise instead of starting a
 * new one. The entry is removed when the promise settles, either way.
 */
export function singleFlight<T>(key: string, run: () => Promise<T>): Promise<T> {
  const existing = inFlight.get(key);
  if (existing !== undefined) return existing as Promise<T>;

  // `async` wrapper: a synchronous throw from `run` becomes a rejection, so the
  // `finally` below always runs and the entry is always removed.
  const promise = (async () => run())().finally(() => {
    if (inFlight.get(key) === promise) inFlight.delete(key);
  });
  inFlight.set(key, promise);
  return promise;
}

/** Hex SHA-256 of the token: the map key, so the raw token is never a key. */
async function refreshTokenKey(refreshToken: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(refreshToken));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Single-flight `refresh(refreshToken)`: concurrent callers in THIS process
 * presenting the SAME refresh token share one call and one result (or one
 * failure). Best-effort only; see the header.
 */
export async function refreshOnce<T>(
  refreshToken: string,
  refresh: (refreshToken: string) => Promise<T>,
): Promise<T> {
  const key = await refreshTokenKey(refreshToken);
  return singleFlight(key, () => refresh(refreshToken));
}

/** Test-only view of how many refreshes are in flight. */
export function inFlightRefreshCount(): number {
  return inFlight.size;
}
