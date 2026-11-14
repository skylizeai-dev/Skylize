// In-process single-flight for the My Day refresh-on-401 path (myday-handler.ts
// `createAccessTokenRunner`).
//
// THE RACE IT CLOSES. Two tabs whose access token expired at the same moment
// both get a backend 401 and both refresh with the SAME refresh token from the
// same cookie. The backend rotation is single-winner, so before this module the
// loser's refresh got a 401 and its response CLEARED the cookie, forcing a
// re-login. Now concurrent refreshes that carry the same refresh token share ONE
// upstream call: every waiter gets the same new pair (or the same failure).
//
// SCOPE - ONE NODE PROCESS ONLY. The map lives in module memory. Requests served
// by different instances (several Node processes, serverless invocations) still
// race; deployment topology is UNKNOWN. Across processes the damage is bounded by
// the backend's reuse grace window (src/skylize/app/auth/user_service.py
// `REFRESH_REUSE_GRACE_SECONDS`): a consumed token re-presented within it gets a
// plain 401, never a user-wide revocation. A request that arrives just AFTER the
// shared refresh settled (its cookie predates the new pair) is also not covered:
// the entry is gone by then, by design (below).
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
 * Single-flight `refresh(refreshToken)`: concurrent callers presenting the SAME
 * refresh token share one call and one result (or one failure).
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
