"use client";

import { useId, useState } from "react";
import { Display, Eyebrow, Section, SectionBody } from "../primitives";

/**
 * The objections an evaluator actually raises about a pre-revenue governance
 * vendor. Nothing here may claim a certification, a customer, or a measured
 * result — where an answer needs a trust signal, it names a mechanism.
 */
const FAQS = [
  {
    q: "You are not SOC 2 certified. Why should we take security seriously here?",
    a: "We are not certified, and we will not imply otherwise anywhere on this page. What we can point at is the architecture: permissions are tokens signed with ECDSA P-384 and verified at the call site, the failure mode is closed, and every action is attributable through its delegation chain. Certification is a process we will go through — it is not the thing doing the enforcing.",
  },
  {
    q: "What does a design partner commit to?",
    a: "A real agent workload to enforce against, one engineering contact, and honest feedback on a regular cadence. No fee, and no obligation to buy anything afterwards.",
  },
  {
    q: "How long until this is production-ready?",
    a: "The console screens on this page run today; the enforcement layer is in build. Production readiness is scoped with each design partner against their own deployment, because that is the only honest way to answer it. We will not quote a date we cannot hold.",
  },
  {
    q: "What does it cost?",
    a: "Pricing is custom and scoped to the deployment. Design partners are not charged during the partnership.",
  },
  {
    q: "What happens when a token fails to verify?",
    a: "Nothing happens. The action is refused before it reaches your systems, and the refusal is written to the audit log with its delegation chain, so you can see what was attempted and under whose authority.",
  },
  {
    q: "Where does the layer sit in our stack?",
    a: "Between the agent and the systems it calls. Actions route through the enforcement layer, which verifies the token and then either passes the call through or refuses it. Skylize holds the grants and the log, not your data.",
  },
] as const;

export function Faq() {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const baseId = useId();

  return (
    <Section id="faq">
      <SectionBody>
        <Eyebrow index="06">Questions</Eyebrow>
        <div className="mt-[18px] grid grid-cols-1 items-start gap-[clamp(1.75rem,4vw,4.5rem)] md:grid-cols-2">
          <Display className="max-w-[16ch]">The objections, answered.</Display>

          <div className="flex flex-col">
            {FAQS.map((item, i) => {
              const isOpen = !!open[i];
              return (
                <div key={item.q} className="border-t border-border">
                  <h3>
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-controls={`${baseId}-panel-${i}`}
                      id={`${baseId}-trigger-${i}`}
                      onClick={() =>
                        setOpen((s) => ({ ...s, [i]: !s[i] }))
                      }
                      className="flex w-full cursor-pointer items-baseline justify-between gap-5 py-[22px] text-left text-[1.06rem] leading-[1.45] font-medium text-foreground"
                    >
                      <span className="max-w-[46ch]">{item.q}</span>
                      <span
                        aria-hidden
                        className="font-mono text-sm text-blue"
                      >
                        {isOpen ? "−" : "+"}
                      </span>
                    </button>
                  </h3>
                  <div
                    id={`${baseId}-panel-${i}`}
                    role="region"
                    aria-labelledby={`${baseId}-trigger-${i}`}
                    hidden={!isOpen}
                  >
                    <p className="max-w-[62ch] pb-6 text-[1.03rem] leading-[1.7] text-muted-foreground">
                      {item.a}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </SectionBody>
    </Section>
  );
}
