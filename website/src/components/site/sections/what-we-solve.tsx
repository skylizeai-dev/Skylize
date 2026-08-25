import { Reveal } from "@/components/skylize";
import { Display, Eyebrow, Lede, MonoLabel, Section, SectionBody } from "../primitives";

/**
 * The two modes of agent work. The point of the section is that they are NOT
 * governed differently — looped and one-off work pass the same check — so the
 * two columns are deliberately symmetric in weight and structure.
 */
const modes = [
  {
    label: "Inside the loop",
    title: "Automated doesn't mean ungoverned.",
    body: "Repetitive, always-on operational work runs continuously through the automation layer. Every action it takes still carries a signed token naming its scope, its ceiling and its expiry — the same one a person's request would carry.",
  },
  {
    label: "Outside the loop",
    title: "No waiting for the next cycle, no blank context.",
    body: "A one-off ask, a draft, a question — handled by an agent grounded in the same live, continuously synced memory the automation layer keeps, scoped to what that person is permitted to see. Nothing has to be re-explained to a fresh assistant.",
  },
] as const;

export function WhatWeSolve() {
  return (
    <Section id="what-we-solve">
      <SectionBody>
        <div className="grid grid-cols-1 items-start gap-[clamp(2rem,5vw,5rem)] md:grid-cols-2">
          <div>
            <Eyebrow index="01">What we solve</Eyebrow>
            <Reveal>
              <Display className="mt-[18px] max-w-[20ch]">
                Two kinds of work, one governance model.
              </Display>
            </Reveal>
          </div>
          <Reveal delay={0.08} className="flex flex-col gap-5">
            <Lede className="max-w-[65ch]">
              An agent can move money, change a production system, and act on a
              customer&apos;s behalf. The tooling around it was built to
              observe — dashboards, traces, logs read after something has
              already happened.
            </Lede>
            <Lede tone="strong" className="max-w-[65ch]">
              What&apos;s missing is not capability. It&apos;s authorization —
              and it has to hold for automated work and one-off asks alike.
            </Lede>
          </Reveal>
        </div>

        <div className="mt-[clamp(2.5rem,5vw,4.5rem)] grid grid-cols-1 items-start gap-[clamp(1.75rem,4vw,4rem)] border-t border-border pt-[clamp(1.75rem,3.5vw,2.75rem)] md:grid-cols-2">
          {modes.map((mode, i) => (
            <Reveal
              key={mode.label}
              delay={i * 0.08}
              className="flex flex-col gap-3.5"
            >
              <MonoLabel>{mode.label}</MonoLabel>
              <Display as="h3" size="card" className="max-w-[20ch]">
                {mode.title}
              </Display>
              <p className="max-w-[52ch] text-[1.03rem] leading-[1.7] text-muted-foreground">
                {mode.body}
              </p>
            </Reveal>
          ))}
        </div>

        <Reveal>
          <p className="mt-[clamp(1.75rem,3.5vw,2.75rem)] max-w-[70ch] border-t border-border pt-[clamp(1.25rem,2.5vw,1.75rem)] text-[1.09rem] leading-[1.7] text-foreground">
            One enforcement layer. Whether an action is looped or one-off, the
            same signature, scope, ceiling and expiry are checked before it
            runs — not two trust models for two modes of work.
          </p>
        </Reveal>
      </SectionBody>
    </Section>
  );
}
