// Server-only HTTP client for the Skylize FastAPI backend.
//
// This is the ONLY module that attaches the service API key, and the key
// never leaves it except inside the X-API-Key request header: it is never
// logged, never included in an error, never returned to a caller.

import { getBackendConfig } from "./config";

const DEFAULT_TIMEOUT_MS = 10_000;
/** Retries after the initial attempt, on network failure / timeout / 5xx only. */
const MAX_RETRIES = 2;

/** Typed failure surfaced to route handlers. Carries no headers and no key. */
export class SkylizeApiError extends Error {
  /**
   * Backend HTTP status for responses the backend actually sent;
   * 502 for network failures, 504 for timeouts.
   */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "SkylizeApiError";
    this.status = status;
  }
}

export interface SkylizeFetchOptions {
  method?: "GET" | "POST";
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
    throw new SkylizeApiError(response.status, await extractErrorMessage(response));
  }

  // Unreachable: every loop path returns or throws. Satisfies the compiler.
  throw new SkylizeApiError(502, "Backend unreachable.");
}
