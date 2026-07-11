"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { StageStatus } from "@/lib/workflow-build";

export interface WorkflowBuildDialProps {
  /** Exactly four stage states, in order. */
  stages: [StageStatus, StageStatus, StageStatus, StageStatus];
  className?: string;
}

const STAGE_LABELS = ["Understand", "Plan", "Build", "Verify"] as const;

const ERROR_COLOR = "#EF4444";
const COMPLETE_COLOR = "#0047FF";
const PENDING_COLOR = "var(--border-strong)";

const SIZE = 148;
const R = 56;
const STROKE = 10;
const GAP_DEG = 10; // gap between quadrant segments

/** Arc path for one quadrant segment (90° minus the gap). */
function segmentPath(index: number): string {
  const start = -90 + index * 90 + GAP_DEG / 2;
  const end = -90 + (index + 1) * 90 - GAP_DEG / 2;
  const c = SIZE / 2;
  const rad = (d: number) => (d * Math.PI) / 180;
  const x1 = c + R * Math.cos(rad(start));
  const y1 = c + R * Math.sin(rad(start));
  const x2 = c + R * Math.cos(rad(end));
  const y2 = c + R * Math.sin(rad(end));
  return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
}

function colorFor(status: StageStatus): string {
  switch (status) {
    case "complete":
      return COMPLETE_COLOR;
    case "active":
      return COMPLETE_COLOR;
    case "error":
      return ERROR_COLOR;
    default:
      return PENDING_COLOR;
  }
}

/**
 * The 4-stage build dial. Segments are discrete stage states — never a
 * smooth percentage fill. Active pulses via opacity only (no scale).
 */
export function WorkflowBuildDial({ stages, className }: WorkflowBuildDialProps) {
  const completed = stages.filter((s) => s === "complete").length;
  const errored = stages.some((s) => s === "error");
  const activeIndex = stages.findIndex((s) => s === "active");

  const centerLabel = errored
    ? "ERROR"
    : completed === 4
      ? "READY"
      : activeIndex >= 0
        ? STAGE_LABELS[activeIndex].toUpperCase()
        : completed > 0
          ? "GATED"
          : "IDLE";

  return (
    <div className={cn("flex flex-col items-center gap-3", className)}>
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={`Workflow build progress: stage states ${stages.join(", ")}`}
      >
        {stages.map((status, i) => {
          const base = (
            <path
              key={`seg-${i}`}
              d={segmentPath(i)}
              fill="none"
              stroke={colorFor(status)}
              strokeWidth={STROKE}
              strokeLinecap="butt"
              opacity={status === "pending" ? 0.35 : 1}
            />
          );
          if (status === "active") {
            return (
              <motion.path
                key={`seg-${i}`}
                d={segmentPath(i)}
                fill="none"
                stroke={colorFor(status)}
                strokeWidth={STROKE}
                strokeLinecap="butt"
                animate={{ opacity: [0.35, 1, 0.35] }}
                transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
              />
            );
          }
          return base;
        })}

        {/* center readout */}
        <text
          x={SIZE / 2}
          y={SIZE / 2 - 4}
          textAnchor="middle"
          className="fill-foreground font-mono"
          style={{ fontSize: 13, letterSpacing: "0.12em" }}
        >
          {centerLabel}
        </text>
        <text
          x={SIZE / 2}
          y={SIZE / 2 + 14}
          textAnchor="middle"
          className="fill-current font-mono"
          style={{
            fontSize: 10,
            letterSpacing: "0.06em",
            fill: errored ? ERROR_COLOR : "var(--color-muted-foreground)",
          }}
        >
          {completed}/4
        </text>
      </svg>

      {/* stage legend */}
      <div className="grid grid-cols-4 gap-2">
        {stages.map((status, i) => (
          <div key={STAGE_LABELS[i]} className="flex flex-col items-center gap-1">
            <span
              className="font-mono text-[9px] tracking-[0.1em] uppercase"
              style={{
                color:
                  status === "pending"
                    ? "var(--color-muted-foreground)"
                    : colorFor(status),
              }}
            >
              {STAGE_LABELS[i]}
            </span>
            <span className="font-mono text-[9px] text-muted-foreground">
              {status === "complete" ? "✓" : status === "error" ? "✕" : status === "active" ? "…" : "·"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
