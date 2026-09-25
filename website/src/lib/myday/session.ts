// My Day session — an ENCRYPTED cookie carrying the user's OWN Skylize token
// pair. This is a deliberately SEPARATE mechanism from the console's
// `lib/skylize/session.ts`, and the difference is cryptographic, not cosmetic:
//
//   * The console cookie (`skylize_console`) is HMAC-SIGNED and carries
//     `v1.<expiry>.<signature>` — no secret at all. Signing is sufficient there
//     because there is nothing inside to keep confidential; the cookie only
//     asserts "the shared-password holder is present".
//   * This cookie (`skylize_myday`) CONTAINS BEARER TOKENS. Integrity alone is
//     not enough: it needs CONFIDENTIALITY, so it is sealed with AES-256-GCM
//     (authenticated encryption — confidentiality and integrity in one
//     primitive, so there is no separate HMAC step to get wrong).
//
// FOUR INDEPENDENT ISOLATION LAYERS from the console path, so neither can be
// mistaken for or forged from the other:
//   1. cookie NAME     — `skylize_myday` vs `skylize_console`
//   2. SECRET          — SKYLIZE_MYDAY_SESSION_SECRET vs SKYLIZE_CONSOLE_SESSION_SECRET
//   3. route namespace — /api/my-day/* vs /api/console/*
//   4. code path       — lib/myday/* is self-contained; it reads nothing from
//                        lib/skylize/* and imports no console module
//
// Web Crypto only (no node:crypto), matching the console module's constraint, so
// the same code runs in route handlers and in the proxy gate on any runtime.

/** Distinct from SESSION_COOKIE_NAME in lib/skylize/session.ts, on purpose. */
export const MYDAY_SESSION_COOKIE_NAME = "skylize_myday";

/**
 * Cookie lifetime. Bounded by the backend's REFRESH token TTL
 * (`jwt_refresh_token_ttl_days`, config.py:111 — 14 days), not by the 30-minute
 * access TTL: the access token inside is refreshed transparently, so the cookie
 * outliving one access token is the normal case, not an error.
 *
 * Kept deliberately shorter than the backend's 14 days. The cookie is the only
 * thing standing between a stolen browser session and a live refresh token, so
 * it expires first; the backend row remains revocable independently.
 */
export const MYDAY_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days

/** Domain-separation string bound into the AAD of every sealed payload. */
const SEALING_CONTEXT = "skylize-myday-session.v1";
const AES_GCM_IV_BYTES = 12; // 96-bit IV, the GCM standard
const AES_KEY_BITS = 256;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** What lives inside the sealed cookie. Never leaves the server unsealed. */
export interface MyDaySession {
  /** Skylize access JWT — forwarded as `Authorization: Bearer <this>`. */
  accessToken: string;
  /** Skylize refresh JWT — spent against POST /api/v1/auth/refresh. */
  refreshToken: string;
  /**
   * Access-token expiry, epoch MILLISECONDS, copied from the JWT's own `exp`
   * claim at seal time. Held here so the handler can decide to refresh WITHOUT
   * parsing the JWT on every request; the backend remains the authority on
   * validity either way, because a forged/early value only ever causes an extra
   * refresh attempt or a 401 — never an accepted request.
   */
  accessExpiresAtMs: number;
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> | null {
  try {
    const padded =
      value.replace(/-/g, "+").replace(/_/g, "/") +
      "=".repeat((4 - (value.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

/**
 * Derive a 256-bit AES-GCM key from the configured secret via SHA-256.
 *
 * The secret is an arbitrary-length high-entropy string, but AES-GCM needs
 * exactly 256 bits, so it is hashed to a fixed length rather than truncated
 * (truncation would silently discard entropy past byte 32).
 */
async function importAesKey(secret: string, usages: KeyUsage[]): Promise<CryptoKey> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(secret));
  return crypto.subtle.importKey("raw", digest, { name: "AES-GCM", length: AES_KEY_BITS }, false, usages);
}

/**
 * Seal a session into `v1.<base64url iv>.<base64url ciphertext+tag>`.
 *
 * A fresh random IV per seal is required for GCM — reusing one under the same
 * key is a catastrophic failure mode for this cipher, so it is generated here
 * and never derived from the payload.
 *
 * `SEALING_CONTEXT` is passed as additional authenticated data, so a ciphertext
 * produced for any other purpose (or under a future v2 format) fails to open
 * rather than decrypting into something the caller might trust.
 */
export async function sealSession(session: MyDaySession, secret: string): Promise<string> {
  const key = await importAesKey(secret, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(AES_GCM_IV_BYTES));
  const plaintext = encoder.encode(JSON.stringify(session));
  const sealed = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: encoder.encode(SEALING_CONTEXT) },
      key,
      plaintext,
    ),
  );
  return `v1.${toBase64Url(iv)}.${toBase64Url(sealed)}`;
}

/**
 * Open a sealed cookie, or return null.
 *
 * NULL IS THE ONLY FAILURE SIGNAL, and every failure mode collapses into it:
 * wrong shape, wrong version, bad base64, wrong key, tampered ciphertext,
 * tampered AAD, or a payload that is not a well-formed session. Callers treat
 * null as "no session" and MUST fail closed — they must never substitute any
 * other credential (see lib/myday/client.ts, which holds no other credential to
 * substitute).
 */
export async function openSession(
  sealedValue: string,
  secret: string,
): Promise<MyDaySession | null> {
  const parts = sealedValue.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;

  const iv = fromBase64Url(parts[1]);
  const sealed = fromBase64Url(parts[2]);
  if (iv === null || sealed === null || iv.length !== AES_GCM_IV_BYTES) return null;

  let plaintext: ArrayBuffer;
  try {
    const key = await importAesKey(secret, ["decrypt"]);
    plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv, additionalData: encoder.encode(SEALING_CONTEXT) },
      key,
      sealed,
    );
  } catch {
    // Authentication failure (tampering, wrong secret) throws here. Swallowed
    // into null on purpose: the caller gets one uniform "no session" answer and
    // cannot accidentally branch on the cryptographic reason.
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(decoder.decode(plaintext));
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      typeof (parsed as MyDaySession).accessToken !== "string" ||
      typeof (parsed as MyDaySession).refreshToken !== "string" ||
      typeof (parsed as MyDaySession).accessExpiresAtMs !== "number" ||
      !(parsed as MyDaySession).accessToken ||
      !(parsed as MyDaySession).refreshToken
    ) {
      return null;
    }
    return parsed as MyDaySession;
  } catch {
    return null;
  }
}

/**
 * Read an access JWT's `exp` claim as epoch milliseconds.
 *
 * DECODE ONLY — this deliberately does NOT verify the signature, and must never
 * be used to decide whether a token is trustworthy. The BACKEND is the sole
 * authority on validity (edge/deps.py `get_context_or_user` verifies HS256
 * against the server-side secret, which this layer does not hold). The value is
 * used for one thing: deciding when to proactively refresh. A wrong value costs
 * an extra refresh or produces a backend 401 — it can never admit a bad token.
 *
 * Returns 0 when the claim cannot be read, which reads as "already expired" and
 * therefore triggers a refresh rather than a silent pass.
 */
export function readAccessTokenExpiryMs(accessToken: string): number {
  const segments = accessToken.split(".");
  if (segments.length !== 3) return 0;
  const payloadBytes = fromBase64Url(segments[1]);
  if (payloadBytes === null) return 0;
  try {
    const claims: unknown = JSON.parse(decoder.decode(payloadBytes));
    const exp = (claims as { exp?: unknown }).exp;
    return typeof exp === "number" && Number.isFinite(exp) ? exp * 1000 : 0;
  } catch {
    return 0;
  }
}

interface MyDayCookie {
  name: string;
  value: string;
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

/**
 * Cookie descriptor. `httpOnly` is the load-bearing flag: it is what keeps the
 * sealed token pair out of client-side JavaScript entirely. Even sealed, the
 * cookie must never be readable by the bundle.
 */
export function mydaySessionCookie(sealedValue: string): MyDayCookie {
  return {
    name: MYDAY_SESSION_COOKIE_NAME,
    value: sealedValue,
    httpOnly: true,
    // `secure` everywhere except `next dev`, whose origin is plain http —
    // same single exception the console cookie makes, for the same reason.
    secure: process.env.NODE_ENV !== "development",
    sameSite: "lax",
    path: "/",
    maxAge: MYDAY_SESSION_TTL_SECONDS,
  };
}

/** Expired variant — logout, and every fail-closed path. */
export function clearedMydaySessionCookie(): MyDayCookie {
  return { ...mydaySessionCookie(""), maxAge: 0 };
}
