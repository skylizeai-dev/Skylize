// @vitest-environment node
//
// DELETE /api/my-day/session — server-side logout (myday_session_revocation_design.md).
// The backend is MOCKED via vi.mock of myday-client.
//
// What these prove:
//   * logout sends the refresh token sealed INSIDE the cookie to the backend
//     (and nothing else), then clears the cookie with 204;
//   * when the backend call fails, the cookie is STILL cleared and the answer is
//     STILL 204, and the failure is logged at error level without the token
//     (owner decision, design 2.0.3);
//   * with no cookie, or one that does not decrypt, there is nothing to revoke:
//     no backend call, cookie cleared, 204.

import { NextRequest } from "next/server";
import type { NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-session-secret-00000000-32+chars";

const mydayLogout = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayLogout };
});

const { DELETE } = await import("./route");
const { MyDayApiError } = await import("@/lib/skylize/myday-client");
const { MYDAY_SESSION_COOKIE_NAME, deriveMyDayKeyMaterial, encryptSession } =
  await import("@/lib/skylize/myday-session");

const URL_ = "https://app.test/api/my-day/session";
const PAIR = { access_token: "acc.jwt.value", refresh_token: "ref.jwt.secret-value" };

async function sealed(pair = PAIR): Promise<string> {
  return encryptSession(pair, await deriveMyDayKeyMaterial(MYDAY_SECRET));
}

function logoutReq(cookie?: string): NextRequest {
  const headers: Record<string, string> = { "Sec-Fetch-Site": "same-origin" };
  if (cookie !== undefined) headers.Cookie = `${MYDAY_SESSION_COOKIE_NAME}=${cookie}`;
  return new NextRequest(URL_, { method: "DELETE", headers });
}

function expectCleared(response: NextResponse): void {
  const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
  expect(cookie?.value).toBe("");
  expect(cookie?.maxAge).toBe(0);
}

beforeEach(() => {
  mydayLogout.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("DELETE /api/my-day/session (server-side logout)", () => {
  it("revokes the sealed refresh token on the backend, then clears the cookie", async () => {
    mydayLogout.mockResolvedValue(undefined);

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expect(mydayLogout).toHaveBeenCalledTimes(1);
    expect(mydayLogout).toHaveBeenCalledWith(PAIR.refresh_token);
    expectCleared(response);
  });

  it.each([
    ["network failure", new MyDayApiError(502, "Backend unreachable.")],
    ["timeout", new MyDayApiError(504, "Backend request timed out after 10000ms.")],
    ["backend 500", new MyDayApiError(500, "Backend error (HTTP 500).")],
    ["unexpected error", new Error("boom")],
  ])("still clears the cookie and 204s on %s, and logs it", async (_label, failure) => {
    mydayLogout.mockRejectedValue(failure);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expectCleared(response);
    expect(errorLog).toHaveBeenCalledTimes(1);
    // The log line must never carry the refresh (or access) token.
    const logged = JSON.stringify(errorLog.mock.calls);
    expect(logged).toMatch(/server-side logout failed/);
    expect(logged).not.toContain(PAIR.refresh_token);
    expect(logged).not.toContain(PAIR.access_token);
  });

  it("makes no backend call when there is no cookie", async () => {
    const response = await DELETE(logoutReq());
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("makes no backend call when the cookie does not decrypt", async () => {
    const response = await DELETE(logoutReq("not-a-real-ciphertext"));
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("makes no backend call for a cookie sealed under a different key", async () => {
    const foreign = await encryptSession(
      PAIR,
      await deriveMyDayKeyMaterial("some-other-secret-of-at-least-32-characters!!"),
    );
    const response = await DELETE(logoutReq(foreign));
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
  });
});
