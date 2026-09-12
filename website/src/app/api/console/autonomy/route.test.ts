// @vitest-environment node
//
// NODE, not the suite's default jsdom: this route is server-only, and
// lib/skylize/config asserts exactly that by throwing when `window` exists
// (config.ts:27-35). Running it under jsdom trips that guard -- which is the
// guard working, not a test problem.

// Tests for the autonomy BFF route -- the ONLY server surface the static
// Console-Black SPA is allowed to talk to, and therefore the only place the
// service API key is attached.
//
// The session is a REAL signed cookie (createSessionToken), not a mock: the
// point of this route is that it refuses an unauthenticated caller before it
// ever spends the credential, and mocking the verifier would assume away the
// thing under test. Only `skylizeFetch` -- the network boundary -- is mocked.

import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const SESSION_SECRET = "test-console-session-secret-at-least-32-chars";

process.env.SKYLIZE_BACKEND_URL = "https://backend.test";
process.env.SKYLIZE_SERVICE_API_KEY = "sk_live_testprefix.testsecret";
process.env.SKYLIZE_CONSOLE_SESSION_SECRET = SESSION_SECRET;
process.env.SKYLIZE_CONSOLE_PASSWORD = "test-password";

const skylizeFetch = vi.fn();

vi.mock("@/lib/skylize/client", async (importOriginal) => {
  // SkylizeApiError stays the REAL class: handler.ts maps errors with
  // `instanceof`, which a stubbed class would silently fail.
  const actual = await importOriginal<typeof import("@/lib/skylize/client")>();
  return { ...actual, skylizeFetch };
});

const { GET, PUT } = await import("./route");
const { SkylizeApiError } = await import("@/lib/skylize/client");
const { SESSION_COOKIE_NAME, createSessionToken } = await import(
  "@/lib/skylize/session"
);

const URL_ = "https://console.test/api/console/autonomy";

async function authedCookie(): Promise<string> {
  return `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
}

async function get(): Promise<NextRequest> {
  return new NextRequest(URL_, {
    method: "GET",
    headers: { cookie: await authedCookie() },
  });
}

async function put(body: unknown, opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.auth !== false) headers.cookie = await authedCookie();
  return new NextRequest(URL_, { method: "PUT", headers, body: JSON.stringify(body) });
}

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("GET /api/console/autonomy", () => {
  it("forwards mode AND configured from the backend", async () => {
    skylizeFetch.mockResolvedValue({ mode: "act_within_budget", configured: true });

    const response = await GET(await get());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      mode: "act_within_budget",
      configured: true,
    });
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/autonomy");
  });

  it("keeps 'never configured' distinguishable from 'chose observe'", async () => {
    // The fail-closed default (ruling 7). Dropping `configured` here would make
    // the console unable to tell the owner these two states apart.
    skylizeFetch.mockResolvedValue({ mode: "observe", configured: false });

    const response = await GET(await get());

    await expect(response.json()).resolves.toEqual({
      mode: "observe",
      configured: false,
    });
  });

  it("refuses an unauthenticated read without spending the credential", async () => {
    const response = await GET(new NextRequest(URL_, { method: "GET" }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});

describe("PUT /api/console/autonomy", () => {
  it("sends the validated mode to the backend PUT", async () => {
    skylizeFetch.mockResolvedValue({ mode: "propose", configured: true });

    const response = await PUT(await put({ mode: "propose" }));

    expect(response.status).toBe(200);
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/autonomy", {
      method: "PUT",
      body: { mode: "propose" },
    });
  });

  it("returns the mode the backend PERSISTED, not the one requested", async () => {
    // Guards against an optimistic echo: if the backend ever stored something
    // other than what was asked for, the console must show the stored value.
    skylizeFetch.mockResolvedValue({ mode: "observe", configured: true });

    const response = await PUT(await put({ mode: "act_governed" }));

    await expect(response.json()).resolves.toEqual({
      mode: "observe",
      configured: true,
    });
  });

  it("rejects an unknown mode at the edge, before any backend call", async () => {
    const response = await PUT(await put({ mode: "full_send" }));

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("rejects an extra field (strictObject) rather than forwarding it", async () => {
    const response = await PUT(await put({ mode: "propose", org_id: "org_b" }));

    // org_id must come from the authenticated context at the backend, never
    // from a body field -- so the BFF refuses to carry one at all.
    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated write without spending the credential", async () => {
    const response = await PUT(await put({ mode: "act_governed" }, { auth: false }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("maps a backend credential rejection to 502, never to 401", async () => {
    // A backend 403 means OUR service key lacks the owner scope. Surfacing that
    // as 401 would send the operator to a login screen that cannot fix it.
    skylizeFetch.mockRejectedValue(new SkylizeApiError(403, "requires role: owner"));

    const response = await PUT(await put({ mode: "propose" }));

    expect(response.status).toBe(502);
  });

  it("never puts the service credential in the response body", async () => {
    skylizeFetch.mockResolvedValue({ mode: "propose", configured: true });

    const response = await PUT(await put({ mode: "propose" }));
    const text = await response.text();

    expect(text).not.toContain("sk_live");
    expect(text).not.toContain(process.env.SKYLIZE_SERVICE_API_KEY);
    expect(Object.keys(JSON.parse(text)).sort()).toEqual(["configured", "mode"]);
  });

  it("guards the method: a GET against the PUT export is 405", async () => {
    const response = await PUT(
      new NextRequest(URL_, { method: "GET", headers: { cookie: await authedCookie() } }),
    );

    expect(response.status).toBe(405);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});
