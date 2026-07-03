// Server-side authentication for the console API routes (/api/console/*).
//
// The browser stores the Skylize access token in localStorage and sends it as
// `Authorization: Bearer <token>` (see lib/auth.ts). These helpers validate that
// token on the server — against the same backend `/api/v1/auth/me` endpoint the
// client uses — before any privileged action (e.g. the n8n proxy) runs.
//
// This module intentionally imports nothing from `next/*` so the security
// decision stays a plain, unit-testable function.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export interface ConsoleIdentity {
  userId: string;
  orgId: string;
  roles: string[];
}

/** Extract a bearer token from an Authorization header value. Pure. */
export function extractBearerToken(authHeader: string | null): string | null {
  if (!authHeader) return null;
  const match = /^Bearer\s+(.+)$/i.exec(authHeader.trim());
  if (!match) return null;
  const token = match[1].trim();
  return token.length > 0 ? token : null;
}

/**
 * Validate an access token against the Skylize backend and return the caller's
 * identity, or null if the token is missing/invalid or the backend is
 * unreachable. Never throws.
 */
export async function verifyAccessToken(
  token: string,
  baseUrl: string,
): Promise<ConsoleIdentity | null> {
  let res: Response;
  try {
    res = await fetch(`${baseUrl}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;

  let data: unknown;
  try {
    data = await res.json();
  } catch {
    return null;
  }

  const user = data as { user_id?: unknown; org_id?: unknown; roles?: unknown };
  if (typeof user.user_id !== "string" || typeof user.org_id !== "string") {
    return null;
  }
  const roles = Array.isArray(user.roles)
    ? user.roles.filter((r): r is string => typeof r === "string")
    : [];
  return { userId: user.user_id, orgId: user.org_id, roles };
}

/**
 * Authoritative gate for a console request: resolve the caller's identity from
 * its bearer token, or null if unauthenticated. Callers return 401 on null.
 */
export async function authenticateConsoleRequest(
  request: Request,
): Promise<ConsoleIdentity | null> {
  const token = extractBearerToken(request.headers.get("authorization"));
  if (!token) return null;
  const base = API_BASE || new URL(request.url).origin;
  return verifyAccessToken(token, base);
}
