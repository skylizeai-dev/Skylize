// @vitest-environment node
//
// OD21, login path: POST /api/my-day/session with a broken My Day config fails
// in mydayLogin -> myDayFetch -> getMyDayConfig (myday-client.ts) BEFORE any
// backend call, and must be logged under the same `myday_config_error` tag as
// the session-read path, answered 500, with no secret or password in the log.
// No module mocking: the real client runs, and fetch is spied to prove no
// request leaves the BFF.

import { NextRequest } from "next/server";
import { afterAll, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-login-cfg-secret-5555555-32+chars";
const PASSWORD = "pw-value-123-login";

// Break the config BEFORE the route (and so getMyDayConfig) is first used.
vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", MYDAY_SECRET);
vi.stubEnv("NODE_ENV", "test");

const { POST } = await import("./route");

afterAll(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("OD21 — login path config error", () => {
  it("the login path logs a config error under the same tag and answers 500", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const response = await POST(
      new NextRequest("https://app.test/api/my-day/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
        body: JSON.stringify({ email: "a@example.com", password: PASSWORD }),
      }),
    );

    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Internal server error." });
    expect(response.cookies.get("skylize_myday")).toBeUndefined();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(errorLog).toHaveBeenCalledTimes(1);
    expect(String(errorLog.mock.calls[0][0])).toContain("myday_config_error");
    const logged = JSON.stringify(errorLog.mock.calls);
    expect(logged).not.toContain(MYDAY_SECRET);
    expect(logged).not.toContain(PASSWORD);
  });
});
