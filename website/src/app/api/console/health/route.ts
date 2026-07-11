// GET /api/console/health -> backend GET /health, verbatim {status, backend}.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { BackendHealth, ConsoleHealth } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const health = await skylizeFetch<BackendHealth>("/health");
    const payload: ConsoleHealth = {
      status: health.status,
      backend: health.backend,
    };
    return NextResponse.json(payload);
  },
});
