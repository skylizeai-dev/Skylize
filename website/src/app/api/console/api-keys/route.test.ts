// @vitest-environment node
//
// Key management is the one console surface that handles a live secret, so the
// tests below pin the two things that matter: the plaintext travels exactly
// once (on the mint response) and never appears in a list, and a revocation
// that succeeded is reported as a success rather than as a malformed body.

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

const { GET, POST } = await import("./route");
const { DELETE } = await import("./[id]/route");
const { SESSION_COOKIE_NAME, createSessionToken } = await import(
  "@/lib/skylize/session"
);

const BASE = "https://console.test/api/console/api-keys";

async function cookie(): Promise<string> {
  return `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
}

async function get(opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = {};
  if (opts.auth !== false) headers.cookie = await cookie();
  return new NextRequest(BASE, { method: "GET", headers });
}

async function post(body: unknown): Promise<NextRequest> {
  return new NextRequest(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json", cookie: await cookie() },
    body: JSON.stringify(body),
  });
}

async function del(id: string): Promise<[NextRequest, { params: Promise<{ id: string }> }]> {
  const request = new NextRequest(`${BASE}/${id}`, {
    method: "DELETE",
    headers: { cookie: await cookie() },
  });
  return [request, { params: Promise.resolve({ id }) }];
}

const KEY_ID = "33333333-3333-4333-8333-333333333333";

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("GET /api/console/api-keys", () => {
  it("lists metadata only — no secret, no hash", async () => {
    const row = {
      key_id: KEY_ID,
      prefix: "sk_live_abc",
      name: "CI",
      scopes: ["owner"],
      created_by: "user_1",
      created_at: "2026-09-01T00:00:00Z",
      expires_at: null,
      last_used_at: null,
      revoked_at: null,
    };
    skylizeFetch.mockResolvedValue([row]);

    const response = await GET(await get());
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body).toEqual([row]);
    expect(JSON.stringify(body)).not.toContain("api_key");
  });

  it("refuses an unauthenticated read without spending the credential", async () => {
    const response = await GET(await get({ auth: false }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});

describe("POST /api/console/api-keys", () => {
  it("forwards the mint and returns the one-time plaintext", async () => {
    skylizeFetch.mockResolvedValue({
      key_id: KEY_ID,
      prefix: "sk_live_abc",
      name: "CI",
      scopes: ["owner"],
      api_key: "sk_live_abc.thesecret",
      expires_at: null,
    });

    const response = await POST(await post({ name: "CI", scopes: ["owner"] }));

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      api_key: "sk_live_abc.thesecret",
    });
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/api-keys", {
      method: "POST",
      body: { name: "CI", scopes: ["owner"] },
    });
  });

  it("rejects an unnamed key and an unknown field at the edge", async () => {
    for (const bad of [{ name: "" }, { name: "x", role: "owner" }]) {
      skylizeFetch.mockReset();
      const response = await POST(await post(bad));
      expect(response.status).toBe(400);
      expect(skylizeFetch).not.toHaveBeenCalled();
    }
  });
});

describe("DELETE /api/console/api-keys/{id}", () => {
  it("reports a 204 revocation as a success, not a malformed body", async () => {
    // skylizeFetch resolves null for 204. Before that was handled, a completed
    // revocation surfaced as "Backend returned a malformed JSON body."
    skylizeFetch.mockResolvedValue(null);

    const [request, ctx] = await del(KEY_ID);
    const response = await DELETE(request, ctx);

    expect(response.status).toBe(204);
    expect(skylizeFetch).toHaveBeenCalledWith(`/api/v1/api-keys/${KEY_ID}`, {
      method: "DELETE",
    });
  });

  it("refuses a non-UUID id before calling the backend", async () => {
    const [request, ctx] = await del("not-a-uuid");
    const response = await DELETE(request, ctx);

    expect(response.status).toBe(400);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});
