"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { LineChart, LifeBuoy, Cog, Wallet } from "lucide-react";
import { cn } from "@/lib/utils";
import { Container, Eyebrow, AltitudeLine, Reveal } from "@/components/skylize";
import { EASE_ALTITUDE } from "@/lib/motion";

const agents = [
  {
    id: "revenue",
    icon: LineChart,
    name: "Revenue Agent",
    role: "Owns pipeline hygiene, routing, and follow-up — so no opportunity ever goes cold.",
    capabilities: [
      "Lead scoring & routing",
      "Follow-up sequencing",
      "CRM enrichment",
      "Forecast updates",
    ],
    metrics: [
      { value: "−71%", label: "lead response time" },
      { value: "+24%", label: "pipeline coverage" },
    ],
  },
  {
    id: "support",
    icon: LifeBuoy,
    name: "Support Agent",
    role: "Triages, resolves, and escalates with full context — deflecting volume without dropping quality.",
    capabilities: [
      "Ticket triage",
      "Drafted resolutions",
      "Escalation routing",
      "SLA monitoring",
    ],
    metrics: [
      { value: "−64%", label: "avg handle time" },
      { value: "98%", label: "CSAT maintained" },
    ],
  },
  {
    id: "operations",
    icon: Cog,
    name: "Operations Agent",
    role: "Runs the recurring back-office work — reconciliation, syncs, reporting — on schedule, every time.",
    capabilities: [
      "Data reconciliation",
      "Cross-system sync",
      "Scheduled reporting",
      "Exception handling",
    ],
    metrics: [
      { value: "1,200+", label: "tasks / week" },
      { value: "0", label: "missed runs" },
    ],
  },
  {
    id: "finance",
    icon: Wallet,
    name: "Finance Agent",
    role: "Keeps the numbers current — invoicing, collections, and anomaly detection, without the spreadsheet sprawl.",
    capabilities: [
      "Invoice generation",
      "Collections follow-up",
      "Spend anomaly alerts",
      "Month-end close prep",
    ],
    metrics: [
      { value: "−9 days", label: "time to close" },
      { value: "+18%", label: "on-time collections" },
    ],
  },
];

export function Agents() {
  const [active, setActive] = useState(agents[0].id);
  const agent = agents.find((a) => a.id === active)!;
  const ActiveIcon = agent.icon;

  return (
    <section id="agents" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="04">AI agents</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            A workforce of agents, each accountable for an outcome.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
            Not a single chatbot. A coordinated system of specialists — scoped,
            measured, and supervised — that runs the operation alongside your
            team.
          </p>
        </Reveal>

        <Reveal delay={0.05} className="mt-14">
          <div className="grid grid-cols-1 overflow-hidden rounded-xl border border-border lg:grid-cols-[320px_1fr]">
            {/* Selector */}
            <ul className="flex flex-col">
              {agents.map((a, i) => {
                const Icon = a.icon;
                const isActive = a.id === active;
                return (
                  <li key={a.id} className={i > 0 ? "border-t border-border" : ""}>
                    <button
                      type="button"
                      onClick={() => setActive(a.id)}
                      aria-pressed={isActive}
                      className={cn(
                        "group flex w-full items-center gap-4 px-6 py-5 text-left transition-colors duration-200",
                        isActive ? "bg-accent" : "hover:bg-accent/60",
                      )}
                    >
                      <span
                        className={cn(
                          "flex size-9 shrink-0 items-center justify-center rounded-md border transition-colors duration-200",
                          isActive
                            ? "border-blue/50 text-blue"
                            : "border-border text-muted-foreground group-hover:text-foreground",
                        )}
                      >
                        <Icon className="size-[17px]" strokeWidth={1.75} />
                      </span>
                      <span className="flex-1">
                        <span
                          className={cn(
                            "block text-[0.95rem] font-medium tracking-tight transition-colors",
                            isActive ? "text-foreground" : "text-muted-foreground group-hover:text-foreground",
                          )}
                        >
                          {a.name}
                        </span>
                      </span>
                      {isActive && (
                        <motion.span
                          layoutId="agent-active"
                          className="h-5 w-0.5 rounded-full"
                          style={{ background: "var(--color-blue)" }}
                        />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>

            {/* Detail panel */}
            <div className="relative border-t border-border bg-card p-7 sm:p-10 lg:border-t-0 lg:border-l lg:border-border">
              <AnimatePresence mode="wait">
                <motion.div
                  key={agent.id}
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.4, ease: EASE_ALTITUDE }}
                >
                  <div className="flex items-center gap-3">
                    <span className="flex size-11 items-center justify-center rounded-md border border-blue/40 text-blue">
                      <ActiveIcon className="size-5" strokeWidth={1.75} />
                    </span>
                    <span className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
                      autonomous · supervised
                    </span>
                  </div>

                  <h3 className="mt-6 text-3xl font-semibold tracking-tight text-foreground">
                    {agent.name}
                  </h3>
                  <p className="mt-3 max-w-md text-lg leading-relaxed text-muted-foreground">
                    {agent.role}
                  </p>

                  <AltitudeLine className="my-8" />

                  <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                    {agent.capabilities.map((cap) => (
                      <div key={cap} className="flex items-center gap-3">
                        <span
                          aria-hidden
                          className="size-1 shrink-0 rounded-full"
                          style={{ background: "var(--color-blue)" }}
                        />
                        <span className="font-mono text-[13px] text-foreground/90">{cap}</span>
                      </div>
                    ))}
                  </div>

                  <div className="mt-9 flex flex-wrap gap-8">
                    {agent.metrics.map((m) => (
                      <div key={m.label}>
                        <div className="font-display text-2xl font-semibold text-foreground tabular-nums">
                          {m.value}
                        </div>
                        <div className="mt-1 font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                          {m.label}
                        </div>
                      </div>
                    ))}
                  </div>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
