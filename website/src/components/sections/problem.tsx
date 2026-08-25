import { Container, Eyebrow, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const problems = [
  {
    index: "01",
    title: "Credentials, not permissions",
    body: "An agent gets an API key and inherits everything that key can do. There is no way to say \"this task only, this budget, for the next ten minutes.\"",
  },
  {
    index: "02",
    title: "Spend has no ceiling",
    body: "A loop that retries, a prompt that fans out, a tool that costs per call. Nothing stops the run at a limit because no limit was ever attached to it.",
  },
  {
    index: "03",
    title: "Logs, not evidence",
    body: "Application logs say what happened. They cannot prove who authorized it, under what scope, or that the record was not edited afterward.",
  },
  {
    index: "04",
    title: "No way to stop it",
    body: "When an agent starts doing the wrong thing, the honest answer is usually to revoke a key and restart a service — and hope the in-flight work drains.",
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
              The missing piece was never more automation.
            </h2>
          </Reveal>
          <Reveal delay={0.1} className="flex items-end">
            <p className="text-lg leading-relaxed text-muted-foreground">
              Agents got good enough to take real actions against real systems.
              What never arrived was the layer that decides whether a given
              action is permitted — and leaves proof either way. So the work
              stalls at the demo, because nobody will sign off on production.
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
