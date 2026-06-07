import { Workflow, Network, Activity, TrendingUp } from "lucide-react";
import {
  Container,
  Eyebrow,
  AltitudeLine,
  Reveal,
  RevealGroup,
  RevealItem,
} from "@/components/skylize";

const features = [
  {
    icon: Workflow,
    title: "Autonomous agents",
    body: "Agents own a workflow end-to-end — research, decide, execute, report. Supervised when you want it, autonomous when you trust it.",
    tag: "agents",
  },
  {
    icon: Network,
    title: "Sits on your stack",
    body: "Connects to the tools you already run — CRM, billing, support, data — with no rip-and-replace and no migration project.",
    tag: "integration",
  },
  {
    icon: Activity,
    title: "Operational visibility",
    body: "Every action, cost, and outcome is observable in real time. Decisions stop waiting on a report that nobody has time to build.",
    tag: "control",
  },
  {
    icon: TrendingUp,
    title: "Compounding leverage",
    body: "Each workflow you hand off frees the team to build the next. Capacity grows without growing headcount.",
    tag: "scale",
  },
];

export function Solution() {
  return (
    <section id="solution" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="02">The solution</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            One operating layer for your entire operation.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
            Skylize is the infrastructure beneath the work — a system of agents
            and operational logic that runs the repetitive, so your team runs
            the company.
          </p>
        </Reveal>

        <RevealGroup className="mt-14 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
          {features.map((f) => {
            const Icon = f.icon;
            return (
              <RevealItem
                key={f.title}
                className="group relative bg-card p-7 transition-colors duration-300 hover:bg-accent sm:p-8"
              >
                <div className="flex items-center justify-between">
                  <span className="flex size-10 items-center justify-center rounded-md border border-border text-blue transition-colors duration-300 group-hover:border-blue/40">
                    <Icon className="size-[18px]" strokeWidth={1.75} />
                  </span>
                  <span className="font-mono text-[10px] tracking-[0.18em] text-muted-foreground/70 uppercase">
                    {f.tag}
                  </span>
                </div>

                <AltitudeLine className="my-6" />

                <h3 className="text-xl font-medium tracking-tight text-foreground">
                  {f.title}
                </h3>
                <p className="mt-2.5 max-w-md leading-relaxed text-muted-foreground">
                  {f.body}
                </p>
              </RevealItem>
            );
          })}
        </RevealGroup>
      </Container>
    </section>
  );
}
