import { NextResponse } from "next/server";
import { z } from "zod";

import { consoleRoute, errorResponse } from "@/lib/skylize/handler";

/**
 * Server-side bridge to the n8n instance's public REST API.
 * The browser never talks to n8n directly and never sees the API key.
 *
 * REFACTORED onto the shared guarded helper (lib/skylize/handler.ts): the
 * session guard, method guard, zod body validation, and uniform error
 * envelope are the same class every /api/console route uses — auth is the
 * signed `skylize_console` cookie, replacing the old per-route bearer check.
 * This endpoint drives privileged n8n admin actions (create/activate/delete
 * workflows, which can run arbitrary Code/HTTP nodes), so it must never be
 * reachable by an unauthenticated caller.
 *
 * Requires:
 *   N8N_API_URL — e.g. https://skylize.app.n8n.cloud
 *   N8N_API_KEY — an n8n API key (Settings → n8n API)
 */

const N8N_URL = process.env.N8N_API_URL?.replace(/\/$/, "");
const N8N_KEY = process.env.N8N_API_KEY;

// SECURITY GATE (audit 3aa2bed3, HIGH severity) — INTENTIONALLY OFF BY DEFAULT.
// This route drives ungoverned n8n admin actions (create/activate/delete
// workflows, which can run arbitrary Code/HTTP nodes) behind only a session
// cookie — there is NO GovernanceToken / Decision-Engine gate, contradicting
// system_boundaries.md (egress only via governed Integration Adapters). The path
// is dormant (no console UI calls it) and not needed for MVP, so it is gated off
// pending a governed rewrite in Scale-tier. The code below is preserved on
// purpose. Setting SKYLIZE_ENABLE_N8N_ADMIN=true re-enables the raw (still
// session-only, still ungoverned) path for a controlled context; do NOT graft a
// fake GovernanceToken here — the governed rewrite is the real fix, not this one.
const N8N_ADMIN_ENABLED = process.env.SKYLIZE_ENABLE_N8N_ADMIN === "true";

const workflowDefinitionSchema = z.strictObject({
  name: z.string().min(1),
  nodes: z.array(z.unknown()),
  connections: z.record(z.string(), z.unknown()),
  settings: z.record(z.string(), z.unknown()),
});

const bodySchema = z.discriminatedUnion("action", [
  z.strictObject({ action: z.literal("create"), workflow: workflowDefinitionSchema }),
  z.strictObject({ action: z.literal("activate"), id: z.string().min(1) }),
  z.strictObject({ action: z.literal("discard"), id: z.string().min(1) }),
]);

async function n8nFetch(path: string, init: RequestInit) {
  const res = await fetch(`${N8N_URL}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-N8N-API-KEY": N8N_KEY as string,
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  return { res, data };
}

/** Translate an n8n API error into plain language for the console. */
function plainError(status: number, data: unknown): string {
  const detail =
    typeof data === "object" && data !== null && "message" in data
      ? String((data as { message: unknown }).message)
      : "";
  if (status === 400)
    return `n8n rejected the workflow definition${detail ? `: ${detail}` : "."}`;
  if (status === 401 || status === 403)
    return "The n8n API key was rejected — check N8N_API_KEY.";
  if (status === 404) return "The workflow no longer exists in n8n.";
  return `n8n returned an unexpected error (HTTP ${status})${detail ? `: ${detail}` : "."}`;
}

export const POST = consoleRoute<z.infer<typeof bodySchema>>({
  method: "POST",
  schema: bodySchema,
  handler: async ({ body }) => {
    // Gated off by default — see the SECURITY GATE note above. Short-circuit
    // before touching n8n credentials or the network.
    if (!N8N_ADMIN_ENABLED) {
      return errorResponse(
        501,
        "n8n workflow admin is not enabled in this build (gated off pending a governed rewrite).",
      );
    }

    if (!N8N_URL || !N8N_KEY) {
      return errorResponse(
        503,
        "The n8n connection is not configured on this server (set N8N_API_URL and N8N_API_KEY).",
      );
    }

    try {
      if (body.action === "create") {
        const { res, data } = await n8nFetch("/workflows", {
          method: "POST",
          body: JSON.stringify(body.workflow),
        });
        if (!res.ok) return errorResponse(res.status, plainError(res.status, data));
        return NextResponse.json({ id: data.id, name: data.name });
      }

      if (body.action === "activate") {
        const { res, data } = await n8nFetch(
          `/workflows/${encodeURIComponent(body.id)}/activate`,
          { method: "POST" },
        );
        if (!res.ok) return errorResponse(res.status, plainError(res.status, data));
        return NextResponse.json({ id: data.id, active: data.active === true });
      }

      const { res, data } = await n8nFetch(
        `/workflows/${encodeURIComponent(body.id)}`,
        { method: "DELETE" },
      );
      if (!res.ok) return errorResponse(res.status, plainError(res.status, data));
      return NextResponse.json({ deleted: true });
    } catch {
      return errorResponse(502, "Could not reach the n8n instance.");
    }
  },
});
