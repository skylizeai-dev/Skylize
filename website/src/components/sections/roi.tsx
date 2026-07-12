import { Container, Eyebrow, Counter, Reveal, CtaButton } from "@/components/skylize";

const metrics = [
  { value: 31, suffix: " hrs", label: "reclaimed / person / week" },
  { value: 3.2, decimals: 1, suffix: "×", label: "return in year one" },
  { value: 68, prefix: "−", suffix: "%", label: "cost per operation" },
  { value: 11, suffix: " wks", label: "average payback period" },
];

export function Roi() {
  return (
    <section id="roi" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <div className="grid gap-10 lg:grid-cols-[1fr_0.9fr] lg:items-end lg:gap-16">
          <Reveal>
            <Eyebrow index="05">The return</Eyebrow>
            <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
              The math is not subtle.
            </h2>
            <p className="mt-6 max-w-md text-lg leading-relaxed text-muted-foreground">
              Automation that removes real cost and returns real hours. These
              are the medians we see across deployments in the first year.
            </p>
          </Reveal>
          <Reveal delay={0.1} className="lg:pb-2">
            <CtaButton href="#contact" arrow>
              Model your ROI
            </CtaButton>
          </Reveal>
        </div>

        <Reveal delay={0.05} className="mt-14">
          <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {metrics.map((m) => (
              <div key={m.label} className="bg-card px-7 py-10">
                <Counter
                  value={m.value}
                  decimals={m.decimals}
                  prefix={m.prefix}
                  suffix={m.suffix}
                  className="font-mono text-[clamp(2.5rem,5vw,3.5rem)] font-semibold tracking-tight text-foreground tabular-nums"
                />
                <div
                  aria-hidden
                  className="mt-4 mb-4 h-px w-8"
                  style={{ background: "var(--color-blue)" }}
                />
                <p className="font-mono text-[11px] tracking-[0.1em] text-muted-foreground uppercase">
                  {m.label}
                </p>
              </div>
            ))}
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
