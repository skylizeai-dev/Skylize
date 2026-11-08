// @vitest-environment node
//
// OD21: a My Day CONFIG error must never surface as a silent 401. It is logged
// under `myday_config_error` (message only, never a secret), answered 500, and
// the user's cookie is NOT cleared. A normal tampered/expired cookie under a
// VALID config stays 401 + clear and never carries the config tag.
// OD20 edge: a logout whose config is broken cannot attempt revocation, so it
// is a PARTIAL logout (200 + partial body + cleared cookie), tagged as a config
// error — never a 204 "full" logout while a live token may exist server-side.
//
// getMyDayConfig memoises a SUCCESSFUL config at module scope, so every case
// re-imports fresh modules after vi.resetModules().

import { NextRequest } from "next/server";
import type { NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-config-err-secret-33333333-32+chars";
const CONSOLE_SECRET = "test-console-config-err-secret-4444444-32+chars";
const SHORT_SECRET = "short-secret-xyz";
const PAIR = { access_token: "cfg.access.token", refresh_token: "cfg.refresh.token" };
const CONFIG_TAG = "myday_config_error";

const mydayGet = vi.fn();
const mydayRefresh = vi.fn();
const mydayLogout = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayGet, mydayRefresh, mydayLogout };
});

async function freshModules() {
  vi.resetModules();
  const me = await import("./me/route");
  const session = await import("./session/route");
  const cookie = await import("@/lib/skylize/myday-session");
  return { GET: me.GET, DELETE: session.DELETE, ...cookie };
}

/** A cookie sealed directly with a key (sealing needs no config). */
async function sealedCookie(secret = MYDAY_SECRET): Promise<string> {
  const { encryptSession, deriveMyDayKeyMaterial } = await import(
    "@/lib/skylize/myday-session"
  );
  return encryptSession(PAIR, await deriveMyDayKeyMaterial(secret));
}

function req(url: string, method: "GET" | "DELETE", cookie?: string): NextRequest {
  const headers: Record<string, string> = { "Sec-Fetch-Site": "same-origin" };
  if (cookie !== undefined) headers.Cookie = `skylize_myday=${cookie}`;
  return new NextRequest(url, { method, headers });
}

function loggedText(spy: { mock: { calls: unknown[][] } }): string {
  return JSON.stringify(spy.mock.calls);
}

function expectNoSecretIn(text: string): void {
  for (const s of [MYDAY_SECRET, CONSOLE_SECRET, SHORT_SECRET, PAIR.access_token, PAIR.refresh_token]) {
    expect(text).not.toContain(s);
  }
}

function expectCleared(response: NextResponse): void {
  const cookie = response.cookies.get("skylize_myday");
  expect(cookie?.value).toBe("");
  expect(cookie?.maxAge).toBe(0);
}

beforeEach(() => {
  mydayGet.mockReset();
  mydayRefresh.mockReset();
  mydayLogout.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("NODE_ENV", "test");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const BROKEN_CONFIGS = [
  [
    "identical My Day and Console secrets",
    () => {
      vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
      vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", MYDAY_SECRET);
    },
  ],
  [
    "short My Day secret",
    () => {
      vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", SHORT_SECRET);
      vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", CONSOLE_SECRET);
    },
  ],
] as const;

describe("OD21 — config errors are logged and distinguishable", () => {
  it.each(BROKEN_CONFIGS)(
    "a config error is logged and is distinguishable from a normal 401 [%s]",
    async (_label, breakConfig) => {
      breakConfig();
      const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
      const { GET } = await freshModules();

      const response = await GET(req("https://app.test/api/my-day/me", "GET", await sealedCookie()));

      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({ error: "Internal server error." });
      // The cookie is NOT touched: the user is not at fault.
      expect(response.cookies.get("skylize_myday")).toBeUndefined();
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(errorLog).toHaveBeenCalledTimes(1);
      expect(String(errorLog.mock.calls[0][0])).toContain(CONFIG_TAG);
      expectNoSecretIn(loggedText(errorLog));
      expect(mydayGet).not.toHaveBeenCalled();
    },
  );

  it("a tampered or foreign-key cookie with a VALID config is a 401 + clear, without the config tag", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", CONSOLE_SECRET);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    const warnLog = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { GET } = await freshModules();

    for (const cookie of [
      "tampered-not-a-ciphertext",
      await sealedCookie("a-different-secret-that-is-also-32-chars-long!"),
    ]) {
      const response = await GET(req("https://app.test/api/my-day/me", "GET", cookie));
      expect(response.status).toBe(401);
      expectCleared(response);
    }
    const allLogs = loggedText(errorLog) + loggedText(warnLog);
    expect(allLogs).not.toContain(CONFIG_TAG);
    expect(mydayGet).not.toHaveBeenCalled();
  });

  // The login path's config error is covered in session/route.config-login.test.ts:
  // vi.resetModules() does not reset a vi.mock'd module, so the mocked
  // myday-client here would stay bound to a stale myday-config instance.
});

describe("OD20 edge — logout with a config error is PARTIAL, never full", () => {
  it.each(BROKEN_CONFIGS)(
    "a logout with a config error gives the partial body + clear + config tag [%s]",
    async (_label, breakConfig) => {
      breakConfig();
      const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
      const { DELETE } = await freshModules();

      const response = await DELETE(
        req("https://app.test/api/my-day/session", "DELETE", await sealedCookie()),
      );

      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({
        logout: "partial",
        serverSessionEnded: false,
        message: "Signed out on this device; we could not end the session on the server.",
      });
      expectCleared(response);
      expect(mydayLogout).not.toHaveBeenCalled(); // revocation could not be attempted
      expect(errorLog).toHaveBeenCalledTimes(1);
      expect(String(errorLog.mock.calls[0][0])).toContain(CONFIG_TAG);
      expectNoSecretIn(loggedText(errorLog));
    },
  );

  it("a logout with a config error but NO cookie is still a full 204 (nothing to revoke)", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", SHORT_SECRET);
    const { DELETE } = await freshModules();

    const response = await DELETE(req("https://app.test/api/my-day/session", "DELETE"));

    expect(response.status).toBe(204);
    expectCleared(response);
  });
});
