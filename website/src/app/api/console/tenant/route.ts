// GET /api/console/tenant -> backend GET /api/v1/tenants/me,
// projected to exactly {org_id, display_name, status}.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { ConsoleTenant, TenantMe } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const tenant = await skylizeFetch<TenantMe>("/api/v1/tenants/me");
    const payload: ConsoleTenant = {
      org_id: tenant.org_id,
      display_name: tenant.display_name,
      status: tenant.status,
    };
    return NextResponse.json(payload);
  },
});
