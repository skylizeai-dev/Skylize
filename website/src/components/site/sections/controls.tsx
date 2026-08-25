"use client";

import { useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { Display, Eyebrow, Section, SectionBody, SpecTable } from "../primitives";

/**
 * Five enforcement points. Every `specs` row states a property of the
 * architecture — what signs it, where it is checked, what happens when the
 * check fails. None of them is a benchmark, an adoption number, or an outcome
 * claim, and none should become one: this section is the page's answer to
 * "prove it", so it can only carry facts that hold before a first customer.
 */
const CONTROLS = [
  {
    label: "Scoped tokens",
    title: "A token names exactly what may happen.",
    body: "Scope is written into the grant and signed. An agent holding a token for one action cannot present it for another, and the token stops working when the grant expires.",
    specs: [
      { k: "Signature", v: "ECDSA P-384" },
      { k: "Verified at", v: "Call site" },
      { k: "Scope", v: "Per grant" },
      { k: "Failure mode", v: "Closed" },
    ],
  },
  {
    label: "Budget ceilings",
    title: "Spend is capped before execution.",
    body: "A ceiling travels with the token and is checked before the action runs. Nothing is reconciled after the money has moved — an action that would exceed the ceiling is refused.",
    specs: [
      { k: "Enforced", v: "Pre-execution" },
      { k: "Ceiling", v: "Per token" },
      { k: "Over ceiling", v: "Refused" },
      { k: "Failure mode", v: "Closed" },
    ],
  },
  {
    label: "Approval gates",
    title: "Nothing activates until a person approves.",
    body: "Gated scopes stay inactive until a named human approves them. The approval is recorded as part of the delegation chain, so the authorising party is attributable later.",
    specs: [
      { k: "State until approval", v: "Inactive" },
      { k: "Approver", v: "Named human" },
      { k: "Record", v: "Delegation chain" },
      { k: "Failure mode", v: "Closed" },
    ],
  },
  {
    label: "Kill switch",
    title: "One halt, everything stops.",
    body: "The kill switch stops agent activity system-wide and refuses new tokens. Operators trigger it from the Command Console; the halt and its cause land in the audit log.",
    specs: [
      { k: "Scope", v: "System-wide" },
      { k: "Effect", v: "Immediate halt" },
      { k: "New tokens", v: "Refused" },
      { k: "Recorded", v: "Audit log" },
    ],
  },
  {
    label: "Tenant isolation",
    title: "Tenants share nothing.",
    body: "Keys and grants are held per tenant, and a token issued in one tenant does not verify in another. Attribution follows the delegation chain rather than a shared service identity.",
    specs: [
      { k: "Boundary", v: "Per tenant" },
      { k: "Keys", v: "Per tenant" },
      { k: "Cross-tenant", v: "Refused" },
      { k: "Attribution", v: "Delegation chain" },
    ],
  },
] as const;

export function Controls() {
  const [active, setActive] = useState(0);
  const baseId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Roving focus: arrows move between tabs, Home/End jump to the ends.
  function onKeyDown(e: React.KeyboardEvent) {
    const last = CONTROLS.length - 1;
    let next: number | null = null;
    if (e.key === "ArrowRight") next = active === last ? 0 : active + 1;
    else if (e.key === "ArrowLeft") next = active === 0 ? last : active - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next === null) return;
    e.preventDefault();
    setActive(next);
    tabRefs.current[next]?.focus();
  }

  const control = CONTROLS[active];

  return (
    <Section id="controls">
      <SectionBody>
        <Eyebrow index="04">Controls</Eyebrow>
        <Display className="mt-[18px] max-w-[20ch]">
          Five enforcement points.
        </Display>

        <div
          role="tablist"
          aria-label="Enforcement points"
          onKeyDown={onKeyDown}
          className="mt-[clamp(2rem,4vw,3.25rem)] flex flex-wrap border-b border-border"
        >
          {CONTROLS.map((c, i) => {
            const selected = i === active;
            return (
              <button
                key={c.label}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`${baseId}-tab-${i}`}
                aria-selected={selected}
                aria-controls={`${baseId}-panel-${i}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => setActive(i)}
                className={cn(
                  "-mb-px cursor-pointer border-b-2 py-3 pr-[18px] mr-[18px] font-mono text-[11px] tracking-[0.12em] uppercase transition-colors duration-200",
                  selected
                    ? "border-blue text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {c.label}
              </button>
            );
          })}
        </div>

        <div
          role="tabpanel"
          id={`${baseId}-panel-${active}`}
          aria-labelledby={`${baseId}-tab-${active}`}
          className="grid grid-cols-1 items-start gap-[clamp(1.75rem,4vw,4.5rem)] pt-[clamp(1.75rem,3.5vw,2.75rem)] md:grid-cols-2"
        >
          <div>
            <Display as="h3" size="sub" className="max-w-[16ch]">
              {control.title}
            </Display>
            <p className="mt-[18px] max-w-[60ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
              {control.body}
            </p>
          </div>
          <SpecTable rows={control.specs} />
        </div>
      </SectionBody>
    </Section>
  );
}
