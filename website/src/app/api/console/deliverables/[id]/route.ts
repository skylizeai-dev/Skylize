// GET /api/console/deliverables/{id} -> backend GET /api/v1/deliverables/{id},
// verbatim (DeliverableDetailResponse). 404 passes through via the shared
// error mapping.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type { BackendDeliverableDetail } from "@/lib/skylize/types";

export const GET = consoleRoute<undefined, { id: string }>({
  method: "GET",
  handler: async ({ params }) => {
    // The framework's own dynamic segment, forwarded by consoleRoute — no
    // pathname regex mirroring this file's folder name.
    if (!z.uuid().safeParse(params.id).success) {
      return errorResponse(400, "Invalid deliverable id — expected a UUID.");
    }
    const deliverable = await skylizeFetch<BackendDeliverableDetail>(
      `/api/v1/deliverables/${params.id}`,
    );
    return NextResponse.json(deliverable);
  },
});
