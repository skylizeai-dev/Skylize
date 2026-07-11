// POST /api/console/session — interim single-password login -> 204 + cookie.
// DELETE /api/console/session — logout -> 204 + cleared cookie.
//
// INTERIM gate: one shared password (SKYLIZE_CONSOLE_PASSWORD) stands in for
// the deferred OIDC epic. Deliberately unauthenticated (it IS the login) and
// exempted from the proxy gate by the frozen contract.

import { NextResponse } from "next/server";
import { z } from "zod";

import { getConsoleAuthConfig } from "@/lib/skylize/config";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import {
  clearedSessionCookie,
  constantTimeEquals,
  createSessionToken,
  sessionCookie,
} from "@/lib/skylize/session";

const loginSchema = z.strictObject({
  password: z.string().min(1).max(1024),
});

export const POST = consoleRoute<z.infer<typeof loginSchema>>({
  method: "POST",
  requireAuth: false,
  schema: loginSchema,
  handler: async ({ body }) => {
    const { accessPassword, cookieSecret } = getConsoleAuthConfig();

    if (!(await constantTimeEquals(body.password, accessPassword))) {
      return errorResponse(401, "Invalid password.");
    }

    const token = await createSessionToken(cookieSecret);
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(sessionCookie(token));
    return response;
  },
});

export const DELETE = consoleRoute({
  method: "DELETE",
  requireAuth: false,
  handler: async () => {
    const response = new NextResponse(null, { status: 204 });
    response.cookies.set(clearedSessionCookie());
    return response;
  },
});
