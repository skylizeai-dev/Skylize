// My Day session cookie: `skylize_myday`.
//
// Unlike the Console cookie (signing-only HMAC over an expiry — session.ts),
// this cookie carries a REAL backend bearer credential (the user's access +
// refresh token pair). It therefore needs CONFIDENTIALITY, not just integrity,
// so its value is AES-256-GCM ciphertext. AES-GCM is authenticated encryption:
// it gives BOTH confidentiality (the bearer token is unreadable from the cookie)
// AND integrity (a tampered cookie fails the auth-tag check on decrypt, so
// `decryptSession` returns null and the caller fails closed to 401).
//
// Web Crypto only (no node:crypto), so the same code runs in the Next.js
// route-handler runtime and the proxy/edge runtime — the same constraint the
// Console cookie holds (session.ts:6-7).

export const MYDAY_SESSION_COOKIE_NAME = "skylize_myday";

/**
 * Cookie lifetime. The backend refresh token's default TTL is 14 days
 * (src/skylize/config.py:111); the access token's is 30 min
 * (src/skylize/config.py:110). The cookie should stay usable for as long as its
 * refresh token can still mint a fresh access token, so maxAge tracks the
 * refresh TTL — a shorter maxAge would log the user out while their refresh
 * token was still valid, and a longer one would leave a dead cookie the backend
 * will only reject. 14 days.
 */
export const MYDAY_SESSION_TTL_SECONDS = 14 * 24 * 60 * 60;

/** AES-256-GCM: 96-bit (12-byte) IV is the standard/recommended nonce length. */
const GCM_IV_BYTES = 12;
/** AES-256 needs exactly 32 bytes of key material. */
const AES_256_KEY_BYTES = 32;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/**
 * The plaintext packed into the cookie: the backend token pair plus an
 * issued-at (epoch ms) for observability / future rotation decisions.
 */
export interface MyDaySessionPayload {
  access_token: string;
  refresh_token: string;
  /** Issued-at, epoch milliseconds. Set by `encryptSession`. */
  iat: number;
}

/** What a caller supplies to `encryptSession`: just the token pair. */
export interface MyDayTokenPair {
  access_token: string;
  refresh_token: string;
}

// ── base64url helpers (modelled on session.ts:28-46) ────────────────────────

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

// ── key derivation and import ───────────────────────────────────────────────

/**
 * Derive exactly 32 bytes of AES-256 key material from the raw configured
 * secret via SHA-256. Coordinated with myday-config.ts, which validates the raw
 * secret is >= 32 chars of high-entropy data; SHA-256 then compresses it to the
 * fixed 32-byte width AES-256 requires regardless of the raw length. This is the
 * "derive a 32-byte key from the raw secret via SHA-256" option documented in
 * the design; it keeps operator configuration to a single opaque string.
 */
export async function deriveMyDayKeyMaterial(
  secret: string,
): Promise<Uint8Array<ArrayBuffer>> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(secret));
  return new Uint8Array(digest);
}

/**
 * Import 32-byte key material as a non-extractable AES-GCM CryptoKey.
 *
 * The `Uint8Array<ArrayBuffer>` annotation (not the bare `Uint8Array`, which
 * widens to `ArrayBufferLike`) is what `crypto.subtle.importKey`'s `BufferSource`
 * overload requires — the same reason session.ts pins its base64url output to
 * `Uint8Array<ArrayBuffer>`.
 */
async function importAesGcmKey(
  keyMaterial: Uint8Array<ArrayBuffer>,
  usages: KeyUsage[],
): Promise<CryptoKey> {
  if (keyMaterial.length !== AES_256_KEY_BYTES) {
    throw new Error(
      `My Day AES key material must be exactly ${AES_256_KEY_BYTES} bytes ` +
        `(received ${keyMaterial.length}).`,
    );
  }
  return crypto.subtle.importKey(
    "raw",
    keyMaterial,
    { name: "AES-GCM" },
    false,
    usages,
  );
}

// ── encrypt / decrypt ───────────────────────────────────────────────────────

/**
 * Encrypt the token pair into a cookie-value string.
 *
 * A fresh random 96-bit IV is generated per call and prepended to the AES-GCM
 * output (ciphertext with the 128-bit auth tag appended, per Web Crypto's
 * AES-GCM contract). The packed `iv || ciphertext+tag` byte string is then
 * base64url-encoded into the cookie value.
 */
export async function encryptSession(
  tokenPair: MyDayTokenPair,
  keyMaterial: Uint8Array<ArrayBuffer>,
): Promise<string> {
  const key = await importAesGcmKey(keyMaterial, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(GCM_IV_BYTES));

  const payload: MyDaySessionPayload = {
    access_token: tokenPair.access_token,
    refresh_token: tokenPair.refresh_token,
    iat: Date.now(),
  };
  const plaintext = encoder.encode(JSON.stringify(payload));

  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext),
  );

  const packed = new Uint8Array(iv.length + ciphertext.length);
  packed.set(iv, 0);
  packed.set(ciphertext, iv.length);
  return toBase64Url(packed);
}

/**
 * Decrypt a cookie value back to the session payload, or `null` on ANY failure:
 * a bad shape, un-decodable base64, a too-short packed buffer, a GCM auth-tag
 * mismatch (tamper), or a JSON body that is not a well-formed token pair.
 *
 * This NEVER throws to the caller. Every failure mode collapses to null so the
 * caller can uniformly fail closed (clear the cookie, return 401). GCM's auth
 * tag makes tamper detection automatic — a flipped bit anywhere in the packed
 * bytes fails `crypto.subtle.decrypt`, which we catch.
 */
export async function decryptSession(
  cookieValue: string,
  keyMaterial: Uint8Array<ArrayBuffer>,
): Promise<MyDaySessionPayload | null> {
  const packed = fromBase64Url(cookieValue);
  // Need at least the IV plus a non-empty GCM output (16-byte tag minimum).
  if (packed === null || packed.length <= GCM_IV_BYTES) return null;

  const iv = packed.subarray(0, GCM_IV_BYTES);
  const ciphertext = packed.subarray(GCM_IV_BYTES);

  let plaintext: ArrayBuffer;
  try {
    const key = await importAesGcmKey(keyMaterial, ["decrypt"]);
    plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      ciphertext,
    );
  } catch {
    // Auth-tag failure (tamper), wrong key, or malformed input — all fail closed.
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(decoder.decode(plaintext));
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      typeof (parsed as MyDaySessionPayload).access_token !== "string" ||
      typeof (parsed as MyDaySessionPayload).refresh_token !== "string"
    ) {
      return null;
    }
    const value = parsed as Partial<MyDaySessionPayload> & MyDayTokenPair;
    return {
      access_token: value.access_token,
      refresh_token: value.refresh_token,
      iat: typeof value.iat === "number" ? value.iat : 0,
    };
  } catch {
    return null;
  }
}

// ── cookie descriptors (mirroring session.ts:105-132) ───────────────────────

interface MyDaySessionCookie {
  name: string;
  value: string;
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

/**
 * Cookie descriptor for NextResponse.cookies.set — httpOnly, secure (except in
 * `next dev`, where the origin is plain http), lax, path "/", 14-day maxAge.
 */
export function mydaySessionCookie(value: string): MyDaySessionCookie {
  return {
    name: MYDAY_SESSION_COOKIE_NAME,
    value,
    httpOnly: true,
    secure: process.env.NODE_ENV !== "development",
    sameSite: "lax",
    path: "/",
    maxAge: MYDAY_SESSION_TTL_SECONDS,
  };
}

/** Expired variant of the same cookie — used by logout / fail-closed clears. */
export function clearedMydaySessionCookie(): MyDaySessionCookie {
  return { ...mydaySessionCookie(""), maxAge: 0 };
}
