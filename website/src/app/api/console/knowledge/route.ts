// GET /api/console/knowledge -> backend GET /api/v1/knowledge/index-health.
//
// WHAT THIS FEED IS NOT. The console's Knowledge screen was designed around a
// "data sources" table whose every column is fiction, and forwarding is only
// safe because none of those columns can be filled from this response:
//
//   * SOURCES. There is no source entity, no connector registry and no sync
//     cadence anywhere in the backend. What this returns is `source_path`: the
//     ORIGIN STRING whoever ingested a document supplied — an upload's
//     `filename`, the literal "onboarding-interview", or a webhook-supplied
//     path (knowledge.py `upload_knowledge` / `interview_knowledge`). Nothing
//     connects to it, polls it, or syncs it. Rendering it under a heading that
//     reads as a configured, syncing data source would assert an integration
//     that does not exist.
//   * CONNECTOR TYPE (DATABASE / CONNECTOR / STREAM / WAREHOUSE). No such
//     taxonomy exists in any table, payload or config. It cannot be derived
//     from a file path, and guessing one from an extension would be a label
//     the platform cannot stand behind.
//   * COVERAGE %. Coverage is ingested-over-total, and the platform never
//     learns the origin's total. The denominator does not exist, so neither
//     does the ratio.
//   * SYNCED / INDEXING STATUS. Ingestion is synchronous and there is no job
//     record, so there is no in-flight state to report. Every chunk this
//     census counts is already indexed, by definition of having been counted.
//   * RECALL p95. No latency is measured or stored on the search path.
//
// What IS real and forwarded: per-`source_path` chunk and document counts, the
// departments actually tagged on those chunks, and `last_ingested_at` — the
// max stored `ingested_at`, i.e. when SKYLIZE last wrote, never when the
// origin last changed.
//
// `truncated` is the backend's own admission that its walk hit a point cap. It
// is forwarded rather than dropped: the counts beside it are then a LOWER
// BOUND, and a console that renders them as complete totals would be wrong in
// exactly the case the flag exists to catch.
//
// Knowledge ingestion is optional at bootstrap (constructed only when both
// QDRANT_URL and OPENAI_API_KEY are set), so the backend answers 503 when it
// is unconfigured. That is a meaningful 4xx/5xx to the console — "this org has
// no vector store wired", not a failure — and `mapSkylizeError` passes the
// backend's own detail through for it.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { BackendKnowledgeIndexHealth } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    // No query parameters: the backend scopes the census to the authenticated
    // caller's own org (ctx.org_id), and there is deliberately nothing here
    // that could ask it about a different one.
    const health = await skylizeFetch<BackendKnowledgeIndexHealth>(
      "/api/v1/knowledge/index-health",
    );
    return NextResponse.json(health);
  },
});
