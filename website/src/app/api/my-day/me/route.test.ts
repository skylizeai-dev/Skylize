// @vitest-environment node
//
// Route tests for GET /api/my-day/me — the authenticated per-user read, plus
// the UNIT-LEVEL cross-identity isolation proof (design section 6.0, at the
// layer a mocked test can honestly exercise).
//
// The backend is MOCKED: mydayGet is stubbed to ECHO the identity implied by the
// bearer token it was handed (org derived from the access_token). So when the
// route forwards THIS session's access token, the response identity is a pure
// function of which cookie was presented. Presenting user A's cookie yields A's
// org; user B's cookie yields B's org; and A's request can NEVER yield B's org.
// That is exactly the "the route forwards this session's token and no other"
// isolation property, provable without a live backend.
//
// The live, end-to-end two-org proof against real Postgres lives in
// tests/integration/test_myday_identity_isolation_pg.py.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MYDAY_SECRET = "test-myday-me-secret-1111111111111-32+chars";

// mydayGet(path, bearerToken) -> echoes the identity for that bearer token.
const mydayGet = vi.fn();
vi.mock("@/lib/skylize/myday-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/skylize/myday-client")>();
  return { ...actual, mydayGet };
});

const { GET } = await import("./route");
const { MyDayApiError } = await import("@/lib/skylize/myday-client");
const {
  MYDAY_SESSION_COOKIE_NAME,
  deriveMyDayKeyMaterial,
  encryptSession,
} = await import("@/lib/skylize/myday-session");

const BASE = "https://app.test/api/my-day/me";

// Two distinct users in two distinct orgs, each identified by their own access
// token marker. The mock backend maps token -> identity.
const USER_A = {
  access_token: "access-token-marker-A",
  refresh_token: "refresh-token-marker-A",
  org_id: "org_a",
  user_id: "user_a",
  email: "a@example.com",
};
const USER_B = {
  access_token: "access-token-marker-B",
  refresh_token: "refresh-token-marker-B",
  org_id: "org_b",
  user_id: "user_b",
  email: "b@example.com",
};

const IDENTITY_BY_TOKEN: Record<string, typeof USER_A> = {
  [USER_A.access_token]: USER_A,
  [USER_B.access_token]: USER_B,
};

/** Build a request carrying an encrypted skylize_myday cookie for `user`. */
async function reqForUser(user: typeof USER_A): Promise<NextRequest> {
  const key = await deriveMyDayKeyMaterial(MYDAY_SECRET);
  const cookieValue = await encryptSession(
    { access_token: user.access_token, refresh_token: user.refresh_token },
    key,
  );
  return new NextRequest(BASE, {
    method: "GET",
    headers: { cookie: `${MYDAY_SESSION_COOKIE_NAME}=${cookieValue}` },
  });
}

function reqWithRawCookie(value: string): NextRequest {
  return new NextRequest(BASE, {
    method: "GET",
    headers: { cookie: `${MYDAY_SESSION_COOKIE_NAME}=${value}` },
  });
}

beforeEach(() => {
  mydayGet.mockReset();
  vi.stubEnv("SKYLIZE_BACKEND_URL", "https://backend.test");
  vi.stubEnv("SKYLIZE_MYDAY_SESSION_SECRET", MYDAY_SECRET);
  vi.stubEnv("NODE_ENV", "test");
  // The backend echo: identity is a function of the bearer token forwarded.
  mydayGet.mockImplementation(async (_path: string, bearerToken: string) => {
    const identity = IDENTITY_BY_TOKEN[bearerToken];
    if (identity === undefined) {
      throw new MyDayApiError(401, "invalid or expired token");
    }
    return {
      user_id: identity.user_id,
      org_id: identity.org_id,
      email: identity.email,
      display_name: null,
      roles: ["owner"],
      is_active: true,
    };
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /api/my-day/me — fail-closed", () => {
  it("401s and clears the cookie when no cookie is present", async () => {
    const response = await GET(new NextRequest(BASE, { method: "GET" }));
    expect(response.status).toBe(401);
    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie?.value).toBe("");
    expect(cookie?.maxAge).toBe(0);
    expect(mydayGet).not.toHaveBeenCalled();
  });

  it("401s and clears the cookie on a garbage/tampered cookie", async () => {
    const response = await GET(reqWithRawCookie("not-a-valid-encrypted-cookie"));
    expect(response.status).toBe(401);
    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie?.value).toBe("");
    expect(cookie?.maxAge).toBe(0);
    // A cookie that fails to decrypt must never reach the backend.
    expect(mydayGet).not.toHaveBeenCalled();
  });

  it("maps a backend 401 on the access token to 401 + cleared cookie (session expired)", async () => {
    // A validly-decrypting cookie whose token the backend rejects: build a
    // cookie for an UNKNOWN token so the echo mock throws MyDayApiError(401).
    const key = await deriveMyDayKeyMaterial(MYDAY_SECRET);
    const cookieValue = await encryptSession(
      { access_token: "unknown-token-the-backend-rejects", refresh_token: "r" },
      key,
    );
    const response = await GET(reqWithRawCookie(cookieValue));

    expect(response.status).toBe(401);
    const cookie = response.cookies.get(MYDAY_SESSION_COOKIE_NAME);
    expect(cookie?.value).toBe("");
    expect(cookie?.maxAge).toBe(0);
    expect(mydayGet).toHaveBeenCalledWith(
      "/api/v1/auth/me",
      "unknown-token-the-backend-rejects",
    );
  });
});

describe("GET /api/my-day/me — cross-cookie identity isolation (design 6.0)", () => {
  it("returns A's identity for A's cookie", async () => {
    const response = await GET(await reqForUser(USER_A));
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.org_id).toBe("org_a");
    expect(body.user_id).toBe("user_a");
    // The route forwarded A's OWN access token, nothing else.
    expect(mydayGet).toHaveBeenCalledWith("/api/v1/auth/me", USER_A.access_token);
  });

  it("returns B's identity for B's cookie", async () => {
    const response = await GET(await reqForUser(USER_B));
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.org_id).toBe("org_b");
    expect(body.user_id).toBe("user_b");
    expect(mydayGet).toHaveBeenCalledWith("/api/v1/auth/me", USER_B.access_token);
  });

  it("A's cookie NEVER yields B's org, and B's NEVER yields A's", async () => {
    // A's request: assert its body AND that the only token forwarded during it
    // was A's, before B's request runs.
    const respA = await GET(await reqForUser(USER_A));
    const bodyA = await respA.json();
    expect(bodyA.org_id).toBe("org_a");
    expect(bodyA.org_id).not.toBe("org_b");
    expect(mydayGet).toHaveBeenCalledTimes(1);
    expect(mydayGet.mock.calls[0][1]).toBe(USER_A.access_token);
    expect(mydayGet.mock.calls[0][1]).not.toBe(USER_B.access_token);

    // B's request: its identity is B's, and the token IT forwarded was B's —
    // A's token was never re-sent on B's behalf.
    const respB = await GET(await reqForUser(USER_B));
    const bodyB = await respB.json();
    expect(bodyB.org_id).toBe("org_b");
    expect(bodyB.org_id).not.toBe("org_a");
    expect(mydayGet).toHaveBeenCalledTimes(2);
    expect(mydayGet.mock.calls[1][1]).toBe(USER_B.access_token);
    expect(mydayGet.mock.calls[1][1]).not.toBe(USER_A.access_token);
  });
});
