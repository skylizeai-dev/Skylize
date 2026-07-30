import { NextRequest, NextResponse } from "next/server";
import { describe, expect, it } from "vitest";

import { consoleRoute, errorResponse } from "@/lib/skylize/handler";

// consoleRoute used to drop the route-context argument Next passes to dynamic
// handlers, so each dynamic route re-derived its [id] from a bespoke pathname
// regex. These tests cover the forward at RUNTIME: typecheck alone would not
// notice a params promise that is never awaited (it would hand the handler a
// Promise typed as the resolved value).

function post(url: string, body: unknown = {}): NextRequest {
  return new NextRequest(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("consoleRoute params forwarding", () => {
  it("hands the handler the AWAITED dynamic segments", async () => {
    let seen: unknown;
    const route = consoleRoute<undefined, { id: string }>({
      method: "POST",
      requireAuth: false,
      handler: async ({ params }) => {
        seen = params;
        return NextResponse.json({ id: params.id });
      },
    });

    const response = await route(
      post("https://console.test/api/console/hitl/abc/approve"),
      { params: Promise.resolve({ id: "abc" }) },
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ id: "abc" });
    // Not a Promise: a forwarded-but-unawaited params object would still
    // typecheck yet break every `params.id` read at runtime.
    expect(seen).toEqual({ id: "abc" });
    expect(seen).not.toBeInstanceOf(Promise);
  });

  it("gives a static route an empty params object, not a crash", async () => {
    const route = consoleRoute({
      method: "POST",
      requireAuth: false,
      handler: async ({ params }) => NextResponse.json({ keys: Object.keys(params) }),
    });

    // Next calls a static route handler with no context argument at all.
    const response = await route(post("https://console.test/api/console/health"));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ keys: [] });
  });

  it("still guards the method before touching params", async () => {
    const route = consoleRoute<undefined, { id: string }>({
      method: "POST",
      requireAuth: false,
      handler: async () => NextResponse.json({ reached: true }),
    });

    const response = await route(
      new NextRequest("https://console.test/api/console/hitl/abc/approve", {
        method: "GET",
      }),
      { params: Promise.resolve({ id: "abc" }) },
    );

    expect(response.status).toBe(405);
  });

  it("surfaces a params rejection through the uniform error envelope", async () => {
    const route = consoleRoute<undefined, { id: string }>({
      method: "POST",
      requireAuth: false,
      handler: async () => NextResponse.json({ reached: true }),
    });

    const response = await route(
      post("https://console.test/api/console/hitl/abc/approve"),
      { params: Promise.reject(new Error("segment resolution failed")) },
    );

    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual({
      error: "Internal server error.",
    });
  });
});

// The browser reads `{ error }`. Adding the backend's machine-readable `code`
// must be purely additive: an envelope with no code has to stay byte-identical
// to what every existing reader already handles.
describe("errorResponse envelope", () => {
  it("emits exactly { error } when there is no code to forward", async () => {
    const response = errorResponse(502, "Backend unreachable.");
    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: "Backend unreachable.",
    });
  });

  it("adds `code` beside the unchanged message when one is forwarded", async () => {
    const response = errorResponse(
      403,
      "requires one of roles: ['admin', 'operator', 'owner']",
      "authorization_failed",
    );
    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      error: "requires one of roles: ['admin', 'operator', 'owner']",
      code: "authorization_failed",
    });
  });
});
