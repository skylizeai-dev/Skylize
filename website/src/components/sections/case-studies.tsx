import { ArrowUpRight } from "lucide-react";
import { Container, Eyebrow, AltitudeLine, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const featured = {
  client: "Meridian Logistics",
  industry: "Freight operations",
  headline: "Cut order processing by 73% and absorbed 2× volume — with the same team.",
  body: "Skylize agents took over intake, validation, and routing across four disconnected systems. What took an analyst nine minutes now resolves in under two, untouched.",
  metrics: [
    { value: "−73%", label: "processing time" },
    { value: "2.1×", label: "volume absorbed" },
    { value: "0", label: "added headcount" },
  ],
};

const cases = [
  {
    client: "Northwind",
    industry: "B2B SaaS",
    result: "Support deflection reached 61% while CSAT held at 98.",
    metrics: [
      { value: "61%", label: "deflection" },
      { value: "98", label: "CSAT" },
    ],
  },
  {
    client: "Vantage Capital",
    industry: "Financial services",
    result: "Month-end close compressed from twelve days to three.",
    metrics: [
      { value: "−9d", label: "time to close" },
      { value: "100%", label: "reconciled" },
    ],
  },
];

export function CaseStudies() {
  return (
    <section id="case-studies" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="06">Case studies</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            Operators who took the work off the team.
          </h2>
        </Reveal>

        {/* Featured */}
        <Reveal delay={0.05} className="mt-14">
          <a
            href="#"
            className="group block overflow-hidden rounded-xl border border-border bg-card transition-colors duration-300 hover:border-border-strong"
          >
            <div className="grid grid-cols-1 lg:grid-cols-[1.25fr_1fr]">
              <div className="p-8 sm:p-10">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
                    {featured.industry}
                  </span>
                  <ArrowUpRight className="size-5 text-muted-foreground transition-all duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-foreground" />
                </div>
                <h3 className="mt-8 text-balance text-[clamp(1.5rem,2.6vw,2.1rem)] font-semibold leading-[1.08] tracking-[-0.02em] text-foreground">
                  {featured.headline}
                </h3>
                <p className="mt-5 max-w-md leading-relaxed text-muted-foreground">
                  {featured.body}
                </p>
                <div className="mt-8 font-display text-base font-medium text-foreground">
                  {featured.client}
                </div>
              </div>

              <div className="grid grid-cols-3 border-t border-border lg:grid-cols-1 lg:border-t-0 lg:border-l lg:border-border">
                {featured.metrics.map((m, i) => (
                  <div
                    key={m.label}
                    className={[
                      "flex flex-col justify-center p-6 sm:p-8",
                      i > 0 ? "border-l border-border lg:border-t lg:border-l-0" : "",
                    ].join(" ")}
                  >
                    <div className="font-display text-[clamp(1.75rem,3vw,2.5rem)] font-semibold tracking-tight text-foreground tabular-nums">
                      {m.value}
                    </div>
                    <div className="mt-1.5 font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                      {m.label}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </a>
        </Reveal>

        {/* Supporting */}
        <RevealGroup className="mt-6 grid grid-cols-1 gap-6 sm:grid-cols-2">
          {cases.map((c) => (
            <RevealItem key={c.client}>
              <a
                href="#"
                className="group flex h-full flex-col rounded-xl border border-border bg-card p-8 transition-colors duration-300 hover:border-border-strong"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
                    {c.industry}
                  </span>
                  <ArrowUpRight className="size-5 text-muted-foreground transition-all duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-foreground" />
                </div>
                <p className="mt-6 flex-1 text-xl font-medium leading-snug tracking-tight text-foreground">
                  {c.result}
                </p>
                <AltitudeLine className="my-6" />
                <div className="flex items-center justify-between">
                  <span className="font-display font-medium text-foreground">{c.client}</span>
                  <div className="flex gap-6">
                    {c.metrics.map((m) => (
                      <div key={m.label} className="text-right">
                        <div className="font-display text-lg font-semibold text-foreground tabular-nums">
                          {m.value}
                        </div>
                        <div className="font-mono text-[9px] tracking-[0.12em] text-muted-foreground uppercase">
                          {m.label}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </a>
            </RevealItem>
          ))}
        </RevealGroup>
      </Container>
    </section>
  );
}
