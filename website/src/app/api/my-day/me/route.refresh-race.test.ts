// @vitest-environment node
//
// Two-tab refresh race (myday-handler.ts + myday-refresh-lock.ts), exercised
// through GET /api/my-day/me. Two concurrent requests carry the SAME session
// cookie, both get a backend 401 on the expired access token, and both need a
// refresh. The backend rotation is single-winner, so the mock refresh refuses
// any second presentation of the old refresh token with 401, as the real backend
// does. With the in-process single-flight there is exactly ONE upstream refresh
// and neither response clears the cookie.
//
// Scope: this proves ONE Node process. Multi-instance behaviour is not tested.

import { NextRequest } from "next/server";
import type { NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-race-secret-3333333333-32+chars";

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
const { inFlightRefreshCount } = await import("@/lib/skylize/myday-refresh-lock");

const BASE = "https://app.test/api/my-day/me";
const OLD = { access_token: "old-access-expired", refresh_token: "old-refresh-shared" };
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

function expectCleared(response: NextResponse): void {
  const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
  expect(cookie?.value).toBe("");
  expect(cookie?.maxAge).toBe(0);
}

/**
 * A refresh mock that behaves like the single-winner backend: the FIRST call
 * with a token waits on `release` and then settles with `outcome`; any further
 * call with the same token is refused with 401 (the token is consumed).
 */
function singleWinnerRefresh(outcome: () => Promise<typeof NEW>): { release: () => void } {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let presented = false;
  mydayRefresh.mockImplementation(async () => {
    if (presented) throw new MyDayApiError(401, "refresh token already consumed");
    presented = true;
    await gate;
    return outcome();
  });
  return { release };
}

/** Release the shared refresh only after BOTH requests got their first 401. */
async function releaseWhenBothWaiting(release: () => void): Promise<void> {
  await vi.waitFor(() => expect(mydayGet).toHaveBeenCalledTimes(2));
  await vi.waitFor(() => expect(mydayRefresh).toHaveBeenCalled());
  // Give the second request time to hash its token and join the in-flight entry.
  await new Promise((r) => setTimeout(r, 50));
  release();
}

beforeEach(() => {
  mydayGet.mockReset();
  mydayRefresh.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
  mydayGet.mockImplementation(async (_path: string, token: string) => {
    if (token === OLD.access_token) throw new MyDayApiError(401, "expired");
    return USER;
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("two-tab refresh race (single-flight, one Node process)", () => {
  it("two concurrent 401->refresh flows make exactly ONE upstream refresh; neither clears the cookie", async () => {
    const { release } = singleWinnerRefresh(async () => NEW);

    const [a, b] = await Promise.all([
      GET(await reqWith()),
      GET(await reqWith()),
      releaseWhenBothWaiting(release),
    ]);

    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    expect(mydayRefresh).toHaveBeenCalledWith(OLD.refresh_token);
    for (const response of [a, b]) {
      expect(response.status).toBe(200);
      await expectResealedWithNewPair(response);
    }
    // Both retried with the NEW access token.
    const retryTokens = mydayGet.mock.calls.map((call) => call[1]);
    expect(retryTokens.filter((t) => t === NEW.access_token)).toHaveLength(2);
    expect(inFlightRefreshCount()).toBe(0);
  });

  it("a shared refresh refused with 401 ends BOTH sessions exactly like a single refresh would", async () => {
    const { release } = singleWinnerRefresh(async () => {
      throw new MyDayApiError(401, "invalid refresh token");
    });

    const [a, b] = await Promise.all([
      GET(await reqWith()),
      GET(await reqWith()),
      releaseWhenBothWaiting(release),
    ]);

    expect(mydayRefresh).toHaveBeenCalledTimes(1);
    for (const response of [a, b]) {
      expect(response.status).toBe(401);
      expectCleared(response);
    }
    expect(inFlightRefreshCount()).toBe(0);
  });
});
