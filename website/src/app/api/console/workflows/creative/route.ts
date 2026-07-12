// POST /api/console/workflows/creative -> backend POST /api/v1/workflows/creative,
// mapped down to the frozen console shape {status, hooks: string[]}.

import { NextResponse } from "next/server";
import { z } from "zod";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type {
  BackendWorkflowResponse,
  ConsoleCreativeResponse,
  CreativeRunInput,
} from "@/lib/skylize/types";

// Mirrors the backend's CreativeRunRequest bounds (count: ge=1, le=10); the
// backend model is extra="forbid", so strictObject keeps us byte-compatible.
const creativeSchema = z.strictObject({
  product: z.string().min(1).max(500),
  audience: z.string().min(1).max(500),
  count: z.int().min(1).max(10).optional(),
});

/** On non-success runs `output` can be null or hookless — degrade to []. */
function extractHooks(output: Record<string, unknown> | null): string[] {
  if (output === null || !Array.isArray(output.hooks)) return [];
  return output.hooks.filter((hook): hook is string => typeof hook === "string");
}

export const POST = consoleRoute<z.infer<typeof creativeSchema>>({
  method: "POST",
  schema: creativeSchema,
  handler: async ({ body }) => {
    const input: CreativeRunInput = body;
    const result = await skylizeFetch<BackendWorkflowResponse>(
      "/api/v1/workflows/creative",
      { method: "POST", body: input },
    );
    const payload: ConsoleCreativeResponse = {
      status: result.status,
      hooks: extractHooks(result.output),
    };
    return NextResponse.json(payload);
  },
});
