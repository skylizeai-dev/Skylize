// @vitest-environment node
//
// getMyDayConfig validation (P03(d)): the My Day cookie secret must be long
// enough AND distinct from the Console cookie secret. Error messages must never
// echo either secret's value.
//
// getMyDayConfig memoises its result at module scope, so every case re-imports
// a fresh copy of the module after vi.resetModules().

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "myday-config-test-secret-aaaaaaaaaaaaaaaaaaaa";
const CONSOLE_SECRET = "console-config-test-secret-bbbbbbbbbbbbbbbbbbbb";

async function freshGetMyDayConfig() {
  vi.resetModules();
  const mod = await import("./myday-config");
  return mod.getMyDayConfig;
}

function thrownMessage(fn: () => unknown): string {
  try {
    fn();
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  throw new Error("expected getMyDayConfig to throw");
}

beforeEach(() => {
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("getMyDayConfig secret validation", () => {
  it("throws when the My Day and Console secrets are identical, naming neither value", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", MYDAY_SECRET);
    const getMyDayConfig = await freshGetMyDayConfig();

    const message = thrownMessage(getMyDayConfig);
    expect(message).toMatch(/must not equal SKYLIZE_CONSOLE_SESSION_SECRET/);
    expect(message).not.toContain(MYDAY_SECRET);
  });

  it("treats secrets that differ only by surrounding whitespace as identical", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", `  ${MYDAY_SECRET}\n`);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", `\t${MYDAY_SECRET}  `);
    const getMyDayConfig = await freshGetMyDayConfig();

    const message = thrownMessage(getMyDayConfig);
    expect(message).toMatch(/must not equal/);
    expect(message).not.toContain(MYDAY_SECRET);
  });

  it("throws on a short My Day secret, naming no value", async () => {
    const short = "too-short-secret";
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", short);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", CONSOLE_SECRET);
    const getMyDayConfig = await freshGetMyDayConfig();

    const message = thrownMessage(getMyDayConfig);
    expect(message).toMatch(/at least 32/);
    expect(message).not.toContain(short);
    expect(message).not.toContain(CONSOLE_SECRET);
  });

  it("passes with distinct valid secrets", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", CONSOLE_SECRET);
    const getMyDayConfig = await freshGetMyDayConfig();

    const config = getMyDayConfig();
    expect(config.cookieSecret).toBe(MYDAY_SECRET);
    expect(config.backendUrl).toBe("https://backend.test");
  });

  it("passes when the Console secret is unset (Console enforces its own presence)", async () => {
    vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
    vi.stubEnv("SKYLIZE_CONSOLE_SESSION_SECRET", undefined);
    const getMyDayConfig = await freshGetMyDayConfig();

    expect(getMyDayConfig().cookieSecret).toBe(MYDAY_SECRET);
  });
});
