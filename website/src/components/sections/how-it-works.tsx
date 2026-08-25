import { Container, Eyebrow, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const steps = [
  {
    num: "01",
    title: "Mint the authority",
    body: "Before an agent runs, it is issued a signed token: the tools it may call, the budget it may spend, the human it acts on behalf of, and the minute it expires.",
  },
  {
    num: "02",
    title: "Verify at the call site",
    body: "Every outbound action is checked against that token where it is made. In scope and under ceiling, it proceeds. Otherwise it is refused, or held at an approval gate for a human.",
  },
  {
    num: "03",
    title: "Keep the evidence",
    body: "Allowed or refused, the decision is written with its token, scope, and delegation chain — so any action can be traced back to the authority that permitted it.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="03">How it works</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            Authority is granted, checked, and recorded.
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
