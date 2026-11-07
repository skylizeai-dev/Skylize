// @vitest-environment node
//
// Unit tests for the My Day cross-site guard (myday-csrf.ts), plus the proof
// that Console is untouched: Console's route wrapper still reaches its handler
// on a cross-site request, and no Console module imports the guard.

import { readFileSync } from "node:fs";
import path from "node:path";

import { NextRequest, NextResponse } from "next/server";
import { describe, expect, it } from "vitest";

import { consoleRoute } from "@/lib/skylize/handler";
import { isStateChangingMethod, verifySameOrigin } from "@/lib/skylize/myday-csrf";

const URL_ = "https://app.test/api/my-day/session";

function req(headers: Record<string, string>, method = "POST"): NextRequest {
  return new NextRequest(URL_, { method, headers });
}

describe("verifySameOrigin", () => {
  it("accepts Sec-Fetch-Site: same-origin", () => {
    expect(verifySameOrigin(req({ "Sec-Fetch-Site": "same-origin" }))).toEqual({ ok: true });
  });

  it.each(["cross-site", "same-site", "none"])("refuses Sec-Fetch-Site: %s", (value) => {
    expect(verifySameOrigin(req({ "Sec-Fetch-Site": value })).ok).toBe(false);
  });

  it("trusts Sec-Fetch-Site over a matching Origin", () => {
    // A cross-site fetch cannot make the browser report same-origin, so the
    // Origin header is irrelevant once Sec-Fetch-Site is present.
    const verdict = verifySameOrigin(
      req({ "Sec-Fetch-Site": "cross-site", Origin: "https://app.test" }),
    );
    expect(verdict.ok).toBe(false);
  });

  it("falls back to Origin and accepts this request's own origin", () => {
    expect(verifySameOrigin(req({ Origin: "https://app.test" }))).toEqual({ ok: true });
  });

  it.each([
    "https://evil.example",
    "http://app.test", // scheme differs
    "https://app.test:8443", // port differs
    "https://sub.app.test", // sibling subdomain
    "null", // opaque origin (sandboxed iframe, file:)
  ])("refuses a mismatched Origin: %s", (origin) => {
    expect(verifySameOrigin(req({ Origin: origin })).ok).toBe(false);
  });

  it("fails closed when neither header is present", () => {
    expect(verifySameOrigin(req({})).ok).toBe(false);
  });
});

describe("isStateChangingMethod", () => {
  it.each(["POST", "PUT", "PATCH", "DELETE", "post"])("%s changes state", (m) => {
    expect(isStateChangingMethod(m)).toBe(true);
  });

  it.each(["GET", "HEAD", "OPTIONS"])("%s does not", (m) => {
    expect(isStateChangingMethod(m)).toBe(false);
  });
});

describe("Console is unaffected", () => {
  it("consoleRoute still reaches its handler on a cross-site POST", async () => {
    let reached = false;
    const route = consoleRoute({
      method: "POST",
      requireAuth: false,
      handler: async () => {
        reached = true;
        return NextResponse.json({ ok: true });
      },
    });

    const response = await route(
      new NextRequest("https://app.test/api/console/health", {
        method: "POST",
        headers: { "Sec-Fetch-Site": "cross-site", Origin: "https://evil.example" },
      }),
    );

    expect(reached).toBe(true);
    expect(response.status).toBe(200);
  });

  it.each(["handler.ts", "proxy-gate.ts", "session.ts", "client.ts", "config.ts"])(
    "Console's %s does not import the My Day guard",
    (file) => {
      const source = readFileSync(path.join(__dirname, file), "utf8");
      expect(source).not.toMatch(/myday-csrf/);
    },
  );
});
