// Server-only configuration for the My Day BFF.
//
// TWO VARIABLES, AND NEITHER IS THE CONSOLE'S SERVICE KEY. That omission is the
// whole security property of this path: the My Day BFF authenticates to the
// backend with the END USER'S own JWT, so it must not hold — and deliberately
// cannot read — the shared service credential. There is no
// SKYLIZE_SERVICE_API_KEY accessor anywhere in lib/myday/*, which makes a
// silent fallback to the wrong identity impossible by CONSTRUCTION rather than
// by discipline.
//
// `SKYLIZE_MYDAY_SESSION_SECRET` is distinct from the console's
// `SKYLIZE_CONSOLE_SESSION_SECRET`: separate keys mean compromising one session
// mechanism cannot forge a cookie for the other.
//
// Read lazily (at first use inside a request, never at module load) so
// `next build` succeeds without them, matching the console config's convention.
// Never prefixed NEXT_PUBLIC_ — Next.js inlines those into the client bundle,
// which for either of these would be a credential leak, so the reader actively
// asserts no NEXT_PUBLIC_ variant is set.

export interface MyDayBackendConfig {
  /** Backend origin, no trailing slash. Same backend the console talks to. */
  backendUrl: string;
}

export interface MyDaySessionConfig {
  /** AES-GCM sealing secret for the `skylize_myday` cookie. */
  sessionSecret: string;
}

function assertServerOnly(): void {
  if (typeof window !== "undefined") {
    throw new Error(
      "lib/myday/config was imported into client-side code. It reads " +
        "server-only secrets and must only be used from route handlers.",
    );
  }
}

function readServerEnv(name: string): string {
  // Computed access on purpose: prevents build-time inlining and lets us probe
  // the NEXT_PUBLIC_ variant without referencing it statically.
  const leakedName = `NEXT_PUBLIC_${name}`;
  if (process.env[leakedName] !== undefined) {
    throw new Error(
      `${leakedName} is set. ${name} is a server-only secret and must never be ` +
        "exposed with a NEXT_PUBLIC_ prefix — Next.js would inline it into the " +
        `client bundle. Remove ${leakedName} and set ${name} instead.`,
    );
  }
  const value = process.env[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(
      `Missing required server environment variable ${name}. Set it in the ` +
        "deployment environment (or website/.env.local for local development). " +
        "It must NOT be prefixed with NEXT_PUBLIC_.",
    );
  }
  return value.trim();
}

let backendConfig: MyDayBackendConfig | null = null;
let sessionConfig: MyDaySessionConfig | null = null;

/** Backend origin — validated on first use. */
export function getMyDayBackendConfig(): MyDayBackendConfig {
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

  backendConfig = { backendUrl: rawUrl.replace(/\/+$/, "") };
  return backendConfig;
}

/** Cookie sealing secret — validated on first use. */
export function getMyDaySessionConfig(): MyDaySessionConfig {
  assertServerOnly();
  if (sessionConfig) return sessionConfig;

  const sessionSecret = readServerEnv("SKYLIZE_MYDAY_SESSION_SECRET");
  if (sessionSecret.length < 32) {
    throw new Error(
      "SKYLIZE_MYDAY_SESSION_SECRET must be at least 32 characters of " +
        "high-entropy random data (it keys the AES-256-GCM seal over the " +
        "user's own access and refresh tokens).",
    );
  }

  sessionConfig = { sessionSecret };
  return sessionConfig;
}
