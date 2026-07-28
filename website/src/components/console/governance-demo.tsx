"use client";

import { useState } from "react";
import { AgentRunner } from "./agent-runner";
import {
  DeliverableCard,
  type DeliverableProvenance,
} from "./deliverable-card";
import { HitlPanel } from "./hitl-panel";
import type { ConsoleDeliverable } from "@/lib/skylize/types";

interface ShownDeliverable {
  deliverable: ConsoleDeliverable;
  provenance: DeliverableProvenance;
  hitlId?: string;
}

/**
 * The two-minute governance story, wired together:
 *   run an agent -> the decision gate's verdict is the headline ->
 *   deferred work lands in the pending panel -> a human approve EXECUTES it ->
 *   the deliverable appears with its provenance.
 * All state shown comes from the backend responses; this shell only routes it.
 */
export function GovernanceDemo() {
  const [queueVersion, setQueueVersion] = useState(0);
  const [shown, setShown] = useState<ShownDeliverable | null>(null);

  return (
    <div className="space-y-8">
      <AgentRunner
        onDeferred={() => setQueueVersion((v) => v + 1)}
        onDeliverable={(deliverable) =>
          setShown({ deliverable, provenance: "decision_gate" })
        }
      />
      <HitlPanel
        version={queueVersion}
        onDeliverable={(deliverable, hitlId) =>
          setShown({ deliverable, provenance: "human_approval", hitlId })
        }
      />
      {shown ? (
        <DeliverableCard
          deliverable={shown.deliverable}
          provenance={shown.provenance}
          hitlId={shown.hitlId}
        />
      ) : null}
    </div>
  );
}
