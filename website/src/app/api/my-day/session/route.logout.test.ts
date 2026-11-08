// @vitest-environment node
//
// DELETE /api/my-day/session — OD20 logout: the cookie is ALWAYS cleared; the
// response says whether the server-side session was ended (204, FULL) or not
// (200 + partial body, PARTIAL). OD20 supersedes the interim T2 "502 and keep
// the cookie" behaviour (2b6bfbe). The backend is MOCKED via vi.mock of
// myday-client; clearedMydaySessionCookie is wrapped in a call-through spy so
// the ORDER "revoke, then clear" is observable.
//
// The config-error logout case (partial + `myday_config_error`) lives in
// app/api/my-day/route.config-error.test.ts, which needs fresh module state.

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
const { MYDAY_LOGOUT_PARTIAL_TAG, MYDAY_PARTIAL_LOGOUT_BODY } = await import(
  "@/lib/skylize/myday-logout"
);

const URL_ = "https://app.test/api/my-day/session";
const PAIR = { access_token: "acc.jwt.value", refresh_token: "ref.jwt.secret-value" };

const FAILURES = [
  ["backend 5xx", new MyDayApiError(500, "Backend error (HTTP 500).")],
  ["backend 503", new MyDayApiError(503, "Backend error (HTTP 503).")],
  ["network failure", new MyDayApiError(502, "Backend unreachable.")],
  ["timeout", new MyDayApiError(504, "Backend request timed out after 10000ms.")],
  ["unexpected error", new Error("boom")],
] as const;

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

describe("DELETE /api/my-day/session — OD20 always-clear logout", () => {
  it("revocation succeeds -> full logout, cookie cleared, unchanged behavior", async () => {
    mydayLogout.mockResolvedValue(undefined);

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
    expect(mydayLogout).toHaveBeenCalledTimes(1);
    expect(mydayLogout).toHaveBeenCalledWith(PAIR.refresh_token);
    expectCleared(response);
    // Revoke strictly BEFORE the cookie is cleared.
    expect(clearedSpy).toHaveBeenCalled();
    const lastLogout = Math.max(...mydayLogout.mock.invocationCallOrder);
    const firstClear = Math.min(...clearedSpy.mock.invocationCallOrder);
    expect(lastLogout).toBeLessThan(firstClear);
  });

  it("revocation fails once then succeeds -> full logout (204), 2 calls", async () => {
    mydayLogout
      .mockRejectedValueOnce(new MyDayApiError(500, "Backend error (HTTP 500)."))
      .mockResolvedValueOnce(undefined);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await DELETE(logoutReq(await sealed()));

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
    expect(mydayLogout).toHaveBeenCalledTimes(2);
    expectCleared(response);
    expect(errorLog).not.toHaveBeenCalled();
  });

  it.each(FAILURES)(
    "revocation fails (5xx) -> cookie IS cleared AND the response marks the logout partial [%s]",
    async (_label, failure) => {
      mydayLogout.mockRejectedValue(failure);
      vi.spyOn(console, "error").mockImplementation(() => {});
      vi.spyOn(console, "warn").mockImplementation(() => {});

      const response = await DELETE(logoutReq(await sealed()));

      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({
        logout: "partial",
        serverSessionEnded: false,
        message: "Signed out on this device; we could not end the session on the server.",
      });
      expect(MYDAY_PARTIAL_LOGOUT_BODY.logout).toBe("partial");
      expectCleared(response);
      expect(mydayLogout).toHaveBeenCalledTimes(2); // first attempt + exactly one retry
      expect(mydayLogout).toHaveBeenNthCalledWith(2, PAIR.refresh_token);
    },
  );

  it.each(FAILURES)(
    "revocation fails -> the partial case is logged [%s]",
    async (_label, failure) => {
      mydayLogout.mockRejectedValue(failure);
      const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
      const warnLog = vi.spyOn(console, "warn").mockImplementation(() => {});

      await DELETE(logoutReq(await sealed()));

      expect(errorLog).toHaveBeenCalledTimes(1);
      expect(String(errorLog.mock.calls[0][0])).toContain(MYDAY_LOGOUT_PARTIAL_TAG);
      expect(errorLog.mock.calls[0][1]).toEqual({
        attempts: 2,
        status: failure instanceof MyDayApiError ? failure.status : "unknown",
      });
      const logged = JSON.stringify([...errorLog.mock.calls, ...warnLog.mock.calls]);
      expect(logged).not.toContain(PAIR.refresh_token);
      expect(logged).not.toContain(PAIR.access_token);
      expect(logged).not.toContain(MYDAY_SECRET);
      expect(logged).not.toContain("myday_config_error");
    },
  );

  it("no cookie -> full logout (204), cookie cleared, no backend call", async () => {
    const response = await DELETE(logoutReq());
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("undecryptable cookie with a valid config -> full logout (204), no backend call", async () => {
    const response = await DELETE(logoutReq("not-a-real-ciphertext"));
    expect(response.status).toBe(204);
    expect(mydayLogout).not.toHaveBeenCalled();
    expectCleared(response);
  });

  it("cookie sealed under a different key -> full logout (204), no backend call", async () => {
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
