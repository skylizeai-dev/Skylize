// Server-only HTTP client for the My Day BFF's calls to the Skylize backend.
//
// This is the My Day analogue of `lib/skylize/client.ts`, but its authentication
// model is the OPPOSITE and that difference is the point:
//
//   * `client.ts` attaches the shared SERVICE key as `X-API-Key` on every call.
//   * this module attaches the USER'S OWN access JWT as `Authorization: Bearer
//     <access_token>` — a per-user, per-org credential the caller supplies as an
//     argument. It has NO access to `serviceApiKey` and no code path that could
//     read it: it imports `getMyDayConfig` (which never returns the service key),
//     NOT `getBackendConfig`, and it does not import `client.ts` at all.
//
// Because the service credential is never imported here, the My Day path CANNOT
// fall back to it (design section 5.0 — fail-closed by construction).
//
// The fetch/retry/error discipline is modelled loosely on client.ts: a bounded
// timeout, GET-only auto-retry (never retry a non-idempotent POST), and a typed
// error that carries neither the bearer token nor any secret.

import { getMyDayConfig } from "./myday-config";
import type { MyDayTokenPair } from "./myday-session";

const DEFAULT_TIMEOUT_MS = 10_000;
/** Retries after the initial attempt, on network failure / timeout / 5xx only. */
const MAX_RETRIES = 2;

/**
 * Typed failure surfaced to route handlers. Carries an HTTP status and a plain
 * message only — NEVER the bearer token, the refresh token, or any secret, and
 * no request headers.
 */
export class MyDayApiError extends Error {
  /**
   * Backend HTTP status for responses the backend actually sent;
   * 502 for network failures, 504 for timeouts.
   */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "MyDayApiError";
    this.status = status;
  }
}

interface MyDayFetchOptions {
  method?: "GET" | "POST";
  /** JSON-serialized as the request body when provided. */
  body?: unknown;
  /**
   * The user's access JWT. When present it is sent as `Authorization: Bearer
   * <token>`. Absent for the pre-auth login/refresh calls, which carry no user
   * credential yet. This is the ONLY credential this module ever attaches.
   */
  bearerToken?: string;
  timeoutMs?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 250ms, then 750ms — bounded, jitter-free backoff is enough at 2 retries. */
function backoffDelayMs(attempt: number): number {
  return 250 * Math.pow(3, attempt);
}

/**
 * Read the backend's error body for a human message from `detail`. The message
 * is the only thing extracted; nothing from the request (least of all the
 * bearer token) is ever echoed back into the error.
 */
async function extractErrorMessage(response: Response): Promise<string> {
  const fallback = `Backend error (HTTP ${response.status}).`;
  try {
    const data: unknown = await response.json();
    if (data !== null && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.length > 0) return detail;
    }
  } catch {
    // Non-JSON error body — fall through to the generic message.
  }
  return fallback;
}

/**
 * Call the Skylize backend on the My Day path. Resolves with the parsed JSON
 * body on 2xx (or null for a 204), throws MyDayApiError otherwise.
 *
 * Retries (max 2, with backoff) apply ONLY to GET requests, and only on network
 * failures, timeouts, and 5xx responses — never to 4xx and never to a POST,
 * whose replay could double-execute a login or consume a refresh token twice.
 */
async function myDayFetch<T>(
  path: string,
  options: MyDayFetchOptions = {},
): Promise<T> {
  const { backendUrl } = getMyDayConfig();
  const { method = "GET", body, bearerToken, timeoutMs = DEFAULT_TIMEOUT_MS } =
    options;
  const url = `${backendUrl}${path}`;
  // Only safe, idempotent methods may be transparently retried.
  const canRetry = method === "GET";

  const headers: Record<string, string> = { Accept: "application/json" };
  // The user's JWT is the ONLY credential attached — never a service key.
  if (bearerToken !== undefined) {
    headers.Authorization = `Bearer ${bearerToken}`;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      clearTimeout(timer);
      const timedOut = controller.signal.aborted;
      if (canRetry && attempt < MAX_RETRIES) {
        await sleep(backoffDelayMs(attempt));
        continue;
      }
      throw new MyDayApiError(
        timedOut ? 504 : 502,
        timedOut
          ? `Backend request timed out after ${timeoutMs}ms.`
          : "Backend unreachable.",
      );
    }
    clearTimeout(timer);

    if (response.ok) {
      if (response.status === 204) {
        return null as T;
      }
      try {
        return (await response.json()) as T;
      } catch {
        throw new MyDayApiError(502, "Backend returned a malformed JSON body.");
      }
    }

    if (response.status >= 500 && canRetry && attempt < MAX_RETRIES) {
      await sleep(backoffDelayMs(attempt));
      continue;
    }
    throw new MyDayApiError(response.status, await extractErrorMessage(response));
  }

  // Unreachable: every loop path returns or throws. Satisfies the compiler.
  throw new MyDayApiError(502, "Backend unreachable.");
}

// ── the My Day backend surface (auth endpoints only, per Stage 1) ───────────

/**
 * Pre-auth login: POST /api/v1/auth/login with { email, password }
 * (LoginRequest, src/skylize/edge/routes/auth.py:60-63,145-162). Returns the
 * backend token pair. Carries NO bearer token — there is no user credential yet.
 */
export async function mydayLogin(
  email: string,
  password: string,
): Promise<MyDayTokenPair> {
  const result = await myDayFetch<MyDayTokenPair>("/api/v1/auth/login", {
    method: "POST",
    body: { email, password },
  });
  return {
    access_token: result.access_token,
    refresh_token: result.refresh_token,
  };
}

/**
 * Pre-auth refresh: POST /api/v1/auth/refresh with { refresh_token }
 * (RefreshRequest, auth.py:66-68,165-180). Rotates the refresh token and returns
 * a NEW pair. The old refresh token is server-side revoked by the backend, so
 * the caller must persist the returned pair. Carries no Authorization header —
 * the refresh token travels in the body, exactly as the backend expects.
 */
export async function mydayRefresh(
  refreshToken: string,
): Promise<MyDayTokenPair> {
  const result = await myDayFetch<MyDayTokenPair>("/api/v1/auth/refresh", {
    method: "POST",
    body: { refresh_token: refreshToken },
  });
  return {
    access_token: result.access_token,
    refresh_token: result.refresh_token,
  };
}

/**
 * Authenticated GET, forwarding the user's access JWT as the Bearer credential
 * (the backend's human-user path reads it at src/skylize/edge/deps.py:63-66).
 * Use for reads such as GET /api/v1/auth/me. A backend 401 here means the access
 * token is expired/invalid; the caller should fail closed (or attempt refresh).
 */
export async function mydayGet<T>(path: string, bearerToken: string): Promise<T> {
  return myDayFetch<T>(path, { method: "GET", bearerToken });
}

/**
 * The backend's current-user shape (UserResponse, auth.py:77-83), returned by
 * GET /api/v1/auth/me. Exposed so route handlers and tests can type the read
 * that the isolation proof (design section 6.0) asserts against.
 */
export interface MyDayUser {
  user_id: string;
  org_id: string;
  email: string;
  display_name: string | null;
  roles: string[];
  is_active: boolean;
}
