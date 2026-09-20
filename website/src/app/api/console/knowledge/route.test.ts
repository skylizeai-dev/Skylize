// @vitest-environment node
//
// The knowledge route is a verbatim pass-through of a census the backend
// already computed, so what is worth testing is (a) that the credential is
// never spent for an unauthenticated caller, (b) that the route cannot be
// steered at another tenant's index, and (c) that it neither invents the
// console mock's fabricated source columns nor drops the backend's own
// `truncated` admission.

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
const { SkylizeApiError } = await import("@/lib/skylize/client");

const BASE = "https://console.test/api/console/knowledge";

async function get(query = "", opts: { auth?: boolean } = {}): Promise<NextRequest> {
  const headers: Record<string, string> = {};
  if (opts.auth !== false) {
    headers.cookie = `${SESSION_COOKIE_NAME}=${await createSessionToken(SESSION_SECRET)}`;
  }
  return new NextRequest(`${BASE}${query}`, { method: "GET", headers });
}

const HEALTH = {
  total_chunks: 7,
  total_documents: 3,
  source_paths: [
    {
      source_path: "handbook.md",
      chunks: 5,
      documents: 1,
      departments: ["support"],
      last_ingested_at: "2026-09-17T10:00:00+00:00",
    },
    {
      source_path: "onboarding-interview",
      chunks: 2,
      documents: 2,
      departments: [],
      last_ingested_at: "2026-09-16T09:00:00+00:00",
    },
  ],
  last_ingested_at: "2026-09-17T10:00:00+00:00",
  truncated: false,
};

beforeEach(() => {
  skylizeFetch.mockReset();
});

describe("GET /api/console/knowledge", () => {
  it("forwards the census verbatim", async () => {
    skylizeFetch.mockResolvedValue(HEALTH);

    const response = await GET(await get());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(HEALTH);
    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/knowledge/index-health");
  });

  it("adds none of the console mock's fabricated source columns", async () => {
    // There is no connector taxonomy, coverage ratio, sync status or recall
    // latency behind this screen. The route's job is to not become the place
    // one gets invented.
    skylizeFetch.mockResolvedValue(HEALTH);

    const body = JSON.stringify(await (await GET(await get())).json());

    for (const invented of [
      "connector",
      "coverage",
      "SYNCED",
      "INDEXING",
      "recall",
      "p95",
      "next_sync",
    ]) {
      expect(body).not.toContain(invented);
    }
  });

  it("cannot be steered at another tenant's index", async () => {
    // The backend scopes to ctx.org_id and the route forwards no parameters,
    // so a hand-crafted query string changes nothing about what is fetched.
    skylizeFetch.mockResolvedValue(HEALTH);

    await GET(await get("?org_id=org_b&source_path=../other"));

    expect(skylizeFetch).toHaveBeenCalledWith("/api/v1/knowledge/index-health");
  });

  it("forwards the backend's truncation admission instead of hiding it", async () => {
    // With truncated true the counts are a LOWER BOUND. Dropping the flag
    // would turn a partial census into a confident, wrong total.
    skylizeFetch.mockResolvedValue({ ...HEALTH, truncated: true });

    const body = await (await GET(await get())).json();

    expect(body.truncated).toBe(true);
  });

  it("degrades rather than crashes when the vector store is unconfigured", async () => {
    // knowledge_ingestion is None unless QDRANT_URL and OPENAI_API_KEY are both
    // set (bootstrap.py), and the backend route answers 503. `mapSkylizeError`
    // collapses every backend 5xx to a console 502 ("upstream failed"), and the
    // backend's own detail is what carries the reason through. What matters
    // here is that it is a clean, explained refusal and NOT a 401 — a 401 would
    // send the operator to the login screen over a missing env var.
    skylizeFetch.mockRejectedValue(
      new SkylizeApiError(503, "knowledge ingestion not configured"),
    );

    const response = await GET(await get());

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: "knowledge ingestion not configured",
    });
  });

  it("refuses an unauthenticated read without spending the credential", async () => {
    const response = await GET(await get("", { auth: false }));

    expect(response.status).toBe(401);
    expect(skylizeFetch).not.toHaveBeenCalled();
  });
});
