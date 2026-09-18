// Server-only HTTP client for the Skylize FastAPI backend.
//
// This is the ONLY module that attaches the service API key, and the key
// never leaves it except inside the X-API-Key request header: it is never
// logged, never included in an error, never returned to a caller.

import { getBackendConfig } from "./config";

const DEFAULT_TIMEOUT_MS = 10_000;
/** Retries after the initial attempt, on network failure / timeout / 5xx only. */
const MAX_RETRIES = 2;

/**
 * The backend's machine-readable refusal causes (src/skylize/edge/errors.py
 * `ErrorCode`). A CLOSED set, mirrored here as a union so the console can switch
 * on it exhaustively — a backend that ever sent an unlisted value is treated as
 * "no code" rather than silently trusted (see `asBackendErrorCode`).
 */
export const BACKEND_ERROR_CODES = [
  "decision_rejected",
  "governance_denied",
  "authorization_failed",
  // The rest of the backend's closed set (edge/errors.py `ErrorCode`). These
  // were missing while only /agents/execute forwarded a code and only three
  // causes could reach it. The co-work turn route raises
  // `principal_authority_denied` on its own path (cowork.py, the PrincipalError
  // branch) -- "the caller's ROLE let them in; their own authority did not" --
  // and an unlisted value is narrowed to null by `asBackendErrorCode`, so
  // leaving it out would silently erase the one cause that tells an operator
  // the refusal was about THEM rather than the request.
  "principal_authority_denied",
  "spend_ceiling_exceeded",
  "spend_ceiling_not_configured",
  "model_not_priced",
  "provider_unavailable",
  "provider_timeout",
  "org_not_available",
] as const;

export type BackendErrorCode = (typeof BACKEND_ERROR_CODES)[number];

/** Narrow an unknown body value to a known code, or null. */
export function asBackendErrorCode(value: unknown): BackendErrorCode | null {
  return typeof value === "string" &&
    (BACKEND_ERROR_CODES as readonly string[]).includes(value)
    ? (value as BackendErrorCode)
    : null;
}

/** Typed failure surfaced to route handlers. Carries no headers and no key. */
export class SkylizeApiError extends Error {
  /**
   * Backend HTTP status for responses the backend actually sent;
   * 502 for network failures, 504 for timeouts.
   */
  readonly status: number;

  /**
   * The backend's `code`, when the error body carried one. null for every
   * uncoded backend error and for every locally synthesised failure (network,
   * timeout, malformed body) — those have no backend cause to name.
   */
  readonly code: BackendErrorCode | null;

  constructor(status: number, message: string, code: BackendErrorCode | null = null) {
    super(message);
    this.name = "SkylizeApiError";
    this.status = status;
    this.code = code;
  }
}

export interface SkylizeFetchOptions {
  /**
   * PUT joins GET/POST for the autonomy route's idempotent write
   * (backend PUT /api/v1/autonomy). Like POST it is never auto-retried --
   * `canRetry` below stays GET-only, so adding PUT cannot introduce a
   * duplicate write even though the backend's PUT happens to be idempotent.
   *
   * DELETE is here for api-key revocation (backend DELETE /api/v1/api-keys/
   * {key_id}). It is likewise never auto-retried: the backend answers 404 for
   * a key that is already gone, so a retry after a timeout that actually
   * SUCCEEDED would turn a completed revocation into a reported failure.
   */
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** JSON-serialized as the request body when provided. */
  body?: unknown;
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
 * Read the backend's error body: the human message from `detail` (unchanged —
 * still a plain string, still the only source of the message) and the optional
 * machine-readable `code` that now travels beside it. A body with no `code` is
 * exactly as usable as before.
 */
async function extractError(
  response: Response,
): Promise<{ message: string; code: BackendErrorCode | null }> {
  const fallback = `Backend error (HTTP ${response.status}).`;
  try {
    const data: unknown = await response.json();
    if (data !== null && typeof data === "object") {
      const code = asBackendErrorCode((data as { code?: unknown }).code);
      if ("detail" in data) {
        const detail = (data as { detail: unknown }).detail;
        if (typeof detail === "string" && detail.length > 0) {
          return { message: detail, code };
        }
      }
      return { message: fallback, code };
    }
  } catch {
    // Non-JSON error body — fall through to the generic message.
  }
  return { message: fallback, code: null };
}

/**
 * Call the Skylize backend. Resolves with the parsed JSON body on 2xx, throws
 * SkylizeApiError otherwise.
 *
 * Retries (max 2, with backoff) apply ONLY to safe, idempotent GET requests,
 * and only on network failures, timeouts, and 5xx responses — never to 4xx.
 * POST requests are NEVER auto-retried: the console's POSTs (creative run,
 * kill-switch engage) are non-idempotent, and a retry after a timeout could
 * double-execute a request the backend already received.
 */
export async function skylizeFetch<T>(
  path: string,
  options: SkylizeFetchOptions = {},
): Promise<T> {
  const { backendUrl, serviceApiKey } = getBackendConfig();
  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const url = `${backendUrl}${path}`;
  // Only safe, idempotent methods may be transparently retried.
  const canRetry = method === "GET";

  const headers: Record<string, string> = {
    "X-API-Key": serviceApiKey,
    Accept: "application/json",
  };
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
      throw new SkylizeApiError(
        timedOut ? 504 : 502,
        timedOut
          ? `Backend request timed out after ${timeoutMs}ms.`
          : "Backend unreachable.",
      );
    }
    clearTimeout(timer);

    if (response.ok) {
      // 204 carries NO body by definition, and the backend uses it for a
      // successful api-key revocation (api_keys.py `revoke_key`). Parsing it
      // would throw and report a completed revocation as a malformed body, so
      // an empty success resolves as null rather than failing.
      if (response.status === 204) {
        return null as T;
      }
      try {
        return (await response.json()) as T;
      } catch {
        throw new SkylizeApiError(502, "Backend returned a malformed JSON body.");
      }
    }

    if (response.status >= 500 && canRetry && attempt < MAX_RETRIES) {
      await sleep(backoffDelayMs(attempt));
      continue;
    }
    const { message, code } = await extractError(response);
    throw new SkylizeApiError(response.status, message, code);
  }

  // Unreachable: every loop path returns or throws. Satisfies the compiler.
  throw new SkylizeApiError(502, "Backend unreachable.");
}
