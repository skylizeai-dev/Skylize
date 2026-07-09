// INTERIM console session gate — a single shared password establishing an
// HMAC-SHA256-signed, httpOnly cookie. This is an explicit placeholder for
// the deferred OIDC epic: when real identity lands, this module is replaced
// by per-user sessions and nothing outside lib/skylize should need to change.
//
// Web Crypto only (no node:crypto) so the same code runs in route handlers
// and in the proxy gate regardless of runtime.

export const SESSION_COOKIE_NAME = "skylize_console";
export const SESSION_TTL_SECONDS = 8 * 60 * 60; // ~8h per contract

/** Domain-separation prefix baked into every signed payload. */
const SIGNING_CONTEXT = "skylize-console-session.v1";
const HMAC_SHA256_BYTES = 32;

const encoder = new TextEncoder();

function importHmacKey(secret: string, usages: KeyUsage[]): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    usages,
  );
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
 * Mint a session token: `v1.<expiresAtMs>.<base64url HMAC-SHA256 signature>`.
 * The expiry is inside the signed payload, so it cannot be extended client-side.
 */
export async function createSessionToken(secret: string): Promise<string> {
  const expiresAtMs = Date.now() + SESSION_TTL_SECONDS * 1000;
  const payload = `${SIGNING_CONTEXT}.${expiresAtMs}`;
  const key = await importHmacKey(secret, ["sign"]);
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(payload)),
  );
  return `v1.${expiresAtMs}.${toBase64Url(signature)}`;
}

/** Verify shape, expiry, and signature. crypto.subtle.verify compares in constant time. */
export async function verifySessionToken(
  token: string,
  secret: string,
): Promise<boolean> {
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;

  const expiresAtMs = Number(parts[1]);
  if (!Number.isSafeInteger(expiresAtMs) || expiresAtMs <= Date.now()) return false;

  const signature = fromBase64Url(parts[2]);
  if (signature === null || signature.length !== HMAC_SHA256_BYTES) return false;

  const key = await importHmacKey(secret, ["verify"]);
  const payload = `${SIGNING_CONTEXT}.${expiresAtMs}`;
  return crypto.subtle.verify("HMAC", key, signature, encoder.encode(payload));
}

/**
 * Constant-time string equality for the password check: both inputs are
 * digested under one ephemeral HMAC key, and the fixed-length digests are
 * XOR-compared, so neither length nor a matching prefix leaks through timing.
 */
export async function constantTimeEquals(a: string, b: string): Promise<boolean> {
  const ephemeralKey = await crypto.subtle.importKey(
    "raw",
    crypto.getRandomValues(new Uint8Array(32)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const [digestA, digestB] = await Promise.all([
    crypto.subtle.sign("HMAC", ephemeralKey, encoder.encode(a)),
    crypto.subtle.sign("HMAC", ephemeralKey, encoder.encode(b)),
  ]);
  const bytesA = new Uint8Array(digestA);
  const bytesB = new Uint8Array(digestB);
  let difference = 0;
  for (let i = 0; i < bytesA.length; i += 1) difference |= bytesA[i] ^ bytesB[i];
  return difference === 0;
}

interface SessionCookie {
  name: string;
  value: string;
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

/** Cookie descriptor for NextResponse.cookies.set — httpOnly, secure, lax, 8h. */
export function sessionCookie(token: string): SessionCookie {
  return {
    name: SESSION_COOKIE_NAME,
    value: token,
    httpOnly: true,
    // `secure` everywhere except `next dev`, where the origin is plain http.
    secure: process.env.NODE_ENV !== "development",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  };
}

/** Expired variant of the same cookie — used by logout. */
export function clearedSessionCookie(): SessionCookie {
  return { ...sessionCookie(""), maxAge: 0 };
}
