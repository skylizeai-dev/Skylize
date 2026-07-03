import { NextResponse } from "next/server";

import { authenticateConsoleRequest } from "@/lib/console-auth";

/**
 * Server-side bridge to the n8n instance's public REST API.
 * The browser never talks to n8n directly and never sees the API key.
 *
 * Every request is authenticated (org-scoped) before any n8n call — this
 * endpoint drives privileged n8n admin actions (create/activate/delete
 * workflows, which can run arbitrary Code/HTTP nodes), so it must never be
 * reachable by an unauthenticated caller.
 *
 * Requires:
 *   N8N_API_URL — e.g. https://skylize.app.n8n.cloud
 *   N8N_API_KEY — an n8n API key (Settings → n8n API)
 */

const N8N_URL = process.env.N8N_API_URL?.replace(/\/$/, "");
const N8N_KEY = process.env.N8N_API_KEY;

interface CreateBody {
  action: "create";
  workflow: {
    name: string;
    nodes: unknown[];
    connections: Record<string, unknown>;
    settings: Record<string, unknown>;
  };
}
interface IdBody {
  action: "activate" | "discard";
  id: string;
}
type Body = CreateBody | IdBody;

async function n8nFetch(path: string, init: RequestInit) {
  const res = await fetch(`${N8N_URL}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-N8N-API-KEY": N8N_KEY as string,
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  const data = await res.json().catch(() => ({}));
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

export async function POST(request: Request) {
  // Authoritative, org-scoped auth gate — runs before any n8n call and before
  // revealing configuration state. Defense-in-depth alongside proxy.ts.
  const identity = await authenticateConsoleRequest(request);
  if (!identity) {
    return NextResponse.json(
      { error: "Authentication required." },
      { status: 401 },
    );
  }

  if (!N8N_URL || !N8N_KEY) {
    return NextResponse.json(
      {
        error:
          "The n8n connection is not configured on this server (set N8N_API_URL and N8N_API_KEY).",
      },
      { status: 503 },
    );
  }

  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: "Invalid request body." }, { status: 400 });
  }

  try {
    if (body.action === "create") {
      const { res, data } = await n8nFetch("/workflows", {
        method: "POST",
        body: JSON.stringify(body.workflow),
      });
      if (!res.ok)
        return NextResponse.json(
          { error: plainError(res.status, data) },
          { status: res.status },
        );
      return NextResponse.json({ id: data.id, name: data.name });
    }

    if (body.action === "activate") {
      const { res, data } = await n8nFetch(
        `/workflows/${encodeURIComponent(body.id)}/activate`,
        { method: "POST" },
      );
      if (!res.ok)
        return NextResponse.json(
          { error: plainError(res.status, data) },
          { status: res.status },
        );
      return NextResponse.json({ id: data.id, active: data.active === true });
    }

    if (body.action === "discard") {
      const { res, data } = await n8nFetch(
        `/workflows/${encodeURIComponent(body.id)}`,
        { method: "DELETE" },
      );
      if (!res.ok)
        return NextResponse.json(
          { error: plainError(res.status, data) },
          { status: res.status },
        );
      return NextResponse.json({ deleted: true });
    }

    return NextResponse.json({ error: "Unknown action." }, { status: 400 });
  } catch {
    return NextResponse.json(
      { error: "Could not reach the n8n instance." },
      { status: 502 },
    );
  }
}
