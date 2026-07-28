"use client";

import { AltitudeLine } from "@/components/skylize";
import type { ConsoleDeliverable } from "@/lib/skylize/types";

export type DeliverableProvenance = "decision_gate" | "human_approval";

/** Every value on this card comes from GET /api/console/deliverables/{id} —
 *  the real backend row, nothing synthesized. */
export function DeliverableCard({
  deliverable,
  provenance,
  hitlId,
}: {
  deliverable: ConsoleDeliverable;
  provenance: DeliverableProvenance;
  hitlId?: string;
}) {
  // Prefer the backend's own provenance stamp when present: the replay path
  // records replay_of_hitl_id in the deliverable's metadata.
  const replayOf =
    typeof deliverable.metadata_json.replay_of_hitl_id === "string"
      ? deliverable.metadata_json.replay_of_hitl_id
      : hitlId;

  return (
    <section
      aria-label="Deliverable"
      className="border border-blue/40 bg-card px-7 pt-6 pb-7 sm:px-8"
    >
      <p className="font-mono text-xs tracking-[0.2em] text-blue uppercase">
        {provenance === "human_approval"
          ? "Deliverable · executed by human approval"
          : "Deliverable · executed after decision approval"}
      </p>
      {provenance === "human_approval" && replayOf ? (
        <p className="mt-1 font-mono text-xs tracking-wide text-muted-foreground">
          replay of hitl · {replayOf}
        </p>
      ) : null}

      <h3 className="mt-4 font-display text-xl font-semibold tracking-tight">
        {deliverable.title}
      </h3>
      <p className="mt-1 font-mono text-xs tracking-wide text-muted-foreground">
        {deliverable.agent_id} · {deliverable.deliverable_type} · status{" "}
        {deliverable.status} · v{deliverable.version} ·{" "}
        {new Date(deliverable.created_at).toLocaleString()}
      </p>

      <AltitudeLine className="my-5" />

      <pre className="max-h-96 overflow-auto text-sm leading-relaxed whitespace-pre-wrap">
        {deliverable.content_markdown}
      </pre>

      <AltitudeLine className="my-5" />

      <div className="space-y-1 font-mono text-xs tracking-wide text-muted-foreground">
        <p>
          governance token ·{" "}
          {deliverable.governance_token_id ?? "none recorded"}
        </p>
        <p>
          recorded cost · not exposed by the backend API (no cost or spend
          endpoint exists to read it from)
        </p>
      </div>
    </section>
  );
}
