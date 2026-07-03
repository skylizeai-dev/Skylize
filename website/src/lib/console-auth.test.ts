// Runnable with zero extra dependencies:  node --test src/lib/console-auth.test.ts
// (Node >= 23.6 strips TypeScript types natively.)
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  extractBearerToken,
  authenticateConsoleRequest,
  verifyAccessToken,
} from "./console-auth.ts";

test("extractBearerToken accepts a well-formed header", () => {
  assert.equal(extractBearerToken("Bearer abc123"), "abc123");
  assert.equal(extractBearerToken("bearer abc123"), "abc123"); // case-insensitive
  assert.equal(extractBearerToken("   Bearer    tok   "), "tok");
});

test("extractBearerToken rejects missing / malformed headers", () => {
  assert.equal(extractBearerToken(null), null);
  assert.equal(extractBearerToken(""), null);
  assert.equal(extractBearerToken("abc123"), null); // no scheme
  assert.equal(extractBearerToken("Bearer "), null); // empty token
  assert.equal(extractBearerToken("Basic xyz"), null); // wrong scheme
});

test("authenticateConsoleRequest returns null for an unauthenticated request", async () => {
  const req = new Request("http://localhost/api/console/workflows", {
    method: "POST",
  });
  // No Authorization header -> unauthenticated -> null (route returns 401).
  const identity = await authenticateConsoleRequest(req);
  assert.equal(identity, null);
});

test("verifyAccessToken resolves identity when the backend accepts the token", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({ user_id: "u1", org_id: "org_a", roles: ["owner"] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  try {
    const identity = await verifyAccessToken("good-token", "http://backend");
    assert.deepEqual(identity, {
      userId: "u1",
      orgId: "org_a",
      roles: ["owner"],
    });
  } finally {
    globalThis.fetch = original;
  }
});

test("verifyAccessToken returns null when the backend rejects the token", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "invalid" }), { status: 401 });
  try {
    const identity = await verifyAccessToken("bad-token", "http://backend");
    assert.equal(identity, null);
  } finally {
    globalThis.fetch = original;
  }
});

test("verifyAccessToken returns null when the backend is unreachable", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("connection refused");
  };
  try {
    const identity = await verifyAccessToken("any", "http://backend");
    assert.equal(identity, null);
  } finally {
    globalThis.fetch = original;
  }
});
