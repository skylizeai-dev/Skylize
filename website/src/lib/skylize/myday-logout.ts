// The response contract of DELETE /api/my-day/session (OD20), exported here so
// a caller can rely on it. It lives in a lib module, not in the route file,
// because a Next.js route file may only export route handlers and route config.
//
//   * FULL logout    — 204, empty body, cookie cleared. The refresh token was
//                      revoked server-side, or there was nothing to revoke (no
//                      cookie, or a cookie that does not decrypt under a VALID
//                      config).
//   * PARTIAL logout — 200, MYDAY_PARTIAL_LOGOUT_BODY, cookie cleared. This
//                      device is signed out, but the server-side session could
//                      not be ended: revocation failed after its single retry,
//                      or a config error made revocation impossible to attempt.
//
// In both cases the cookie is ALWAYS cleared.

/** HTTP status of a partial logout (a full logout is 204 with no body). */
export const MYDAY_PARTIAL_LOGOUT_STATUS = 200;

/** Greppable server-log tag for a partial logout (revocation failed). */
export const MYDAY_LOGOUT_PARTIAL_TAG = "myday_logout_partial";

/** JSON body of a partial logout. */
export interface MyDayPartialLogoutBody {
  logout: "partial";
  serverSessionEnded: false;
  message: string;
}

export const MYDAY_PARTIAL_LOGOUT_BODY: Readonly<MyDayPartialLogoutBody> = Object.freeze({
  logout: "partial",
  serverSessionEnded: false,
  message: "Signed out on this device; we could not end the session on the server.",
});
