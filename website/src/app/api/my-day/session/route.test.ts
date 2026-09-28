// @vitest-environment node
//
// Route tests for POST/DELETE /api/my-day/session — the My Day login/logout.
// The backend is MOCKED (via vi.mock of myday-client), so no real backend runs;
// the required env is stubbed in beforeEach.
//
// What these prove:
//   * a successful login returns 204 and sets an ENCRYPTED skylize_myday cookie
//     that decrypts (under the same test secret) to the REAL backend token pair
//     — i.e. the login path encrypts what the backend actually returned, not a
//     placeholder;
//   * bad credentials (backend 401) map to 401 "Invalid email or password."
//     with NO auth cookie set;
//   * a malformed body (missing password) is rejected at the edge with 400,
//     never reaching the backend;
//   * DELETE clears the cookie (maxAge 0) and returns 204.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Env the My Day config getter requires. SECRET must be >= 32 chars.
const MYDAY_SECRET = "test-myday-session-secret-00000000-32+chars";

// Mock the backend client. mydayLogin is what the POST handler calls; keep the
// real MyDayApiError class so the handler's `instanceof` branch still works.
const mydayLogin = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayLogin };
});

const { POST, DELETE } = await import("./route");
const { MyDayApiError } = await import("@/lib/skylize/myday-client");
const { MYDAY_SESSION_COOKIE_NAME, decryptSession, deriveMyDayKeyMaterial } =
  await import("@/lib/skylize/myday-session");

const BASE = "https://app.test/api/my-day/session";

function postReq(body: unknown): NextRequest {
  return new NextRequest(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function deleteReq(): NextRequest {
  return new NextRequest(BASE, { method: "DELETE" });
}

beforeEach(() => {
  mydayLogin.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  // NODE_ENV drives the cookie `secure` flag; pin it so assertions are stable.
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("POST /api/my-day/session (login)", () => {
  it("204s and sets an encrypted cookie holding the REAL backend pair", async () => {
    const pair = {
      access_token: "backend.access.jwt.for.a",
      refresh_token: "backend.refresh.jwt.for.a",
    };
    mydayLogin.mockResolvedValue(pair);

    const response = await POST(postReq({ email: "a@example.com", password: "hunter2pw" }));

    expect(response.status).toBe(204);
    expect(mydayLogin).toHaveBeenCalledWith("a@example.com", "hunter2pw");

    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie).toBeDefined();
    expect(cookie?.value.length).toBeGreaterThan(0);
    // The value must NOT be the raw token — it is ciphertext.
    expect(cookie?.value).not.toContain(pair.access_token);
    expect(cookie?.httpOnly).toBe(true);

    // Decrypt with the SAME secret the route used: proves the login path
    // encrypted exactly the pair the backend returned.
    const key = await deriveMyDayKeyMaterial(MYDAY_SECRET);
    const decrypted = await decryptSession(cookie!.value, key);
    expect(decrypted?.access_token).toBe(pair.access_token);
    expect(decrypted?.refresh_token).toBe(pair.refresh_token);
  });

  it("maps backend 401 to 401 'Invalid email or password.' and sets NO auth cookie", async () => {
    mydayLogin.mockRejectedValue(new MyDayApiError(401, "invalid credentials"));

    const response = await POST(postReq({ email: "a@example.com", password: "wrong" }));

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: "Invalid email or password.",
    });
    // No skylize_myday cookie with a value may be set on a failed login.
    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie?.value ?? "").toBe("");
  });

  it("rejects a malformed body (missing password) with 400, without calling the backend", async () => {
    const response = await POST(postReq({ email: "a@example.com" }));

    expect(response.status).toBe(400);
    expect(mydayLogin).not.toHaveBeenCalled();
  });

  it("rejects a non-email address with 400 at the edge", async () => {
    const response = await POST(postReq({ email: "not-an-email", password: "hunter2pw" }));

    expect(response.status).toBe(400);
    expect(mydayLogin).not.toHaveBeenCalled();
  });
});

describe("DELETE /api/my-day/session (logout)", () => {
  it("204s and clears the skylize_myday cookie (maxAge 0)", async () => {
    const response = await DELETE(deleteReq());

    expect(response.status).toBe(204);
    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie).toBeDefined();
    expect(cookie?.value).toBe("");
    expect(cookie?.maxAge).toBe(0);
    // logout must not touch the backend.
    expect(mydayLogin).not.toHaveBeenCalled();
  });
});
