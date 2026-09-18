// @vitest-environment node
//
// The audit route is a verbatim pass-through, so what is worth testing is the
// EDGE VALIDATION that keeps a malformed cursor from costing a round trip, and
// the guarantee that the credential is never spent for an unauthenticated
// caller. The "no human actor / no signature" discipline is enforced on the
// browser side (the route must not invent fields, and it does not).

import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const SESSION_SECRET = "test-console-session-secret-at-least-32-chars";

process.env.SKYLIZE_BACKEND_URL = "https://backend.test";
process.env.SKYLIZE_SERVICE_API_KEY = "sk_live_testprefix.testsecret";
process.env.SKYLIZE_CONSOLE_SESSION_SECRET = SESSION_SECRET;
process.env.SKYLIZE_CONSOLE_PASSWORD = "test-password";

const skylizeFetch = vi.fn();

vi.mock("@/lib/skylize/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/client")>();
  return { ...actual, skylizeFetch };
});

const { GET } = await import("./route");
const { SESSION_COOKIE_NAME, createSessionToken } = await import(
  "@/lib/skylize/session"
);

const BASE = "https://console.test/api/console/audit";

async function get(query = "", opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = {};
  if (opts.auth !== false) {
    headers.cookie = `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
  }
  return new NextRequest(`${BASE}${query}`, { method: "GET", headers });
}

const PAGE = {
  entries: [
    {
      event_id: "11111111-1111-4111-8111-111111111111",
      correlation_id: "22222222-2222-4222-8222-222222222222",
      action_type: "agent.execute",
      result: "success",
      occurred_at: "2026-09-17T10:00:00Z",
      source_agent_id: "cowork_agent",
      authority_level: "worker",
      governance_token_id: null,
      result_reason: null,
      inputs_hash: "a".repeat(64),
      outputs_hash: "b".repeat(64),
    },
  ],
  next_before: null,
};

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("GET /api/console/audit", () => {
  it("forwards the page verbatim, hashes and all", async () => {
    skylizeFetch.mockResolvedValue(PAGE);

    const response = await GET(await get());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(PAGE);
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/audit?limit=50");
  });

  it("forwards a timezone-aware cursor", async () => {
    skylizeFetch.mockResolvedValue(PAGE);

    await GET(await get("?limit=10&before=2026-09-17T10%3A00%3A00Z"));

    expect(skylizeFetch).toHaveBeenCalledWith(
      "/api/v1/audit?limit=10&before=2026-09-17T10%3A00%3A00Z",
    );
  });

  it("refuses a NAIVE cursor at the edge instead of eating a backend 422", async () => {
    // The backend requires tz-awareness (audit.py:53-54). Catching it here
    // turns a wasted round trip into an immediate, specific message.
    const response = await GET(await get("?before=2026-09-17T10%3A00%3A00"));

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("refuses an out-of-range limit", async () => {
    for (const bad of ["?limit=0", "?limit=201", "?limit=abc"]) {
      skylizeFetch.mockReset();
      const response = await GET(await get(bad));
      expect(response.status).toBe(400);
      expect(skylizeFetch).not.toHaveBeenCalled();
    }
  });

  it("refuses an unauthenticated read without spending the credential", async () => {
    const response = await GET(await get("", { auth: false }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});
