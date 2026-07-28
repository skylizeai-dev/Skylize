// GET /api/console/deliverables/{id} -> backend GET /api/v1/deliverables/{id},
// verbatim (DeliverableDetailResponse). 404 passes through via the shared
// error mapping.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendDeliverableDetail } from "@/lib/skylize/types";

function deliverableIdFromPath(pathname: string): string | null {
  const match = pathname.match(/\/api\/console\/deliverables\/([^/]+)\/?$/);
  if (!match) return null;
  const candidate = decodeURIComponent(match[1]);
  return z.uuid().safeParse(candidate).success ? candidate : null;
}

export const GET = consoleRoute({
  method: "GET",
  handler: async ({ request }) => {
    const id = deliverableIdFromPath(request.nextUrl.pathname);
    if (id === null) {
      return errorResponse(400, "Invalid deliverable id — expected a UUID.");
    }
    const deliverable = await skylizeFetch<BackendDeliverableDetail>(
      `/api/v1/deliverables/${id}`,
    );
    return NextResponse.json(deliverable);
  },
});
