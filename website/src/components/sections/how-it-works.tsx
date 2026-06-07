import { Container, Eyebrow, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const steps = [
  {
    num: "01",
    title: "Map the operation",
    body: "We trace how work actually moves through your business — every handoff, queue, and decision — and find where the leverage is.",
  },
  {
    num: "02",
    title: "Deploy the agents",
    body: "Agents stand up on your existing stack, scoped to real workflows, with guardrails and human approval exactly where it matters.",
  },
  {
    num: "03",
    title: "Scale what works",
    body: "Proven workflows go fully autonomous. Coverage expands as trust compounds — capacity grows without new headcount.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="03">How it works</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            From manual operation to autonomous system in three moves.
          </h2>
        </Reveal>

        <div className="relative mt-16">
          {/* The altitude track the steps sit on */}
          <div
            aria-hidden
            className="absolute top-[7px] right-0 left-0 hidden h-px lg:block"
            style={{
              background:
                "linear-gradient(90deg, var(--border-strong), var(--border-strong) 66%, transparent)",
            }}
          />

          <RevealGroup className="grid grid-cols-1 gap-12 lg:grid-cols-3 lg:gap-10">
            {steps.map((step) => (
              <RevealItem key={step.num} className="relative">
                {/* Node on the track */}
                <span
                  aria-hidden
                  className="relative z-10 flex size-3.5 items-center justify-center border border-border-strong bg-background"
                >
                  <span className="size-1.5" style={{ background: "var(--color-blue)" }} />
                </span>

                <div className="mt-7 font-mono text-xs tracking-[0.18em] text-blue/80 tabular-nums">
                  {step.num}
                </div>
                <h3 className="mt-3 text-2xl font-medium tracking-tight text-foreground">
                  {step.title}
                </h3>
                <p className="mt-3 max-w-sm leading-relaxed text-muted-foreground">
                  {step.body}
                </p>
              </RevealItem>
            ))}
          </RevealGroup>
        </div>
      </Container>
    </section>
  );
}
