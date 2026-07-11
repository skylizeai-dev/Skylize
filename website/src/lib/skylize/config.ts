// Server-only configuration for the Skylize console BFF.
//
// These four variables are the BFF's entire secret surface. They are read
// lazily (at first use inside a request, never at module load) so `next build`
// succeeds without them, and each getter throws a clear, actionable error the
// moment a route actually needs a value that is missing.
//
// None of these names may EVER carry a NEXT_PUBLIC_ prefix — Next.js inlines
// NEXT_PUBLIC_* values into the client bundle, which for these values would be
// a credential leak. `readServerEnv` actively asserts that no NEXT_PUBLIC_
// variant of a secret is configured.

export interface SkylizeBackendConfig {
  /** Railway backend origin, no trailing slash. */
  backendUrl: string;
  /** Sent as X-API-Key on every backend call. Never logged, never returned. */
  serviceApiKey: string;
}

export interface ConsoleAuthConfig {
  /** INTERIM single console password (placeholder for the deferred OIDC epic). */
  accessPassword: string;
  /** HMAC-SHA256 secret for the skylize_console session cookie. */
  cookieSecret: string;
}

function assertServerOnly(): void {
  if (typeof window !== "undefined") {
    throw new Error(
      "lib/skylize/config was imported into client-side code. " +
        "It holds server secrets and must only be used from route handlers, " +
        "the proxy gate, or other server-only modules.",
    );
  }
}

function readServerEnv(name: string): string {
  // Computed access on purpose: prevents any build-time inlining and lets us
  // probe the NEXT_PUBLIC_ variant without referencing it statically.
  const leakedName = `NEXT_PUBLIC_${name}`;
  if (process.env[leakedName] !== undefined) {
    throw new Error(
      `${leakedName} is set. ${name} is a server-only secret and must never ` +
        "be exposed with a NEXT_PUBLIC_ prefix — Next.js would inline it into " +
        `the client bundle. Remove ${leakedName} and set ${name} instead.`,
    );
  }
  const value = process.env[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(
      `Missing required server environment variable ${name}. ` +
        "Set it in the deployment environment (or website/.env.local for " +
        "local development). It must NOT be prefixed with NEXT_PUBLIC_.",
    );
  }
  return value.trim();
}

let backendConfig: SkylizeBackendConfig | null = null;
let consoleAuthConfig: ConsoleAuthConfig | null = null;

/** Backend connection settings — validated on first use. */
export function getBackendConfig(): SkylizeBackendConfig {
  assertServerOnly();
  if (backendConfig) return backendConfig;

  const rawUrl = readServerEnv("SKYLIZE_BACKEND_URL");
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error(
      `SKYLIZE_BACKEND_URL is not a valid URL: received ${JSON.stringify(rawUrl)}.`,
    );
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("SKYLIZE_BACKEND_URL must be an http(s) URL.");
  }

  backendConfig = {
    backendUrl: rawUrl.replace(/\/+$/, ""),
    serviceApiKey: readServerEnv("SKYLIZE_SERVICE_API_KEY"),
  };
  return backendConfig;
}

/** Console gate settings (interim password + cookie secret) — validated on first use. */
export function getConsoleAuthConfig(): ConsoleAuthConfig {
  assertServerOnly();
  if (consoleAuthConfig) return consoleAuthConfig;

  const cookieSecret = readServerEnv("SKYLIZE_CONSOLE_SESSION_SECRET");
  if (cookieSecret.length < 32) {
    throw new Error(
      "SKYLIZE_CONSOLE_SESSION_SECRET must be at least 32 characters of " +
        "high-entropy random data (it keys the HMAC that signs console " +
        "session cookies).",
    );
  }

  consoleAuthConfig = {
    accessPassword: readServerEnv("SKYLIZE_CONSOLE_PASSWORD"),
    cookieSecret,
  };
  return consoleAuthConfig;
}
