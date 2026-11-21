// @vitest-environment node
//
// Two-tab refresh race (myday-handler.ts + myday-refresh-lock.ts), exercised
// through GET /api/my-day/me. Two requests carry the SAME session cookie, both
// get a backend 401 on the expired access token, and both need a refresh. The
// mock refresh behaves like the real single-winner backend: the first
// presentation of the old refresh token rotates it, any later one is refused
// with a plain 401.
//
// Two kinds of test, kept apart on purpose:
//   * CORRECTNESS ("without coalescing"): what the user gets when the in-process
//     single-flight does NOT apply, i.e. the requests reach different Vercel
//     instances or one arrives after the shared refresh settled. These
//     assertions must hold whatever myday-refresh-lock.ts does, because the
//     correctness boundary is the backend (design doc section 9.0.3).
//   * OPTIMISATION ("best-effort in-process coalescing"): when both requests
//     land in this ONE process while the refresh is in flight, they share one
//     upstream refresh. That saves a call and a re-login; it is not a
//     correctness boundary and nothing else relies on it.

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
 * with a token settles with `outcome` (only after `release` when `gated`); any
 * further call with the same token is refused with 401 (the token is consumed).
 */
function singleWinnerRefresh(
  outcome: () => Promise<typeof NEW>,
  { gated }: { gated: boolean },
): { release: () => void } {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  if (!gated) release();
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

const refusedWith401 = async (): Promise<typeof NEW> => {
  throw new MyDayApiError(401, "invalid refresh token");
};

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

describe("two-tab refresh race: correctness without coalescing (the backend is the boundary)", () => {
  it("uncoalesced race (another instance, or after the refresh settled): the winner keeps the new pair, the loser gets a deterministic 401 + cleared cookie", async () => {
    singleWinnerRefresh(async () => NEW, { gated: false });

    // Sequential, so the single-flight cannot apply: the first refresh has
    // settled and its entry is gone before the second request refreshes.
    const winner = await GET(await reqWith());
    expect(inFlightRefreshCount()).toBe(0);
    const loser = await GET(await reqWith());

    expect(winner.status).toBe(200);
    await expectResealedWithNewPair(winner);
    // The old token reached the backend twice; the second presentation was refused.
    expect(mydayRefresh).toHaveBeenCalledTimes(2);
    expect(mydayRefresh).toHaveBeenNthCalledWith(2, OLD.refresh_token);
    // The loser takes the plain refresh-401 path (step 4), never a 502.
    expect(loser.status).toBe(401);
    expectCleared(loser);
  });

  it.each(["concurrent", "sequential"] as const)(
    "a refresh refused with 401 ends every session with 401 + cleared cookie (%s requests)",
    async (mode) => {
      const { release } = singleWinnerRefresh(refusedWith401, { gated: mode === "concurrent" });

      let responses: NextResponse[];
      if (mode === "concurrent") {
        const [a, b] = await Promise.all([
          GET(await reqWith()),
          GET(await reqWith()),
          releaseWhenBothWaiting(release),
        ]);
        responses = [a, b];
      } else {
        responses = [await GET(await reqWith()), await GET(await reqWith())];
      }

      // Asserted on the OUTCOME only, never on how many upstream refreshes ran:
      // it is the same whether or not the requests were coalesced.
      for (const response of responses) {
        expect(response.status).toBe(401);
        expectCleared(response);
      }
      expect(inFlightRefreshCount()).toBe(0);
    },
  );
});

describe("two-tab refresh race: best-effort in-process coalescing (optimisation, not correctness)", () => {
  it("same process, refresh in flight: two 401->refresh flows share ONE upstream refresh and neither clears the cookie", async () => {
    const { release } = singleWinnerRefresh(async () => NEW, { gated: true });

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

  it("same process, shared refresh refused: every coalesced waiter gets the one 401 + cleared cookie", async () => {
    const { release } = singleWinnerRefresh(refusedWith401, { gated: true });

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
