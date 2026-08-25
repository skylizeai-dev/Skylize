// GET /api/console/agents -> backend GET /api/v1/agents, verbatim.
//
// The agent picker is built from this live list (ids + JSON input schemas) —
// never from a hardcoded catalogue.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { BackendAgentListResponse } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const agents = await skylizeFetch<BackendAgentListResponse>("/api/v1/agents");
    return NextResponse.json(agents);
  },
});
