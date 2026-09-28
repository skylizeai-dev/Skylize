// @vitest-environment node
//
// Unit tests for the My Day session crypto (myday-session.ts). No backend, no
// env, no infra — pure Web Crypto round-trips and the fail-closed guarantees
// that the whole My Day identity path leans on (design section 5.0).
//
// The two load-bearing properties proven here:
//   * CONFIDENTIALITY + INTEGRITY: AES-256-GCM, a fresh IV per call, and a
//     tampered/short/garbage cookie decrypts to `null` rather than throwing.
//   * CROSS-SECRET ISOLATION: a cookie minted under secret X is UNREADABLE
//     under secret Y (decrypt returns null). A per-tenant/per-deployment secret
//     is therefore a hard cryptographic boundary, not a convention.

import { describe, expect, it } from "vitest";

import {
  MYDAY_SESSION_COOKIE_NAME,
  MYDAY_SESSION_TTL_SECONDS,
  clearedMydaySessionCookie,
  decryptSession,
  deriveMyDayKeyMaterial,
  encryptSession,
  mydaySessionCookie,
} from "./myday-session";

const SECRET_X = "test-myday-secret-XXXXXXXXXXXXXXXXXXXXX-32+chars";
const SECRET_Y = "test-myday-secret-YYYYYYYYYYYYYYYYYYYYY-32+chars-different";

const PAIR = {
  access_token: "access.jwt.value.for.user.a",
  refresh_token: "refresh.jwt.value.for.user.a",
};

async function keyFor(secret: string): Promise<Uint8Array<ArrayBuffer>> {
  return deriveMyDayKeyMaterial(secret);
}

describe("deriveMyDayKeyMaterial", () => {
  it("produces exactly 32 bytes (AES-256 key width) from any secret", async () => {
    const key = await keyFor(SECRET_X);
    expect(key).toBeInstanceOf(Uint8Array);
    expect(key.length).toBe(32);
  });

  it("is deterministic for the same secret and diverges for a different one", async () => {
    const a1 = await keyFor(SECRET_X);
    const a2 = await keyFor(SECRET_X);
    const b = await keyFor(SECRET_Y);
    expect(Array.from(a1)).toEqual(Array.from(a2));
    expect(Array.from(a1)).not.toEqual(Array.from(b));
  });
});

describe("encrypt/decrypt round-trip", () => {
  it("recovers the exact token pair and a numeric iat", async () => {
    const key = await keyFor(SECRET_X);
    const cookieValue = await encryptSession(PAIR, key);
    const decrypted = await decryptSession(cookieValue, key);

    expect(decrypted).not.toBeNull();
    expect(decrypted?.access_token).toBe(PAIR.access_token);
    expect(decrypted?.refresh_token).toBe(PAIR.refresh_token);
    expect(typeof decrypted?.iat).toBe("number");
  });

  it("uses a FRESH IV per call: two encryptions of the same pair differ", async () => {
    // Distinct ciphertexts prove the 12-byte IV is regenerated each call, not
    // fixed — reusing an IV under one key would be a catastrophic GCM misuse.
    const key = await keyFor(SECRET_X);
    const c1 = await encryptSession(PAIR, key);
    const c2 = await encryptSession(PAIR, key);
    expect(c1).not.toBe(c2);
    // Both still decrypt correctly to the same plaintext.
    expect((await decryptSession(c1, key))?.access_token).toBe(PAIR.access_token);
    expect((await decryptSession(c2, key))?.access_token).toBe(PAIR.access_token);
  });
});

describe("fail-closed: decryptSession returns null (never throws)", () => {
  it("rejects tampered ciphertext (auth-tag mismatch)", async () => {
    const key = await keyFor(SECRET_X);
    const cookieValue = await encryptSession(PAIR, key);

    // Flip one character in the middle of the base64url value: any bit change
    // in iv||ct+tag makes the GCM auth tag fail.
    const mid = Math.floor(cookieValue.length / 2);
    const orig = cookieValue[mid];
    const swap = orig === "A" ? "B" : "A";
    const tampered = cookieValue.slice(0, mid) + swap + cookieValue.slice(mid + 1);
    expect(tampered).not.toBe(cookieValue);

    await expect(decryptSession(tampered, key)).resolves.toBeNull();
  });

  it("rejects a cookie minted under a DIFFERENT secret (cross-secret isolation)", async () => {
    // The security property that makes the My Day secret a real boundary: a
    // value encrypted under X cannot be read under Y. This is what stops one
    // deployment/tenant secret from ever unlocking another's cookie.
    const keyX = await keyFor(SECRET_X);
    const keyY = await keyFor(SECRET_Y);
    const cookieValue = await encryptSession(PAIR, keyX);

    await expect(decryptSession(cookieValue, keyY)).resolves.toBeNull();
    // Sanity: the same value IS readable under its own key, so the null above
    // is the wrong-key rejection and not a broken cookie.
    await expect(decryptSession(cookieValue, keyX)).resolves.not.toBeNull();
  });

  it("rejects malformed base64url, empty string, and a too-short buffer", async () => {
    const key = await keyFor(SECRET_X);
    // `!!!!` and `@@@@` are not valid base64url; empty is empty; "AAAA"
    // decodes to 3 bytes, far shorter than IV(12)+tag(16).
    for (const bad of ["", "!!!!", "@@@@@@", "AAAA", "A"]) {
      await expect(decryptSession(bad, key)).resolves.toBeNull();
    }
  });

  it("rejects a validly-encrypted payload whose JSON is NOT a token pair", async () => {
    // Craft a genuinely GCM-valid cookie whose plaintext is a well-formed JSON
    // object lacking access_token/refresh_token, exercising the shape guard
    // AFTER a successful decrypt (not the auth-tag path). Reuses the module's
    // own key derivation so the crypto matches decryptSession exactly.
    const key = await keyFor(SECRET_X);
    const importedKey = await crypto.subtle.importKey(
      "raw",
      key,
      { name: "AES-GCM" },
      false,
      ["encrypt"],
    );
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const notAPair = new TextEncoder().encode(
      JSON.stringify({ hello: "world", nested: { a: 1 } }),
    );
    const ct = new Uint8Array(
      await crypto.subtle.encrypt({ name: "AES-GCM", iv }, importedKey, notAPair),
    );
    const packed = new Uint8Array(iv.length + ct.length);
    packed.set(iv, 0);
    packed.set(ct, iv.length);
    // base64url encode exactly as the module does.
    let binary = "";
    for (const b of packed) binary += String.fromCharCode(b);
    const cookieValue = btoa(binary)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    // Decrypts cleanly, but the shape guard rejects it -> null, not a throw.
    await expect(decryptSession(cookieValue, key)).resolves.toBeNull();
  });

  it("rejects an access_token/refresh_token that are present but not strings", async () => {
    // The guard checks the field TYPES, not just presence — a numeric
    // access_token must still fail closed.
    const key = await keyFor(SECRET_X);
    const importedKey = await crypto.subtle.importKey(
      "raw",
      key,
      { name: "AES-GCM" },
      false,
      ["encrypt"],
    );
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const wrongTypes = new TextEncoder().encode(
      JSON.stringify({ access_token: 123, refresh_token: true }),
    );
    const ct = new Uint8Array(
      await crypto.subtle.encrypt({ name: "AES-GCM", iv }, importedKey, wrongTypes),
    );
    const packed = new Uint8Array(iv.length + ct.length);
    packed.set(iv, 0);
    packed.set(ct, iv.length);
    let binary = "";
    for (const b of packed) binary += String.fromCharCode(b);
    const cookieValue = btoa(binary)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    await expect(decryptSession(cookieValue, key)).resolves.toBeNull();
  });
});

describe("cookie descriptors", () => {
  it("mydaySessionCookie carries the fail-closed posture the design mandates", () => {
    const descriptor = mydaySessionCookie("some-encrypted-value");
    expect(descriptor.name).toBe(MYDAY_SESSION_COOKIE_NAME);
    expect(descriptor.name).toBe("skylize_myday");
    expect(descriptor.value).toBe("some-encrypted-value");
    expect(descriptor.httpOnly).toBe(true);
    expect(descriptor.sameSite).toBe("lax");
    expect(descriptor.path).toBe("/");
    expect(descriptor.maxAge).toBe(MYDAY_SESSION_TTL_SECONDS);
  });

  it("clearedMydaySessionCookie is the same cookie with maxAge 0", () => {
    const cleared = clearedMydaySessionCookie();
    expect(cleared.name).toBe("skylize_myday");
    expect(cleared.maxAge).toBe(0);
    expect(cleared.httpOnly).toBe(true);
    expect(cleared.sameSite).toBe("lax");
    expect(cleared.path).toBe("/");
  });
});
