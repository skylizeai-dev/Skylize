// GET /api/console/users -> backend GET /api/v1/tenants/me/users, verbatim.
//
// TWO FIELDS COME BACK, AND TWO IS ALL THERE IS. `UserResponse` is
// {user_id, role} (tenants.py:105-113). There is no email column, no display
// name, no last-seen timestamp and no MFA state behind this route.
//
// The console's Team screen was designed with all four of those. They are not
// projected away here as a matter of taste — there is nothing to project. A
// console that filled those columns could only fill them with invented people,
// which is what the previous fixture did (it shipped six named employees at an
// invented company). The browser side now renders the two real fields and
// drops the rest.
//
// Org scoping is the backend's, off the authenticated key's own context —
// never a parameter this route accepts.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { BackendOrgUser } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const users = await skylizeFetch<BackendOrgUser[]>("/api/v1/tenants/me/users");
    return NextResponse.json(users);
  },
});
