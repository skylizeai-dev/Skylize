// Server-only configuration for the My Day BFF (backend-for-frontend).
//
// This module is a DELIBERATE, isolated copy of the discipline in
// `lib/skylize/config.ts` (the same `assertServerOnly` guard, the same
// `readServerEnv` NEXT_PUBLIC_-leak / trim / non-empty checks). It does NOT
// import that module, and it MUST NOT: `config.ts` exports `getBackendConfig`,
// the only getter that returns `serviceApiKey`, and the whole point of the My
// Day path is that it can never reach that credential (design section 5.0).
// Importing the module that produces the service key — even without calling the
// getter — would put it one hop away from the My Day import graph, so the copy
// is intentional, not accidental duplication.
//
// My Day's cookie carries a REAL bearer token pair and is therefore ENCRYPTED
// (AES-256-GCM), not merely signed. AES-256 needs exactly 32 bytes of key
// material. Rather than force operators to supply a base64/hex secret that
// decodes to exactly 32 bytes, we accept any high-entropy string of >= 32
// chars and DERIVE a 32-byte key from it via SHA-256 in `myday-session.ts`.
// This getter validates only the raw secret's presence and minimum length; the
// SHA-256 derivation lives beside the crypto that consumes it.
//
// As with `config.ts`, none of these names may EVER carry a NEXT_PUBLIC_
// prefix; `readServerEnv` actively asserts that no NEXT_PUBLIC_ variant exists.

/** Minimum characters for the raw My Day cookie secret before SHA-256 derivation. */
export const MYDAY_SESSION_SECRET_MIN_LENGTH = 32;

export interface MyDayConfig {
  /** Railway backend origin, no trailing slash — the SAME origin Console reads. */
  backendUrl: string;
  /**
   * The raw high-entropy secret for the skylize_myday cookie. It is NOT the AES
   * key: `deriveMyDayKeyMaterial` (myday-session.ts) SHA-256s it into the
   * 32-byte AES-256-GCM key. Distinct env var from the Console cookie secret.
   */
  cookieSecret: string;
}

function assertServerOnly(): void {
  if (typeof window !== "undefined") {
    throw new Error(
      "lib/skylize/myday-config was imported into client-side code. " +
        "It reads server-only environment and must only be used from route " +
        "handlers, the proxy gate, or other server-only modules.",
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

let myDayConfig: MyDayConfig | null = null;

/**
 * My Day BFF settings — validated on first use (never at module load, so
 * `next build` succeeds without them). Reuses Console's backend-origin env var
 * `SKYLIZE_BACKEND_URL` (same http(s) validation and trailing-slash strip as
 * config.ts:63-85) but a NEW, distinct cookie secret `SKYLIZE_MYDAY_SESSION_SECRET`.
 *
 * It NEVER reads `SKYLIZE_SERVICE_API_KEY`: the My Day path has no code that can
 * produce the service credential.
 */
export function getMyDayConfig(): MyDayConfig {
  assertServerOnly();
  if (myDayConfig) return myDayConfig;

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

  const cookieSecret = readServerEnv("SKYLIZE_MYDAY_SESSION_SECRET");
  if (cookieSecret.length < MYDAY_SESSION_SECRET_MIN_LENGTH) {
    throw new Error(
      `SKYLIZE_MYDAY_SESSION_SECRET must be at least ${MYDAY_SESSION_SECRET_MIN_LENGTH} ` +
        "characters of high-entropy random data. It is SHA-256-derived into the " +
        "32-byte AES-256-GCM key that encrypts My Day session cookies, and must " +
        "be distinct from SKYLIZE_CONSOLE_SESSION_SECRET — a shared secret would " +
        "defeat the cryptographic isolation this path exists to provide.",
    );
  }

  myDayConfig = {
    backendUrl: rawUrl.replace(/\/+$/, ""),
    cookieSecret,
  };
  return myDayConfig;
}
