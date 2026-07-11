// POST /api/console/kill-switch -> backend POST /api/v1/kill-switch/engage,
// mapped down to the frozen console shape {status}.
//
// scope_type is enum-validated HERE because the backend enforces its _SCOPES
// set with a bare `assert` (a 500 on violation) — the BFF fails fast with 400.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type {
  ConsoleKillSwitchResponse,
  KillSwitchEngageInput,
  KillSwitchEngageResponse,
} from "@/lib/skylize/types";

const killSwitchSchema = z.strictObject({
  scope_type: z.enum(["agent", "department", "tenant", "platform"]),
  scope_id: z.string().min(1).max(200),
  reason: z.string().min(1).max(2000),
});

export const POST = consoleRoute<z.infer<typeof killSwitchSchema>>({
  method: "POST",
  schema: killSwitchSchema,
  handler: async ({ body }) => {
    const input: KillSwitchEngageInput = body;
    const result = await skylizeFetch<KillSwitchEngageResponse>(
      "/api/v1/kill-switch/engage",
      { method: "POST", body: input },
    );
    const payload: ConsoleKillSwitchResponse = { status: result.status };
    return NextResponse.json(payload);
  },
});
