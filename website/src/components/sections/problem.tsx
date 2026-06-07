import { Container, Eyebrow, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const problems = [
  {
    index: "01",
    title: "Work scattered across tools",
    body: "Teams stitch together a dozen systems and still move data by hand. The seams are where time and margin disappear.",
  },
  {
    index: "02",
    title: "Headcount scales with volume",
    body: "Every new customer adds manual load instead of leverage. Growth costs more than it returns.",
  },
  {
    index: "03",
    title: "Insight trapped in operations",
    body: "Decisions wait on reports nobody has time to build. The numbers exist — the attention does not.",
  },
  {
    index: "04",
    title: "The compounding work never ships",
    body: "Operators spend their day clearing the queue. The projects that change the trajectory stay parked.",
  },
];

export function Problem() {
  return (
    <section id="problem" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
          <Reveal>
            <Eyebrow index="01">The problem</Eyebrow>
            <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
              Manual work is the tax on every growing company.
            </h2>
          </Reveal>
          <Reveal delay={0.1} className="flex items-end">
            <p className="text-lg leading-relaxed text-muted-foreground">
              As you scale, the work multiplies faster than the team. More
              tools, more handoffs, more places for things to break. Skylize
              exists to remove that ceiling — permanently.
            </p>
          </Reveal>
        </div>

        <RevealGroup className="mt-16 grid grid-cols-1 border-t border-border sm:grid-cols-2">
          {problems.map((p, i) => (
            <RevealItem
              key={p.index}
              className={[
                "group border-b border-border px-1 py-8 sm:px-8",
                i % 2 === 0 ? "sm:border-r" : "",
              ].join(" ")}
            >
              <div className="flex items-start gap-5">
                <span className="font-mono text-xs text-blue/80 tabular-nums">
                  {p.index}
                </span>
                <div>
                  <h3 className="text-xl font-medium tracking-tight text-foreground">
                    {p.title}
                  </h3>
                  <p className="mt-2 max-w-md leading-relaxed text-muted-foreground">
                    {p.body}
                  </p>
                </div>
              </div>
            </RevealItem>
          ))}
        </RevealGroup>
      </Container>
    </section>
  );
}
