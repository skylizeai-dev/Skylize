// @vitest-environment node
//
// MECHANISM tests for the in-process refresh single-flight
// (myday-refresh-lock.ts). They pin how the map behaves inside ONE process:
// coalescing, cleanup, no poisoning, raw token passed through. They prove
// nothing about correctness: the module is a best-effort optimisation, not a
// correctness boundary (docs/06_integrations/myday_session_revocation_design.md
// section 9.0.3); correctness is the backend's.

import { describe, expect, it, vi } from "vitest";

import { inFlightRefreshCount, refreshOnce, singleFlight } from "./myday-refresh-lock";

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("singleFlight (mechanism, best-effort; not a correctness test)", () => {
  it("same key while in flight: runs once, every caller gets the same result", async () => {
    const gate = deferred<string>();
    const run = vi.fn(() => gate.promise);

    const a = singleFlight("k", run);
    const b = singleFlight("k", run);
    expect(run).toHaveBeenCalledTimes(1);
    gate.resolve("pair");

    expect(await Promise.all([a, b])).toEqual(["pair", "pair"]);
  });

  it("different keys run independently", async () => {
    const run = vi.fn(async () => "x");
    await Promise.all([singleFlight("k1", run), singleFlight("k2", run)]);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("removes the entry after resolve, so a later call runs again", async () => {
    const run = vi.fn(async () => "x");
    await singleFlight("k", run);
    expect(inFlightRefreshCount()).toBe(0);
    await singleFlight("k", run);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("a rejection reaches every waiter, then the entry is removed (no poisoning)", async () => {
    const gate = deferred<string>();
    const failing = vi.fn(() => gate.promise);

    const a = singleFlight("k", failing);
    const b = singleFlight("k", failing);
    gate.reject(new Error("refresh 401"));

    await expect(a).rejects.toThrow("refresh 401");
    await expect(b).rejects.toThrow("refresh 401");
    expect(failing).toHaveBeenCalledTimes(1);
    expect(inFlightRefreshCount()).toBe(0);

    expect(await singleFlight("k", async () => "recovered")).toBe("recovered");
  });

  it("a synchronous throw is a rejection and still removes the entry", async () => {
    const boom = (): Promise<string> => {
      throw new Error("sync boom");
    };
    await expect(singleFlight("k", boom)).rejects.toThrow("sync boom");
    expect(inFlightRefreshCount()).toBe(0);
  });
});

describe("refreshOnce (mechanism, best-effort; not a correctness test)", () => {
  it("concurrent refreshes with the SAME token make one call, passing the raw token", async () => {
    const gate = deferred<{ access_token: string }>();
    const refresh = vi.fn((_token: string) => gate.promise);

    const a = refreshOnce("refresh-token-A", refresh);
    const b = refreshOnce("refresh-token-A", refresh);
    // Let both callers finish hashing and reach the map before releasing.
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    gate.resolve({ access_token: "new" });

    expect(await Promise.all([a, b])).toEqual([{ access_token: "new" }, { access_token: "new" }]);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith("refresh-token-A");
    expect(inFlightRefreshCount()).toBe(0);
  });

  it("different tokens are not coalesced", async () => {
    const refresh = vi.fn(async (token: string) => token.toUpperCase());
    expect(await Promise.all([refreshOnce("t1", refresh), refreshOnce("t2", refresh)])).toEqual([
      "T1",
      "T2",
    ]);
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
