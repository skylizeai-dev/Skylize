// @vitest-environment node
//
// Login CSRF tests for /api/my-day/session (finding N1 of the PR #27 security
// review). The backend is MOCKED via vi.mock of myday-client, so a refused
// request can be proven never to reach the backend at all.
//
// The attack being reproduced: an attacker page submits
//   <form method="POST" action="https://<site>/api/my-day/session"
//         enctype="text/plain">
//     <input name='{"email":"attacker@evil.example","password":"hunter2'
//            value='x"}'>
//   </form>
// The browser sends the body `{"email":"attacker@evil.example","password":"hunter2=x"}`
// with Content-Type text/plain. `request.json()` ignores Content-Type, so before
// this guard the body parsed, passed the zod schema, and the 204's cookie logged
// the victim into the attacker's account.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-session-secret-00000000-32+chars";

const mydayLogin = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayLogin };
});

const { POST, DELETE } = await import("./route");
const { GET: GET_ME } = await import("../me/route");
const { MYDAY_SESSION_COOKIE_NAME } = await import("@/lib/skylize/myday-session");

const SITE = "https://app.test";
const URL_ = `${SITE}/api/my-day/session`;
const ATTACK_BODY = '{"email":"attacker@evil.example","password":"hunter2=x"}';
const PAIR = { access_token: "acc.jwt", refresh_token: "ref.jwt" };

function login(headers: Record<string, string>, body = ATTACK_BODY): NextRequest {
  return new NextRequest(URL_, { method: "POST", headers, body });
}

beforeEach(() => {
  mydayLogin.mockReset();
  mydayLogin.mockResolvedValue(PAIR);
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("login CSRF: the cross-site text/plain form POST is refused", () => {
  it("403s the exact attack, never calls the backend, sets no cookie", async () => {
    const response = await POST(
      login({
        "Content-Type": "text/plain",
        "Sec-Fetch-Site": "cross-site",
        Origin: "https://evil.example",
      }),
    );

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ error: "Cross-site request refused." });
    expect(mydayLogin).not.toHaveBeenCalled();
    expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)).toBeUndefined();
  });

  it("403s the same attack from a browser that sends only Origin", async () => {
    const response = await POST(
      login({ "Content-Type": "text/plain", Origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(mydayLogin).not.toHaveBeenCalled();
  });

  it("403s a request carrying neither header (fail closed)", async () => {
    const response = await POST(login({ "Content-Type": "application/json" }));
    expect(response.status).toBe(403);
    expect(mydayLogin).not.toHaveBeenCalled();
  });

  it("403s a sibling-subdomain (same-site) request", async () => {
    const response = await POST(login({ "Sec-Fetch-Site": "same-site" }));
    expect(response.status).toBe(403);
    expect(mydayLogin).not.toHaveBeenCalled();
  });
});

describe("same-origin requests still work", () => {
  it("logs in with Sec-Fetch-Site: same-origin", async () => {
    const response = await POST(
      login(
        { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
        JSON.stringify({ email: "a@example.com", password: "hunter2pw" }),
      ),
    );
    expect(response.status).toBe(204);
    expect(mydayLogin).toHaveBeenCalledWith("a@example.com", "hunter2pw");
    expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.value).toBeTruthy();
  });

  it("logs in with only a matching Origin (no Sec-Fetch-Site)", async () => {
    const response = await POST(
      login(
        { "Content-Type": "application/json", Origin: SITE },
        JSON.stringify({ email: "a@example.com", password: "hunter2pw" }),
      ),
    );
    expect(response.status).toBe(204);
  });
});

describe("logout and reads", () => {
  it("403s a cross-site DELETE WITHOUT clearing the cookie (no logout CSRF)", async () => {
    const response = await DELETE(
      new NextRequest(URL_, { method: "DELETE", headers: { "Sec-Fetch-Site": "cross-site" } }),
    );
    expect(response.status).toBe(403);
    // A cleared cookie here would let any page log the victim out.
    expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)).toBeUndefined();
  });

  it("same-origin DELETE still clears the cookie", async () => {
    const response = await DELETE(
      new NextRequest(URL_, { method: "DELETE", headers: { "Sec-Fetch-Site": "same-origin" } }),
    );
    expect(response.status).toBe(204);
    expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)?.maxAge).toBe(0);
  });

  it("GET /api/my-day/me is not subject to the guard", async () => {
    // No session cookie: the auth check answers 401, proving the request got
    // past the method-gated guard instead of being 403'd for missing headers.
    const response = await GET_ME(
      new NextRequest(`${SITE}/api/my-day/me`, {
        method: "GET",
        headers: { "Sec-Fetch-Site": "cross-site" },
      }),
    );
    expect(response.status).toBe(401);
  });
});
