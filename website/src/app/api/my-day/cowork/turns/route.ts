// POST /api/my-day/cowork/turns -> backend POST /api/v1/cowork/turns, as THIS user.
//
// ONE FIELD GOES UP: { message }. The backend's `CoworkTurnIn` is
// `extra="forbid"` with exactly that field (schemas/agents/cowork.py), and the
// agent and principal are backend-owned constants — `COWORK_AGENT_ID` and
// `on_behalf_of_principal=ctx.user_id` (edge/routes/cowork.py:50, :104). This
// route does not widen that contract, and could not usefully: a second field
// would be rejected at the backend boundary.
//
// FORWARDING THE USER'S OWN JWT IS WHAT MAKES THIS SAFE. `on_behalf_of_principal`
// is taken from the resolved token, and minting intersects the agent's manifest
// with THAT employee's own compiled authority before signing
// (app/principal/authority.py:112). So a turn can never do something the
// employee could not do themselves. Sent under the shared service key instead, it
// would run under the service identity's authority — a strictly wrong and
// probably wider grant.
//
// ROLE GATE IS NARROWER THAN THE BRIEF'S, AND THAT IS REPORTED, NOT HIDDEN.
// `/cowork/turns` admits ("owner", "admin", "operator") (cowork.py:52) while
// `/me/brief` admits all five roles (brief.py:39). An analyst or viewer with a
// valid session therefore gets a real 403 here while their brief works fine. The
// 403 passes through with the backend's own detail rather than being smoothed
// into a generic failure, so the UI can say which it is.
//
// A 202 IS NOT AN ERROR: it means the turn was DEFERRED to a human and a
// hitl_queue row exists instead of a reply (cowork.py:129-137). Forwarded as-is,
// status included, so the caller can tell a deferral from an answer.

import { NextResponse } from "next/server";
import { z } from "zod";

import { TURN_TIMEOUT_MS, mydayFetch } from "@/lib/myday/client";
import { mydayRoute } from "@/lib/myday/handler";

// Mirrors `CoworkTurnIn` exactly: one field, 1..20000, nothing else accepted.
const turnSchema = z.strictObject({
  message: z.string().min(1).max(20_000),
});

export const POST = mydayRoute<z.infer<typeof turnSchema>>({
  method: "POST",
  schema: turnSchema,
  handler: async ({ accessToken, body }) => {
    const result = await mydayFetch<unknown>("/api/v1/cowork/turns", accessToken, {
      method: "POST",
      body,
      // A turn is a full governed agent run against a paid provider; the
      // backend allows it 120s, so the BFF must not give up first.
      timeoutMs: TURN_TIMEOUT_MS,
    });
    // THE BACKEND'S OWN STATUS, forwarded rather than reasserted: 201 for a
    // completed turn, 202 for one deferred to a human. Stamping a fixed 201 here
    // would tell the UI a deferred turn had been answered.
    return NextResponse.json(result.body, { status: result.status });
  },
});
