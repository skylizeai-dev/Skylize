"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { AltitudeLine } from "@/components/skylize";
import { EASE_ALTITUDE, viewportOnce } from "@/lib/motion";

/* ── Operational systems shown as autonomous agents ───────────── */
const agents = [
  { name: "Revenue Ops", status: "executing", load: 0.82 },
  { name: "Lead Routing", status: "executing", load: 0.64 },
  { name: "Support Triage", status: "idle", load: 0.41 },
  { name: "Data Sync", status: "executing", load: 0.93 },
  { name: "Reporting", status: "queued", load: 0.27 },
] as const;

const statusColor: Record<string, string> = {
  executing: "var(--color-blue)",
  idle: "color-mix(in srgb, var(--color-paper) 40%, transparent)",
  queued: "color-mix(in srgb, var(--color-paper) 22%, transparent)",
};

/* The ascending performance curve — throughput climbing over time. */
const CURVE =
  "M 0 196 C 64 196 92 168 150 170 C 212 172 232 138 300 126 C 362 115 384 94 444 80 C 496 68 524 50 560 34";
const AREA = `${CURVE} L 560 220 L 0 220 Z`;
const FLOORS = [36, 76, 116, 156, 196];

export function AltitudeDashboard({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-xl border border-border bg-card",
        "shadow-[0_40px_120px_-40px_rgba(0,0,0,0.8)]",
        className,
      )}
    >
      {/* Faint instrument grid */}
      <div className="altitude-grid pointer-events-none absolute inset-0 opacity-[0.35]" />

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="relative flex items-center justify-between gap-4 px-5 py-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span className="relative flex size-2">
            <span
              className="absolute inline-flex h-full w-full rounded-full opacity-60"
              style={{ background: "var(--color-blue)" }}
            />
            <motion.span
              className="absolute inline-flex h-full w-full rounded-full"
              style={{ background: "var(--color-blue)" }}
              animate={{ scale: [1, 2.4], opacity: [0.5, 0] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: "easeOut" }}
            />
            <span
              className="relative inline-flex size-2 rounded-full"
              style={{ background: "var(--color-blue)" }}
            />
          </span>
          <span className="font-mono text-[11px] tracking-[0.18em] text-foreground uppercase">
            Operational&nbsp;Altitude
          </span>
        </div>
        <div className="flex items-center gap-4 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase">
          <span className="hidden sm:inline">12 agents</span>
          <span className="text-blue/90">autonomous</span>
        </div>
      </div>

      <AltitudeLine variant="solid" />

      {/* ── Body ───────────────────────────────────────────── */}
      <div className="relative grid grid-cols-1 lg:grid-cols-[1.5fr_1fr]">
        {/* Performance — ascending throughput */}
        <div className="relative p-5 sm:p-6">
          <div className="mb-4 flex items-baseline justify-between">
            <span className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
              Throughput / week
            </span>
            <span className="font-mono text-xs text-foreground tabular-nums">
              +218%
            </span>
          </div>

          <div className="relative">
            {/* y-axis altitude ticks */}
            <div className="pointer-events-none absolute -left-1 top-0 flex h-full flex-col justify-between py-[2px] font-mono text-[9px] text-muted-foreground/70 tabular-nums">
              {["10k", "7.5k", "5k", "2.5k", "0"].map((t) => (
                <span key={t}>{t}</span>
              ))}
            </div>

            <svg
              viewBox="0 0 560 220"
              preserveAspectRatio="none"
              className="h-40 w-full pl-7 sm:h-48"
              aria-hidden
            >
              {/* altitude floors */}
              {FLOORS.map((y) => (
                <line
                  key={y}
                  x1="0"
                  x2="560"
                  y1={y}
                  y2={y}
                  stroke="var(--border)"
                  strokeWidth="1"
                  shapeRendering="crispEdges"
                />
              ))}

              {/* faint area under the curve (flat fill, no gradient) */}
              <motion.path
                d={AREA}
                fill="color-mix(in srgb, var(--color-blue) 9%, transparent)"
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                viewport={viewportOnce}
                transition={{ duration: 1, delay: 0.6, ease: EASE_ALTITUDE }}
              />

              {/* the ascending performance line */}
              <motion.path
                d={CURVE}
                fill="none"
                stroke="var(--color-blue)"
                strokeWidth="1.75"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
                initial={{ pathLength: 0 }}
                whileInView={{ pathLength: 1 }}
                viewport={viewportOnce}
                transition={{ duration: 1.6, ease: EASE_ALTITUDE }}
              />

              {/* leading node */}
              <motion.circle
                cx="560"
                cy="34"
                r="3.5"
                fill="var(--color-blue)"
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                viewport={viewportOnce}
                transition={{ duration: 0.4, delay: 1.5 }}
              />
            </svg>
          </div>
        </div>

        {/* Agents — operational systems running */}
        <div className="border-t border-border p-5 sm:p-6 lg:border-t-0 lg:border-l lg:border-border">
          <div className="mb-4 flex items-baseline justify-between">
            <span className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
              Systems
            </span>
            <span className="font-mono text-[11px] text-muted-foreground/70 uppercase">
              live
            </span>
          </div>

          <motion.ul
            className="flex flex-col gap-3.5"
            initial="hidden"
            whileInView="show"
            viewport={viewportOnce}
            variants={{ hidden: {}, show: { transition: { staggerChildren: 0.09, delayChildren: 0.3 } } }}
          >
            {agents.map((agent) => (
              <motion.li
                key={agent.name}
                variants={{
                  hidden: { opacity: 0, y: 10 },
                  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_ALTITUDE } },
                }}
                className="flex items-center gap-3"
              >
                <span
                  className="size-1.5 shrink-0 rounded-full"
                  style={{ background: statusColor[agent.status] }}
                />
                <span className="w-24 shrink-0 truncate font-mono text-[11px] text-foreground">
                  {agent.name}
                </span>
                <span className="relative h-1 flex-1 overflow-hidden rounded-full bg-foreground/[0.08]">
                  <motion.span
                    className="absolute inset-y-0 left-0 rounded-full"
                    style={{
                      background:
                        agent.status === "executing"
                          ? "var(--color-blue)"
                          : "color-mix(in srgb, var(--color-paper) 35%, transparent)",
                    }}
                    initial={{ width: 0 }}
                    whileInView={{ width: `${agent.load * 100}%` }}
                    viewport={viewportOnce}
                    transition={{ duration: 1, delay: 0.5, ease: EASE_ALTITUDE }}
                  />
                </span>
                <span className="w-8 shrink-0 text-right font-mono text-[10px] text-muted-foreground tabular-nums">
                  {Math.round(agent.load * 100)}
                </span>
              </motion.li>
            ))}
          </motion.ul>
        </div>
      </div>

      <AltitudeLine variant="solid" />

      {/* ── Footer metrics ─────────────────────────────────── */}
      <div className="relative grid grid-cols-3 divide-x divide-border">
        {[
          { value: "1,284", label: "tasks / day" },
          { value: "37ms", label: "p50 latency" },
          { value: "99.98%", label: "uptime" },
        ].map((m) => (
          <div key={m.label} className="px-5 py-4 sm:px-6">
            <div className="font-display text-lg text-foreground tabular-nums sm:text-xl">
              {m.value}
            </div>
            <div className="mt-0.5 font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
              {m.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
