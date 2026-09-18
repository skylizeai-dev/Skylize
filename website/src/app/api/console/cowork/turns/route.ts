// POST /api/console/cowork/turns -> backend POST /api/v1/cowork/turns.
//
// `message` IS THE WHOLE REQUEST, AND THIS SCHEMA IS DELIBERATELY AS NARROW AS
// THE BACKEND'S. The console's composer was designed with an agent-tag picker,
// a department picker and connector selections; none of them has a backend
// counterpart, and adding one is not a wiring decision:
//
//   * the agent is the module constant COWORK_AGENT_ID, never a request field,
//     because "letting a caller name the agent would turn this into a second,
//     less-governed /agents/execute" (cowork.py:47-50);
//   * the principal is `ctx.user_id` off the caller's own credential, because
//     "a caller must never be able to name someone else as the principal"
//     (cowork.py:25-27).
//
// So this is `strictObject`, matching the backend's extra="forbid". If the
// browser ever starts sending a tag or a connector, this route answers 400 and
// the affordance fails LOUDLY at the edge — it never silently no-ops its way
// to a turn the operator thinks was routed somewhere it was not.
//
// TIMEOUT. A turn is one full governed agent run against a paid provider, on
// the same path as /agents/execute, so it gets the approve route's 120s rather
// than the 10s default — the default would abort real work that then completes
// server-side, leaving the operator told it failed.
//
// STATUS CODES the browser must distinguish, forwarded rather than flattened:
//   200 the turn produced a reply (CoworkTurnResponse). The backend answers
//       201 here; this route normalizes it to 200 because the browser
//       branches on the DEFERRED 202, never on 200-vs-201.
//   202 the turn was DEFERRED — a hitl row exists, there is no reply, and the
//       console must say so rather than render an empty answer
//   403 governance/decision/principal refusal (coded; `code` survives mapping)
//   429 token budget or rate limit

import { NextResponse } from "next/server";
import { z } from "zod";

import { SkylizeApiError, skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute, errorResponse } from "@/lib/skylize/handler";
import type {
  BackendCoworkTurnDeferred,
  BackendCoworkTurnResponse,
  CoworkTurnInput,
} from "@/lib/skylize/types";

const coworkTurnSchema = z.strictObject({
  message: z.string().min(1).max(20_000),
});

const TURN_TIMEOUT_MS = 120_000;

export const POST = consoleRoute<z.infer<typeof coworkTurnSchema>>({
  method: "POST",
  schema: coworkTurnSchema,
  handler: async ({ body }) => {
    const input: CoworkTurnInput = body;
    try {
      const result = await skylizeFetch<
        BackendCoworkTurnResponse | BackendCoworkTurnDeferred
      >("/api/v1/cowork/turns", {
        method: "POST",
        body: input,
        timeoutMs: TURN_TIMEOUT_MS,
      });
      // `skylizeFetch` treats every 2xx as success, so the 201 and the 202 are
      // told apart by the field only the deferred body has -- the same test the
      // agents/execute route uses. A deferred turn produced NO reply, and the
      // console has to say so rather than render an empty answer.
      if ("hitl_id" in result) {
        return NextResponse.json(result, { status: 202 });
      }
      return NextResponse.json(result, { status: 200 });
    } catch (error) {
      // SAME EXCEPTION AS agents/execute, for the same reason. The shared
      // mapSkylizeError collapses every backend 403 into 502 ("the SERVER's
      // credential was refused"), which is right for a plain read but wrong
      // here: this route walks the governed pipeline, so a 403 has three
      // distinct causes the operator must be able to tell apart --
      //   decision_rejected          a governance verdict on their request
      //   governance_denied          a platform control (kill switch) blocked it
      //   principal_authority_denied their OWN authority was insufficient
      // Reporting any of those as "the server credential was refused" would
      // send an operator to fix a deployment problem that does not exist.
      if (error instanceof SkylizeApiError && error.status === 403) {
        return errorResponse(403, error.message, error.code);
      }
      throw error;
    }
  },
});
