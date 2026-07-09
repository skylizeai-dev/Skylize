"use client";

/**
 * HERO DEMO — a compressed, read-only-safe run of the real console flow.
 *
 * Reuses the REAL WorkflowBuildDial and ActionCard components (no parallel
 * copies to drift), driven by a scripted local-state timeline. This is the
 * ONE surface where simulated timing is acceptable: a public marketing page
 * with no backend action behind it.
 *
 * PROVABLY INERT: this module performs no fetch/XHR and never imports the
 * live build controller (lib/workflow-build's startWorkflowBuild / API
 * bridge). Only the PURE routing helpers (routeGoal, chainForDepartment —
 * plain functions over static generated data) and presentational components
 * are used. Every ActionCard callback mutates local demo state only.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { EASE_ALTITUDE } from "@/lib/motion";
import { AGENTS } from "@/components/console/agent-network.data";
import { departmentColor, DEPARTMENTS } from "@/components/console/department-color";
import { ActionCard } from "@/components/console/action-card";
import { WorkflowBuildDial } from "@/components/console/workflow-build-dial";
import { chainForDepartment, routeGoal, type StageStatus } from "@/lib/workflow-build";

const PRESETS = [
  "Send me a daily summary of overdue tasks",
  "Nurture new leads with a 5-touch email sequence",
  "Flag invoices overdue by 30+ days",
] as const;

type Stages = [StageStatus, StageStatus, StageStatus, StageStatus];
type DemoPhase = "idle" | "running" | "gated" | "approved" | "declined";

const IDLE_STAGES: Stages = ["pending", "pending", "pending", "pending"];

const agentById = new Map(AGENTS.map((a) => [a.id, a]));

export function HeroWorkflowDemo() {
  const [goal, setGoal] = useState<string | null>(null);
  const [stages, setStages] = useState<Stages>(IDLE_STAGES);
  const [phase, setPhase] = useState<DemoPhase>("idle");
  const [department, setDepartment] = useState<string | null>(null);
  const [chain, setChain] = useState<string[]>([]);
  const [gatedAt, setGatedAt] = useState<number | null>(null);
  const timers = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    for (const t of timers.current) window.clearTimeout(t);
    timers.current = [];
  }, []);
  useEffect(() => clearTimers, [clearTimers]);

  const later = useCallback((ms: number, fn: () => void) => {
    timers.current.push(window.setTimeout(fn, ms));
  }, []);

  function run(preset: string) {
    clearTimers();
    setGoal(preset);
    setPhase("running");
    setChain([]);
    setDepartment(null);
    setGatedAt(null);
    setStages(["active", "pending", "pending", "pending"]);

    const dept = routeGoal(preset);
    const agentChain = chainForDepartment(dept);

    later(700, () => {
      setDepartment(dept);
      setChain(agentChain);
      setStages(["complete", "active", "pending", "pending"]);
    });
    later(1700, () => setStages(["complete", "complete", "active", "pending"]));
    later(2700, () => {
      setStages(["complete", "complete", "complete", "pending"]);
      setPhase("gated");
      setGatedAt(Date.now());
    });
  }

  function approve() {
    setPhase("approved");
    setStages(["complete", "complete", "complete", "active"]);
    later(700, () => setStages(["complete", "complete", "complete", "complete"]));
  }

  function decline() {
    setPhase("declined");
  }

  function reset() {
    clearTimers();
    setGoal(null);
    setStages(IDLE_STAGES);
    setPhase("idle");
    setDepartment(null);
    setChain([]);
    setGatedAt(null);
  }

  const dept = DEPARTMENTS.find((d) => d.id === department);
  const director =
    chain.map((id) => agentById.get(id)).find((a) => a?.authority === "director") ??
    agentById.get(chain[chain.length - 1] ?? "");

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card text-left">
      {/* Console chrome strip */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="flex items-center gap-2 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
          <span className="size-1.5 rounded-full" style={{ background: "var(--color-blue)" }} />
          Skylize Console — Live Demo
        </span>
        {goal != null && (
          <button
            type="button"
            onClick={reset}
            className="font-mono text-[10px] tracking-[0.1em] text-muted-foreground uppercase transition-colors duration-150 hover:text-foreground"
          >
            Reset
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 p-4 sm:p-5 lg:grid-cols-[1fr_200px_1fr]">
        {/* Left — goal presets */}
        <div className="flex flex-col gap-2">
          <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
            1 · Pick a business goal
          </span>
          {PRESETS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => run(p)}
              className={cn(
                "rounded-[4px] border px-3 py-2 text-left text-[13px] leading-snug transition-colors duration-150",
                goal === p
                  ? "border-blue/50 bg-blue/[0.06] text-foreground"
                  : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
              )}
            >
              {p}
            </button>
          ))}
          {dept && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[10px] tracking-[0.1em] text-muted-foreground uppercase">
                Routed to
              </span>
              <span
                className="rounded-[4px] border px-1.5 py-0.5 font-mono text-[10px]"
                style={{
                  borderColor: `color-mix(in srgb, ${departmentColor(dept.id)} 45%, transparent)`,
                  color: departmentColor(dept.id),
                }}
              >
                {dept.name}
              </span>
            </div>
          )}
          {chain.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              {chain.map((id, i) => (
                <span key={id} className="flex items-center gap-1">
                  {i > 0 && <span className="text-[10px] text-muted-foreground/50">→</span>}
                  <span className="rounded-[4px] border border-border bg-background px-1.5 py-0.5 font-mono text-[9px] text-foreground/75">
                    {agentById.get(id)?.name ?? id}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Center — the real dial component */}
        <div className="flex flex-col items-center justify-center gap-2">
          <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
            2 · Watch it build
          </span>
          <WorkflowBuildDial stages={stages} />
        </div>

        {/* Right — the real governance gate */}
        <div className="flex flex-col gap-2">
          <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
            3 · You stay in control
          </span>
          {phase === "gated" || phase === "approved" || phase === "declined" ? (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24, ease: EASE_ALTITUDE }}
            >
              <ActionCard
                agentId={director?.id ?? "director"}
                department={department ?? ""}
                departmentColor={departmentColor(department ?? "")}
                description="Approve to activate this workflow. Nothing runs in your stack until you say so."
                timestamp={gatedAt ?? 0}
                title="Activation gate"
                onApprove={approve}
                onDecline={decline}
                onModify={() => decline()}
              />
              {phase === "approved" && stages[3] === "complete" && (
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                  Activated. In the product this is a real n8n workflow — with a
                  signed governance token.
                </p>
              )}
              {phase === "declined" && (
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                  Declined — the draft is discarded. Control stays with you.
                </p>
              )}
            </motion.div>
          ) : (
            <div className="flex h-full min-h-24 items-center justify-center rounded-[4px] border border-dashed border-border px-3 py-4">
              <span className="text-center font-mono text-[11px] leading-relaxed text-muted-foreground/70">
                {phase === "running"
                  ? "your org is working…"
                  : "the approval gate appears here"}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
