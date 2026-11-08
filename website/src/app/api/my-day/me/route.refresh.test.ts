// @vitest-environment node
//
// Refresh-on-401 (OD3-AMEND) for authenticated My Day routes, exercised through
// GET /api/my-day/me. The logic itself lives once in myday-handler.ts
// (`createAccessTokenRunner`); this route is its only current consumer.
//
// The backend is MOCKED via vi.mock of myday-client (mydayGet + mydayRefresh);
// the real MyDayApiError class is kept so status branches behave as in prod.

import { NextRequest } from "next/server";
import type { NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-refresh-secret-2222222222-32+chars";

const mydayGet = vi.fn();
const mydayRefresh = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayGet, mydayRefresh };
});

const { GET } = await import("./route");
const { MyDayApiError } = await import("@/lib/skylize/myday-client");
const { MYDAY_SESSION_COOKIE_NAME, decryptSession, deriveMyDayKeyMaterial, encryptSession } =
  await import("@/lib/skylize/myday-session");

const BASE = "https://app.test/api/my-day/me";
const OLD = { access_token: "old-access-expired", refresh_token: "old-refresh-token" };
const NEW = { access_token: "new-access-fresh", refresh_token: "new-refresh-rotated" };
const USER = {
  user_id: "user_a",
  org_id: "org_a",
  email: "a@example.com",
  display_name: null,
  roles: ["owner"],
  is_active: true,
};

async function reqWith(pair = OLD): Promise<NextRequest> {
  const cookie = await encryptSession(pair, await deriveMyDayKeyMaterial(MYDAY_SECRET));
  return new NextRequest(BASE, {
    method: "GET",
    headers: { cookie: `${MYDAY_SESSION_COOKIE_NAME}=${cookie}` },
  });
}

function expectCleared(response: NextResponse): void {
  const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
  expect(cookie?.value).toBe("");
  expect(cookie?.maxAge).toBe(0);
}

async function expectResealedWithNewPair(response: NextResponse): Promise<void> {
  const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
  expect(cookie?.value).toBeTruthy();
  expect(cookie?.maxAge).toBeGreaterThan(0);
  const payload = await decryptSession(
    cookie?.value ?? "",
    await deriveMyDayKeyMaterial(MYDAY_SECRET),
  );
  expect(payload?.access_token).toBe(NEW.access_token);
  expect(payload?.refresh_token).toBe(NEW.refresh_token);
}

beforeEach(() => {
  mydayGet.mockReset();
  mydayRefresh.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("refresh-on-401 (OD3-AMEND)", () => {
  it("401 -> refresh ok -> retry 200: 200 with the cookie re-sealed to the NEW pair", async () => {
    mydayGet
      .mockRejectedValueOnce(new MyDayApiError(401, "expired"))
      .mockResolvedValueOnce(USER);
    mydayRefresh.mockResolvedValueOnce(NEW);

    const response = await GET(await reqWith());

    expect(response.status).toBe(200);
    expect((await response.json()).user_id).toBe("user_a");
    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    expect(mydayRefresh).toHaveBeenCalledWith(OLD.refresh_token);
    expect(mydayGet).toHaveBeenCalledTimes(2);
    expect(mydayGet).toHaveBeenNthCalledWith(1, "/api/v1/auth/me", OLD.access_token);
    expect(mydayGet).toHaveBeenNthCalledWith(2, "/api/v1/auth/me", NEW.access_token);
    await expectResealedWithNewPair(response);
  });

  it("401 -> refresh ok -> retry 401: 401, cookie cleared, refresh called exactly once", async () => {
    mydayGet.mockRejectedValue(new MyDayApiError(401, "expired"));
    mydayRefresh.mockResolvedValue(NEW);

    const response = await GET(await reqWith());

    expect(response.status).toBe(401);
    expectCleared(response);
    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    expect(mydayGet).toHaveBeenCalledTimes(2);
  });

  it("401 -> refresh 401: 401 and cookie cleared, no retry of the original call", async () => {
    mydayGet.mockRejectedValue(new MyDayApiError(401, "expired"));
    mydayRefresh.mockRejectedValue(new MyDayApiError(401, "invalid refresh token"));

    const response = await GET(await reqWith());

    expect(response.status).toBe(401);
    expectCleared(response);
    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    expect(mydayGet).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["backend 500", new MyDayApiError(500, "Backend error (HTTP 500).")],
    ["network failure", new MyDayApiError(502, "Backend unreachable.")],
    ["timeout", new MyDayApiError(504, "Backend request timed out after 10000ms.")],
    ["unexpected error", new Error("boom")],
  ])("401 -> refresh fails (%s): 502 and cookie cleared, no token logged", async (_l, failure) => {
    mydayGet.mockRejectedValue(new MyDayApiError(401, "expired"));
    mydayRefresh.mockRejectedValue(failure);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await GET(await reqWith());

    expect(response.status).toBe(502);
    expectCleared(response);
    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    expect(mydayGet).toHaveBeenCalledTimes(1);
    const logged = JSON.stringify(errorLog.mock.calls);
    expect(logged).not.toContain(OLD.refresh_token);
    expect(logged).not.toContain(OLD.access_token);
  });

  it("401 -> refresh ok -> retry 500: the NEW pair still rides on the error response", async () => {
    mydayGet
      .mockRejectedValueOnce(new MyDayApiError(401, "expired"))
      .mockRejectedValueOnce(new MyDayApiError(500, "Backend error (HTTP 500)."));
    mydayRefresh.mockResolvedValueOnce(NEW);

    const response = await GET(await reqWith());

    expect(response.status).toBe(502);
    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    await expectResealedWithNewPair(response);
  });

  it.each([
    ["403", new MyDayApiError(403, "forbidden"), 401],
    ["404", new MyDayApiError(404, "not found"), 404],
    ["429", new MyDayApiError(429, "slow down"), 429],
    ["500", new MyDayApiError(500, "Backend error (HTTP 500)."), 502],
    ["504", new MyDayApiError(504, "timed out"), 504],
  ])("non-401 backend error (%s) does not trigger refresh", async (_l, failure, status) => {
    mydayGet.mockRejectedValue(failure);

    const response = await GET(await reqWith());

    expect(response.status).toBe(status);
    expect(mydayRefresh).not.toHaveBeenCalled();
    expect(mydayGet).toHaveBeenCalledTimes(1);
  });

  it("a 200 on the first try never refreshes and never re-sets the cookie", async () => {
    mydayGet.mockResolvedValueOnce(USER);

    const response = await GET(await reqWith());

    expect(response.status).toBe(200);
    expect(mydayRefresh).not.toHaveBeenCalled();
    expect(response.cookies.get(MYDAY_SESSION_COOKIE_NAME)).toBeUndefined();
  });
});
