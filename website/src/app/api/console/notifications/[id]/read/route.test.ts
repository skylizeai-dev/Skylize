// @vitest-environment node
//
// Mark-read is a bodyless action route: what is worth testing is the UUID
// guard (no wasted round trip on a malformed id) and that a real id forwards
// verbatim to the backend's idempotent POST — the same shape api-keys'
// [id]/route.test.ts uses for its DELETE.

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

const { POST } = await import("./route");
const { SESSION_COOKIE_NAME, createSessionToken } = await import(
  "@/lib/skylize/session"
);

const BASE = "https://console.test/api/console/notifications";
const NOTIFICATION_ID = "11111111-1111-4111-8111-111111111111";

async function cookie(): Promise<string> {
  return `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
}

async function post(
  id: string,
): Promise<[NextRequest, { params: Promise<{ id: string }> }]> {
  const request = new NextRequest(`${BASE}/${id}/read`, {
    method: "POST",
    headers: { cookie: await cookie() },
  });
  return [request, { params: Promise.resolve({ id }) }];
}

const READ_NOTIFICATION = {
  notification_id: NOTIFICATION_ID,
  kind: "hitl.approval_requested",
  severity: "warning",
  title: "Approval needed: spend.increase",
  body: "content_review_agent is waiting on a human decision.",
  correlation_id: "22222222-2222-4222-8222-222222222222",
  created_at: "2026-09-17T10:00:00Z",
  read_at: "2026-09-17T10:05:00Z",
};

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("POST /api/console/notifications/[id]/read", () => {
  it("forwards the acknowledgement verbatim", async () => {
    skylizeFetch.mockResolvedValue(READ_NOTIFICATION);
    const [request, ctx] = await post(NOTIFICATION_ID);

    const response = await POST(request, ctx);

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(READ_NOTIFICATION);
    expect(skylizeFetch).toHaveBeenCalledWith(
      `/api/v1/notifications/${NOTIFICATION_ID}/read`,
      { method: "POST" },
    );
  });

  it("refuses a non-UUID id before calling the backend", async () => {
    const [request, ctx] = await post("not-a-uuid");

    const response = await POST(request, ctx);

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated call without spending the credential", async () => {
    const request = new NextRequest(`${BASE}/${NOTIFICATION_ID}/read`, {
      method: "POST",
    });
    const response = await POST(request, { params: Promise.resolve({ id: NOTIFICATION_ID }) });

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});
