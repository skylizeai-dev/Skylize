// @vitest-environment node
//
// NODE, not jsdom: these routes are server-only and lib/skylize/config throws
// when `window` exists (config.ts:27-35).
//
// The point of this suite is the NARROWNESS of the turn contract. The console's
// composer was designed with an agent-tag picker, a department picker and
// connector selections; the backend accepts `message` and nothing else, and
// fixes both the agent and the principal itself (cowork.py:47-50). A BFF that
// quietly dropped an extra field would let the UI look like it had routed a
// directive somewhere it had not, so the tests below assert that such a request
// is REFUSED rather than trimmed.

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
const { SkylizeApiError } = await import("@/lib/skylize/client");
const { SESSION_COOKIE_NAME, createSessionToken } = await import(
  "@/lib/skylize/session"
);

const URL_ = "https://console.test/api/console/cowork/turns";

async function post(body: unknown, opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.auth !== false) {
    headers.cookie = `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
  }
  return new NextRequest(URL_, { method: "POST", headers, body: JSON.stringify(body) });
}

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("POST /api/console/cowork/turns", () => {
  it("forwards the message and returns the backend's three fields", async () => {
    skylizeFetch.mockResolvedValue({
      reply: "Three hooks drafted.",
      deliverable_id: "6f1e9a3c-0f1a-4d1e-9a3c-0f1a4d1e9a3c",
      agent_id: "cowork_agent",
    });

    const response = await POST(await post({ message: "Draft three hooks" }));

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      reply: "Three hooks drafted.",
      deliverable_id: "6f1e9a3c-0f1a-4d1e-9a3c-0f1a4d1e9a3c",
      agent_id: "cowork_agent",
    });
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/cowork/turns", {
      method: "POST",
      body: { message: "Draft three hooks" },
      timeoutMs: 120_000,
    });
  });

  it("REFUSES an agent_id rather than silently dropping it", async () => {
    // The whole reason the tag picker is disabled in the UI. If this ever
    // returned 200 by trimming the field, the console could show a directive as
    // "sent to the CFO" when the backend had routed it to cowork_agent.
    const response = await POST(
      await post({ message: "Reforecast Q3", agent_id: "cfo_agent" }),
    );

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("REFUSES a department, a principal, and attachments the same way", async () => {
    for (const extra of [
      { department: "finance" },
      { on_behalf_of_principal: "user_other" },
      { attachments: ["deck.pdf"] },
      { connectors: ["slack"] },
    ]) {
      skylizeFetch.mockReset();
      const response = await POST(await post({ message: "hello", ...extra }));
      expect(response.status).toBe(400);
      expect(skylizeFetch).not.toHaveBeenCalled();
    }
  });

  it("rejects an empty message at the edge", async () => {
    const response = await POST(await post({ message: "" }));

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated turn without spending the credential", async () => {
    const response = await POST(await post({ message: "hi" }, { auth: false }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });

  it("surfaces a governance refusal as a 403 the console can act on", async () => {
    skylizeFetch.mockRejectedValue(
      new SkylizeApiError(403, "decision rejected: over ceiling", "decision_rejected"),
    );

    const response = await POST(await post({ message: "spend it all" }));

    // A backend 403 on a BUSINESS decision is the operator's answer, not a
    // credential problem, so it must not be flattened into the 502 that
    // mapSkylizeError reserves for a refused service credential.
    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toMatchObject({
      code: "decision_rejected",
    });
  });
});
