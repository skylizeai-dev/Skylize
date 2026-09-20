// @vitest-environment node
//
// The notifications list route is a verbatim pass-through, so what is worth
// testing is the EDGE VALIDATION that keeps a malformed cursor or filter from
// costing a round trip, and that the credential is never spent for an
// unauthenticated caller — the same shape as audit/route.test.ts.

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

const BASE = "https://console.test/api/console/notifications";

async function get(query = "", opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = {};
  if (opts.auth !== false) {
    headers.cookie = `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
  }
  return new NextRequest(`${BASE}${query}`, { method: "GET", headers });
}

const PAGE = {
  notifications: [
    {
      notification_id: "11111111-1111-4111-8111-111111111111",
      kind: "hitl.approval_requested",
      severity: "warning",
      title: "Approval needed: spend.increase",
      body: "content_review_agent is waiting on a human decision.",
      correlation_id: "22222222-2222-4222-8222-222222222222",
      created_at: "2026-09-17T10:00:00Z",
      read_at: null,
    },
  ],
  unread_count: 1,
  next_before: null,
};

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("GET /api/console/notifications", () => {
  it("forwards the page verbatim", async () => {
    skylizeFetch.mockResolvedValue(PAGE);

    const response = await GET(await get());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(PAGE);
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/notifications?limit=50");
  });

  it("forwards the unread filter", async () => {
    skylizeFetch.mockResolvedValue(PAGE);

    await GET(await get("?unread_only=true"));

    expect(skylizeFetch).toHaveBeenCalledWith(
      "/api/v1/notifications?limit=50&unread_only=true",
    );
  });

  it("refuses an invalid unread_only value", async () => {
    const response = await GET(await get("?unread_only=yes"));

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("forwards a timezone-aware cursor", async () => {
    skylizeFetch.mockResolvedValue(PAGE);

    await GET(await get("?limit=10&before=2026-09-17T10%3A00%3A00Z"));

    expect(skylizeFetch).toHaveBeenCalledWith(
      "/api/v1/notifications?limit=10&before=2026-09-17T10%3A00%3A00Z",
    );
  });

  it("refuses a NAIVE cursor at the edge instead of eating a backend 422", async () => {
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
