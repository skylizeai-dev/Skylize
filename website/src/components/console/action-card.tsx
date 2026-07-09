"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, X as XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { EASE_ALTITUDE } from "@/lib/motion";

/** Lifecycle of a proposed action awaiting human sign-off. */
export type ActionCardState = "pending" | "approved" | "declined" | "modified";

export interface ActionCardProps {
  /** Proposing agent, rendered as a mono badge (e.g. "creative_ops_manager"). */
  agentId: string;
  /** Department the action belongs to. */
  department: string;
  /**
   * Department accent color (hex or CSS color). Sourced from the canonical
   * department palette (agent-network data) — never hardcode per call site.
   */
  departmentColor?: string;
  /** Plain-language description of the proposed action. */
  description: string;
  /** Unix ms timestamp of the proposal. */
  timestamp: number;
  /** Optional heading, defaults to "Proposed action". */
  title?: string;
  /** Controlled state. If omitted the card manages its own lifecycle. */
  state?: ActionCardState;
  onApprove?: () => void;
  onDecline?: () => void;
  /** Called with the user's modification instruction. */
  onModify?: (instruction: string) => void;
  /** Show the "Something else…" free-text escape hatch. */
  allowFreeText?: boolean;
  /** Called with free-text input from the escape hatch. */
  onFreeText?: (text: string) => void;
  className?: string;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const RESOLVED_LABEL: Record<Exclude<ActionCardState, "pending">, string> = {
  approved: "Approved",
  declined: "Declined",
  modified: "Modification requested",
};

/**
 * The core governance primitive: an agent-proposed action awaiting human
 * approval. Terminal-card aesthetic — 4px radius, border-only hover, mono
 * metadata. State machine: pending → approved | declined | modified.
 */
export function ActionCard({
  agentId,
  department,
  departmentColor,
  description,
  timestamp,
  title = "Proposed action",
  state: controlledState,
  onApprove,
  onDecline,
  onModify,
  allowFreeText = false,
  onFreeText,
  className,
}: ActionCardProps) {
  const [internalState, setInternalState] =
    useState<ActionCardState>("pending");
  const state = controlledState ?? internalState;

  const [mode, setMode] = useState<"actions" | "modify" | "freetext">(
    "actions",
  );
  const [draft, setDraft] = useState("");

  const accent = departmentColor ?? "var(--color-border-strong)";

  function resolve(next: Exclude<ActionCardState, "pending">) {
    if (controlledState == null) setInternalState(next);
  }

  function handleApprove() {
    resolve("approved");
    onApprove?.();
  }
  function handleDecline() {
    resolve("declined");
    onDecline?.();
  }
  function submitModify() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMode("actions");
    resolve("modified");
    onModify?.(text);
  }
  function submitFreeText() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMode("actions");
    onFreeText?.(text);
  }

  const actionBtn =
    "rounded-[4px] border border-border px-3 py-1.5 font-mono text-[11px] text-muted-foreground transition-colors duration-150 hover:border-border-strong hover:text-foreground";

  return (
    <div
      className={cn(
        "overflow-hidden rounded-[4px] border border-border bg-card",
        className,
      )}
    >
      {/* Header strip */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-[2px]"
          style={{ background: accent }}
        />
        <span className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
          {title}
        </span>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground tabular-nums">
          {formatTime(timestamp)}
        </span>
      </div>

      {/* Body */}
      <div className="px-3 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-[4px] border border-border bg-background px-1.5 py-0.5 font-mono text-[10px] text-foreground/80">
            {agentId}
          </span>
          <span
            className="rounded-[4px] border px-1.5 py-0.5 font-mono text-[10px] capitalize"
            style={{
              borderColor: `color-mix(in srgb, ${accent} 45%, transparent)`,
              color: accent,
            }}
          >
            {department}
          </span>
        </div>
        <p className="mt-2.5 text-[13px] leading-relaxed text-foreground/90">
          {description}
        </p>
      </div>

      {/* Footer — actions or resolution */}
      <div className="border-t border-border px-3 py-2.5">
        <AnimatePresence mode="wait" initial={false}>
          {state === "pending" ? (
            <motion.div
              key={`pending-${mode}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: EASE_ALTITUDE }}
            >
              {mode === "actions" ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    onClick={handleApprove}
                    className={cn(
                      actionBtn,
                      "border-blue/40 text-blue hover:border-blue hover:text-blue",
                    )}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode("modify")}
                    className={actionBtn}
                  >
                    Modify
                  </button>
                  <button
                    type="button"
                    onClick={handleDecline}
                    className={cn(actionBtn, "hover:border-destructive/50")}
                  >
                    Decline
                  </button>
                  {allowFreeText && (
                    <button
                      type="button"
                      onClick={() => setMode("freetext")}
                      className={cn(actionBtn, "ml-auto")}
                    >
                      Something else…
                    </button>
                  )}
                </div>
              ) : (
                <div className="flex items-start gap-1.5">
                  <textarea
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        if (mode === "modify") submitModify();
                        else submitFreeText();
                      }
                      if (e.key === "Escape") setMode("actions");
                    }}
                    rows={2}
                    placeholder={
                      mode === "modify"
                        ? "Describe the change…"
                        : "Tell the agent what you need…"
                    }
                    aria-label={
                      mode === "modify"
                        ? "Modification instruction"
                        : "Free-form instruction"
                    }
                    className="min-w-0 flex-1 resize-none rounded-[4px] border border-border bg-background px-2.5 py-1.5 text-[12px] leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-border-strong"
                  />
                  <div className="flex flex-col gap-1">
                    <button
                      type="button"
                      onClick={mode === "modify" ? submitModify : submitFreeText}
                      disabled={draft.trim().length === 0}
                      className={cn(actionBtn, "disabled:opacity-40")}
                    >
                      Send
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMode("actions");
                        setDraft("");
                      }}
                      className={actionBtn}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </motion.div>
          ) : (
            <motion.div
              key="resolved"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: EASE_ALTITUDE }}
              className="flex items-center gap-1.5 font-mono text-[11px]"
            >
              {state === "approved" ? (
                <>
                  <Check className="size-3.5 text-blue" strokeWidth={2} />
                  <span className="text-blue">{RESOLVED_LABEL.approved}</span>
                </>
              ) : state === "declined" ? (
                <>
                  <XIcon
                    className="size-3.5 text-muted-foreground"
                    strokeWidth={2}
                  />
                  <span className="text-muted-foreground">
                    {RESOLVED_LABEL.declined}
                  </span>
                </>
              ) : (
                <span className="text-muted-foreground">
                  {RESOLVED_LABEL.modified}
                </span>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

/* ──────────────────────────────────────────────
   GOVERNANCE QUEUE — a composable strip of
   pending ActionCards (dashboard "Pending
   Approvals" surface, Phase 4/6 wire it in).
────────────────────────────────────────────── */

export interface GovernanceQueueItem extends ActionCardProps {
  id: string;
}

export interface GovernanceQueueProps {
  items: GovernanceQueueItem[];
  /** Heading shown above the list. */
  label?: string;
  className?: string;
}

export function GovernanceQueue({
  items,
  label = "Pending Approvals",
  className,
}: GovernanceQueueProps) {
  return (
    <div className={className}>
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
          {label}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
          {items.length}
        </span>
      </div>
      {items.length === 0 ? (
        <div className="rounded-[4px] border border-border bg-card px-4 py-6 text-center font-mono text-[11px] text-muted-foreground">
          Nothing awaiting approval.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {items.map((item) => (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: EASE_ALTITUDE }}
              >
                <ActionCard {...item} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
