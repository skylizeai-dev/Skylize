import { Reveal } from "@/components/skylize";
import { TextLink } from "../button";
import { Display, Eyebrow, Lede, MonoLabel, Section, SectionBody } from "../primitives";

/**
 * The two views onto the same enforcement layer. Both link to a tour that
 * exists; neither claims a screen we cannot show.
 */
const views = [
  {
    audience: "For whoever's accountable for what agents do.",
    name: "Command Console",
    body: "Kill switch, audit log, live scope and permission visibility.",
    href: "/console-preview",
    cta: "See the Command Console →",
  },
  {
    audience: "For the people working alongside it.",
    name: "My Day",
    body: "What got done overnight, what's waiting on a decision, and a record nobody can edit after the fact.",
    href: "/my-day",
    cta: "See My Day →",
  },
] as const;

export function Solution() {
  return (
    <Section id="solution">
      <SectionBody>
        <Eyebrow index="02">Solution</Eyebrow>
        <div className="mt-[18px] grid grid-cols-1 items-start gap-[clamp(2rem,5vw,5rem)] md:grid-cols-2">
          <Reveal>
            <Display className="max-w-[18ch]">
              Enforcement at the call site, not review after the fact.
            </Display>
          </Reveal>
          <Reveal delay={0.08} className="flex flex-col gap-5">
            <Lede className="max-w-[65ch]">
              Skylize sits between an agent and the systems it touches. Each
              action presents a token that names what it may do, how much it
              may spend, and when the grant expires. The signature, the scope,
              the ceiling and the expiry are checked before execution.
            </Lede>
            <Lede tone="strong" className="max-w-[65ch]">
              If the token does not verify, the action does not happen. The
              failure mode is closed.
            </Lede>
            <p className="border-t border-border pt-4 font-mono text-[11px] leading-[1.7] tracking-[0.1em] text-muted-foreground uppercase">
              Two views onto that enforcement run today. Pick the one built for
              you.
            </p>
          </Reveal>
        </div>

        <Reveal className="mt-[clamp(2.25rem,5vw,4rem)]">
          <div className="grid grid-cols-1 gap-[clamp(1.25rem,3vw,2rem)] md:grid-cols-2">
            {views.map((view) => (
              <div
                key={view.name}
                className="flex flex-col gap-4 border border-border p-[clamp(1.5rem,3vw,2.25rem)]"
              >
                <MonoLabel>{view.audience}</MonoLabel>
                <Display as="h3" size="sub">
                  {view.name}
                </Display>
                <p className="max-w-[40ch] text-[1.03rem] leading-[1.7] text-muted-foreground">
                  {view.body}
                </p>
                <TextLink href={view.href} className="mt-1">
                  {view.cta}
                </TextLink>
              </div>
            ))}
          </div>
        </Reveal>
      </SectionBody>
    </Section>
  );
}
