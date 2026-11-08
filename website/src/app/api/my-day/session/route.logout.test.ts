// @vitest-environment node
//
// DELETE /api/my-day/session — server-side logout that FAILS CLOSED (owner
// instruction superseding the 2026-10-14 "always 204" decision). The backend is
// MOCKED via vi.mock of myday-client; clearedMydaySessionCookie is wrapped in a
// spy (calling through) so the ORDER "revoke, then clear" is observable.
//
// What these prove:
//   * logout sends the refresh token sealed INSIDE the cookie to the backend
//     BEFORE the cookie is cleared, then 204 + cleared cookie;
//   * a backend failure is retried exactly ONCE; a second failure is a 502 JSON
//     error with NO cookie clearing (so the client can retry), logged at error
//     level without the token;
//   * fail-once-then-succeed is a normal 204 + cleared cookie;
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

const clearedSpy = vi.fn();
vi.mock("@/lib/skylize/myday-session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-session")>();
  clearedSpy.mockImplementation(actual.clearedMydaySessionCookie);
  return { ...actual, clearedMydaySessionCookie: clearedSpy };
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
  clearedSpy.mockClear();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("DELETE /api/my-day/session (server-side logout, fail-closed)", () => {
  it("backend success: 204 + cleared cookie, and mydayLogout ran BEFORE the cookie was cleared", async () => {
    mydayLogout.mockResolvedValue(undefined);

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expect(mydayLogout).toHaveBeenCalledTimes(1);
    expect(mydayLogout).toHaveBeenCalledWith(PAIR.refresh_token);
    expectCleared(response);
    expect(clearedSpy).toHaveBeenCalled();
    const lastLogout = Math.max(...mydayLogout.mock.invocationCallOrder);
    const firstClear = Math.min(...clearedSpy.mock.invocationCallOrder);
    expect(lastLogout).toBeLessThan(firstClear);
  });

  it.each([
    ["network failure", new MyDayApiError(502, "Backend unreachable.")],
    ["timeout", new MyDayApiError(504, "Backend request timed out after 10000ms.")],
    ["backend 500", new MyDayApiError(500, "Backend error (HTTP 500).")],
    ["unexpected error", new Error("boom")],
  ])(
    "backend fails twice (%s): 502 JSON, NO cookie clearing, exactly 2 calls, error logged without the token",
    async (_label, failure) => {
      mydayLogout.mockRejectedValue(failure);
      const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
      const warnLog = vi.spyOn(console, "warn").mockImplementation(() => {});

      const response = await DELETE(logoutReq(await sealed()));

      expect(response.status).toBe(502);
      const body = await response.json();
      expect(typeof body.error).toBe("string");
      expect(JSON.stringify(body)).not.toContain(PAIR.refresh_token);
      expect(JSON.stringify(body)).not.toContain(PAIR.access_token);
      // No Set-Cookie at all: the cookie stays so the client can retry.
      expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)).toBeUndefined();
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(clearedSpy).not.toHaveBeenCalled();
      expect(mydayLogout).toHaveBeenCalledTimes(2);
      expect(mydayLogout).toHaveBeenNthCalledWith(1, PAIR.refresh_token);
      expect(mydayLogout).toHaveBeenNthCalledWith(2, PAIR.refresh_token);

      expect(errorLog).toHaveBeenCalledTimes(1);
      const logged = JSON.stringify([...errorLog.mock.calls, ...warnLog.mock.calls]);
      expect(logged).toMatch(/server-side logout failed/);
      expect(logged).not.toContain(PAIR.refresh_token);
      expect(logged).not.toContain(PAIR.access_token);
    },
  );

  it("backend fails once then succeeds: 204 + cleared cookie after 2 calls", async () => {
    mydayLogout
      .mockRejectedValueOnce(new MyDayApiError(500, "Backend error (HTTP 500)."))
      .mockResolvedValueOnce(undefined);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expect(mydayLogout).toHaveBeenCalledTimes(2);
    expectCleared(response);
    expect(errorLog).not.toHaveBeenCalled();
  });

  it("no cookie: 204 + cleared cookie and no backend call", async () => {
    const response = await DELETE(logoutReq());
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("undecryptable cookie: 204 + cleared cookie and no backend call", async () => {
    const response = await DELETE(logoutReq("not-a-real-ciphertext"));
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("cookie sealed under a different key: 204 and no backend call", async () => {
    const foreign = await encryptSession(
      PAIR,
      await deriveMyDayKeyMaterial("some-other-secret-of-at-least-32-characters!!"),
    );
    const response = await DELETE(logoutReq(foreign));
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });
});
