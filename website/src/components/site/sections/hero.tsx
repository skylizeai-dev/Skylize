import { Reveal } from "@/components/skylize";
import { SiteButton } from "../button";
import { Display, Lede, SiteContainer } from "../primitives";

/**
 * The trust row under the fold. Each item is a property of the architecture —
 * what signs the grant, what the log guarantees, what the halt covers. None
 * of them is an adoption figure or a measured result, and none may become
 * one: this page's whole argument is that the mechanism is checkable before
 * a single customer exists.
 */
const facts = [
  { label: "Signed tokens", detail: "ECDSA P-384" },
  { label: "Replayable audit log", detail: "Every action reconstructable" },
  { label: "Kill switch", detail: "Immediate, system-wide halt" },
] as const;

export function Hero() {
  return (
    <>
      <section id="top">
        <SiteContainer className="pt-[clamp(3.5rem,9vw,7.5rem)] pb-[clamp(2.5rem,5vw,4rem)]">
          <div className="mb-[clamp(2rem,5vw,3.5rem)] flex flex-wrap items-baseline justify-between gap-5">
            <span className="inline-flex items-center gap-[9px] border border-border px-[11px] py-1.5 font-mono text-[10.5px] tracking-[0.14em] whitespace-nowrap text-muted-foreground uppercase">
              <span className="size-[5px] shrink-0 rounded-full bg-blue" />
              Pre-launch · taking design partners
            </span>
            <span className="font-mono text-[10.5px] tracking-[0.14em] text-muted-foreground uppercase">
              Permission layer for AI agents
            </span>
          </div>

          <Reveal>
            <Display as="h1" size="hero" className="max-w-[17ch]">
              Agents can act. Now prove they were allowed to.
            </Display>
          </Reveal>

          <div className="mt-[clamp(1.75rem,4vw,3rem)] grid grid-cols-1 items-start gap-[clamp(1.75rem,4vw,4rem)] md:grid-cols-2">
            <Reveal delay={0.06}>
              <Lede className="max-w-[62ch]">
                Every agent action carries a signed token naming its scope, its
                budget ceiling, and its expiry — verified at the call site,
                before anything reaches your systems.
              </Lede>
            </Reveal>
            <Reveal delay={0.12}>
              <div className="flex flex-wrap gap-3">
                <SiteButton href="#apply">Apply as a Design Partner</SiteButton>
                <SiteButton href="#how" variant="secondary">
                  See How It Works
                </SiteButton>
              </div>
            </Reveal>
          </div>
        </SiteContainer>
      </section>

      <section className="border-t border-border">
        <SiteContainer>
          <div className="grid grid-cols-1 sm:grid-cols-3">
            {facts.map((fact, i) => (
              <div
                key={fact.label}
                className={
                  i === facts.length - 1
                    ? "py-[22px]"
                    : "py-[22px] sm:border-r sm:border-border"
                }
              >
                <div
                  className={
                    i === 0 ? "" : "sm:pl-[clamp(0px,2vw,1.75rem)]"
                  }
                >
                  <span className="font-mono text-[11px] tracking-[0.12em] text-foreground uppercase">
                    {fact.label}
                  </span>
                  <span className="mt-1.5 block font-mono text-[11px] tracking-[0.08em] text-muted-foreground">
                    {fact.detail}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </SiteContainer>
      </section>
    </>
  );
}
