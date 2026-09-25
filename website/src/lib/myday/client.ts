// Server-only HTTP client for the Skylize backend, authenticating as THE END
// USER rather than as the service.
//
// THE ONE DIFFERENCE FROM lib/skylize/client.ts, AND THE REASON THIS FILE
// EXISTS: that client sends `X-API-Key: <serviceApiKey>` on every call, so the
// backend resolves every request to the SERVICE's identity. Every My Day surface
// is per-employee — `/api/v1/me/brief` and `/api/v1/cowork/turns` both derive
// `principal_id` from `ctx.user_id` and refuse to take it from the caller — so
// routing My Day through the service key would show every employee the SAME
// person's journal, spend and decisions. This client sends
// `Authorization: Bearer <the user's own access JWT>` instead, which the backend
// resolves via `get_context_or_user` (edge/deps.py:97-125) into that user's real
// org_id / user_id / roles.
//
// THERE IS NO SERVICE KEY IN THIS MODULE, and none reachable from it: it imports
// only `getMyDayBackendConfig`, which reads the backend URL and nothing else. A
// fallback to the service credential is therefore not merely forbidden here, it
// is unavailable — the property the design calls "impossible by construction".
//
// NO RETRIES, deliberately, unlike the console client's GET retry. A My Day call
// can arrive with an access token seconds from expiry; the correct response to a
// 401 is to refresh the token and re-issue ONCE (handler.ts owns that), not to
// replay the same doomed credential. Retrying a 5xx is still safe in principle
// but is left out so this module has exactly one job.

import { getMyDayBackendConfig } from "./config";

const DEFAULT_TIMEOUT_MS = 10_000;
/** A co-work turn is a full governed agent run against a paid provider. */
export const TURN_TIMEOUT_MS = 120_000;

/** Typed failure. Carries no headers and never the bearer token. */
export class MyDayApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "MyDayApiError";
    this.status = status;
  }
}

export interface MyDayFetchOptions {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
}

/**
 * A successful backend response, WITH its status preserved.
 *
 * The status is carried deliberately rather than discarded. `/api/v1/cowork/turns`
 * answers 201 for a completed turn and 202 for one DEFERRED to a human
 * (edge/routes/cowork.py:129-137) — both are `response.ok`, so a helper that
 * returned only the body would make a deferral indistinguishable from an answer
 * and let a route stamp its own wrong status on it. A deferral is a governance
 * outcome; reporting it as a completed turn would be exactly the kind of quiet
 * misstatement this path exists to avoid.
 */
export interface MyDayResult<T> {
  status: number;
  body: T;
}

/**
 * Call the backend as the user whose access token is passed in.
 *
 * `accessToken` is a REQUIRED parameter with no default, so a caller cannot
 * accidentally invoke this unauthenticated and have it silently fall back to
 * some other credential — there is no other credential, and omitting the
 * argument is a type error rather than a runtime downgrade.
 *
 * A backend 401 is surfaced as MyDayApiError(401) for the caller to act on
 * (handler.ts refreshes once, then fails closed). It is NOT translated to 502
 * the way the console client's mapper does: for the console a 401 means "the
 * SERVICE credential was rejected" (an infrastructure fault), whereas here it
 * means "THIS USER's token is no longer good" (a session fault). Collapsing the
 * two would either hide an expired session behind a server error or, worse,
 * invite a retry with a different credential.
 */
export async function mydayFetch<T>(
  path: string,
  accessToken: string,
  options: MyDayFetchOptions = {},
): Promise<MyDayResult<T>> {
  const { backendUrl } = getMyDayBackendConfig();
  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const headers: Record<string, string> = {
    // The user's own JWT. Never a service key — this module cannot read one.
    Authorization: `Bearer ${accessToken}`,
    Accept: "application/json",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${backendUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
      signal: controller.signal,
    });
  } catch {
    const timedOut = controller.signal.aborted;
    throw new MyDayApiError(
      timedOut ? 504 : 502,
      timedOut ? "The backend timed out." : "Could not reach the backend.",
    );
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 204) return { status: 204, body: null as T };

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    // The backend's own `detail` when it sent one, so a real cause survives the
    // hop. Never echoes request headers, so the bearer token cannot leak into an
    // error body.
    const detail =
      typeof parsed === "object" && parsed !== null && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : `Backend request failed (HTTP ${response.status}).`;
    throw new MyDayApiError(response.status, detail);
  }

  return { status: response.status, body: parsed as T };
}

/** The backend's token-pair response (`TokenResponse`, edge/routes/auth.py:71-74). */
export interface BackendTokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

/**
 * Exchange email + password for a token pair (`POST /api/v1/auth/login`).
 *
 * Unauthenticated by nature — it IS the login — so it does not go through
 * `mydayFetch`, which requires an access token. The password is sent once, to
 * the backend, over the server-side hop only; it is never stored, logged, or
 * placed in a cookie. Only the resulting tokens are sealed.
 */
export async function backendLogin(
  email: string,
  password: string,
): Promise<BackendTokenPair> {
  const { backendUrl } = getMyDayBackendConfig();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch {
    const timedOut = controller.signal.aborted;
    throw new MyDayApiError(
      timedOut ? 504 : 502,
      timedOut ? "The backend timed out." : "Could not reach the backend.",
    );
  } finally {
    clearTimeout(timer);
  }

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    // 401 from the backend means bad credentials. Forwarded as 401 so the login
    // screen can say so; the backend's own wording is not echoed for a failed
    // login, to avoid turning this into a credential-probing oracle.
    throw new MyDayApiError(
      response.status === 401 ? 401 : response.status,
      response.status === 401
        ? "Invalid email or password."
        : `Backend login failed (HTTP ${response.status}).`,
    );
  }

  if (
    typeof parsed !== "object" ||
    parsed === null ||
    typeof (parsed as BackendTokenPair).access_token !== "string" ||
    typeof (parsed as BackendTokenPair).refresh_token !== "string"
  ) {
    throw new MyDayApiError(502, "The backend returned an unreadable token pair.");
  }

  return parsed as BackendTokenPair;
}

/**
 * Spend a refresh token for a new pair (`POST /api/v1/auth/refresh`).
 *
 * SINGLE-USE AT THE BACKEND: `UserAuthService.refresh` revokes the consumed
 * token BEFORE minting the replacement (app/auth/user_service.py:200-206), so
 * the caller must persist the new pair or lose the session. A failure here is
 * terminal for the session — the old refresh token is already spent — which is
 * exactly why handler.ts treats it as "clear the cookie and re-prompt login"
 * rather than retrying.
 */
export async function backendRefresh(refreshToken: string): Promise<BackendTokenPair> {
  const { backendUrl } = getMyDayBackendConfig();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch {
    const timedOut = controller.signal.aborted;
    throw new MyDayApiError(
      timedOut ? 504 : 502,
      timedOut ? "The backend timed out." : "Could not reach the backend.",
    );
  } finally {
    clearTimeout(timer);
  }

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    throw new MyDayApiError(
      response.status === 401 ? 401 : response.status,
      "The session could not be renewed.",
    );
  }

  if (
    typeof parsed !== "object" ||
    parsed === null ||
    typeof (parsed as BackendTokenPair).access_token !== "string" ||
    typeof (parsed as BackendTokenPair).refresh_token !== "string"
  ) {
    throw new MyDayApiError(502, "The backend returned an unreadable token pair.");
  }

  return parsed as BackendTokenPair;
}
