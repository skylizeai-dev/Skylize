// POST /api/console/agents/execute -> backend POST /api/v1/agents/execute.
//
// STATUS CODES ARE THE CONTRACT and are passed through verbatim — each one is
// a distinct governance outcome the UI must distinguish:
//   201 approved  -> ExecuteAgentResponse body, re-emitted with status 201
//   202 deferred  -> the hitl_id body, re-emitted with status 202 (skylizeFetch
//                    treats any 2xx as success, so the two are told apart by
//                    the presence of `hitl_id` — a field only the 202 body has)
//   403 rejected  -> caught HERE and re-emitted as 403 with the backend's
//                    decision reason. This deliberately bypasses the shared
//                    mapSkylizeError rule that treats 403 as a service-
//                    credential failure (502): on THIS route a 403 carries a
//                    governance verdict ("decision rejected: ..."), not a
//                    credential problem.
//   404 / 422 / 429 pass through via the shared error mapping.

import { NextResponse } from "next/server";
import { z } from "zod";

import { SkylizeApiError, skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type {
  BackendExecuteApproved,
  BackendExecuteDeferred,
} from "@/lib/skylize/types";

// Mirrors the backend's ExecuteAgentRequest (agent_id: min 1 / max 200,
// input: dict). The backend model is extra="forbid", so strictObject keeps us
// byte-compatible.
const executeSchema = z.strictObject({
  agent_id: z.string().min(1).max(200),
  input: z.record(z.string(), z.unknown()),
});

/** Agent runs call a live LLM behind the backend — allow well past 10s. */
const EXECUTE_TIMEOUT_MS = 60_000;

export const POST = consoleRoute<z.infer<typeof executeSchema>>({
  method: "POST",
  schema: executeSchema,
  handler: async ({ body }) => {
    try {
      const result = await skylizeFetch<
        BackendExecuteApproved | BackendExecuteDeferred
      >("/api/v1/agents/execute", {
        method: "POST",
        body,
        timeoutMs: EXECUTE_TIMEOUT_MS,
      });
      if ("hitl_id" in result) {
        return NextResponse.json(result, { status: 202 });
      }
      return NextResponse.json(result, { status: 201 });
    } catch (error) {
      if (error instanceof SkylizeApiError && error.status === 403) {
        return errorResponse(403, error.message);
      }
      throw error;
    }
  },
});
