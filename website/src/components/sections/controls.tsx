"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { KeyRound, Gauge, UserCheck, OctagonX, Building2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Container, Eyebrow, AltitudeLine, Reveal } from "@/components/skylize";
import { EASE_ALTITUDE } from "@/lib/motion";

/**
 * Every `specs` entry below states a property of the architecture — what is
 * signed, where it is checked, what it is stored in. None of them are
 * performance results, adoption figures, or outcome claims, and none should
 * become one: this section is the site's answer to "prove it", so it can only
 * carry facts that hold before a single customer exists.
 */
const controls = [
  {
    id: "tokens",
    icon: KeyRound,
    name: "Scoped tokens",
    role: "An agent's authority is an explicit, signed grant — never an inherited API key.",
    capabilities: [
      "Per-action tool scope",
      "Short expiry window",
      "Anti-replay nonce",
      "Signed delegation chain",
    ],
    specs: [
      { label: "Signature", value: "ECDSA P-384" },
      { label: "Checked", value: "At the call site" },
    ],
  },
  {
    id: "budgets",
    icon: Gauge,
    name: "Budget ceilings",
    role: "The spend limit travels inside the token, so it binds at execution instead of at reconciliation.",
    capabilities: [
      "Ceiling carried in the token",
      "Per-run token accounting",
      "Cost recorded per action",
      "Run halts at exhaustion",
    ],
    specs: [
      { label: "Enforced", value: "Before the call" },
      { label: "Cost ledger", value: "Append-only" },
    ],
  },
  {
    id: "approval",
    icon: UserCheck,
    name: "Approval gates",
    role: "Any class of action can require a named human before it is allowed to execute.",
    capabilities: [
      "Route action classes to a human",
      "Agent halts pending decision",
      "Approve or reject with reason",
      "Decision joins the audit record",
    ],
    specs: [
      { label: "Gate", value: "Blocking" },
      { label: "Acts for", value: "A named principal" },
    ],
  },
  {
    id: "kill-switch",
    icon: OctagonX,
    name: "Kill switch",
    role: "One control that stops an agent, a department, or an entire tenant from acting.",
    capabilities: [
      "Halt one agent or a whole tenant",
      "Revoke authority by token id",
      "Consulted on every governed action",
      "State persists across restart",
    ],
    specs: [
      { label: "Failure mode", value: "Closed" },
      { label: "Scope", value: "Agent to tenant" },
    ],
  },
  {
    id: "isolation",
    icon: Building2,
    name: "Tenant isolation",
    role: "One tenant's agents, credentials, and audit history are not reachable from another's.",
    capabilities: [
      "Row-level isolation per tenant",
      "Tenant-scoped credential access",
      "No cross-tenant token validity",
      "Enforced in the data layer",
    ],
    specs: [
      { label: "Boundary", value: "Database-enforced" },
      { label: "Model", value: "Multi-tenant" },
    ],
  },
];

export function Controls() {
  const [active, setActive] = useState(controls[0].id);
  const control = controls.find((c) => c.id === active)!;
  const ActiveIcon = control.icon;

  return (
    <section id="controls" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="04">The controls</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            Five enforcement points, not five dashboards.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
            Each control is a place where an action can be refused. They
            compose: a call must be in scope, under budget, past its gate, not
            killed, and inside its tenant — or it does not happen.
          </p>
        </Reveal>

        <Reveal delay={0.05} className="mt-14">
          <div className="grid grid-cols-1 overflow-hidden rounded-xl border border-border lg:grid-cols-[320px_1fr]">
            {/* Selector */}
            <ul className="flex flex-col">
              {controls.map((c, i) => {
                const Icon = c.icon;
                const isActive = c.id === active;
                return (
                  <li key={c.id} className={i > 0 ? "border-t border-border" : ""}>
                    <button
                      type="button"
                      onClick={() => setActive(c.id)}
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
                            isActive
                              ? "text-foreground"
                              : "text-muted-foreground group-hover:text-foreground",
                          )}
                        >
                          {c.name}
                        </span>
                      </span>
                      {isActive && (
                        <motion.span
                          layoutId="control-active"
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
                  key={control.id}
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
                      enforcement point
                    </span>
                  </div>

                  <h3 className="mt-6 text-3xl font-semibold tracking-tight text-foreground">
                    {control.name}
                  </h3>
                  <p className="mt-3 max-w-md text-lg leading-relaxed text-muted-foreground">
                    {control.role}
                  </p>

                  <AltitudeLine className="my-8" />

                  <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                    {control.capabilities.map((cap) => (
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

                  {/* Spec strip — architecture facts, deliberately not numbers */}
                  <dl className="mt-9 flex flex-wrap gap-x-12 gap-y-5 border-t border-border pt-7">
                    {control.specs.map((s) => (
                      <div key={s.label}>
                        <dt className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                          {s.label}
                        </dt>
                        <dd className="mt-1.5 font-mono text-base text-foreground">{s.value}</dd>
                      </div>
                    ))}
                  </dl>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
