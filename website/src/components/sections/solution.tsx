import { KeyRound, Gauge, UserCheck, ScrollText } from "lucide-react";
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
    icon: KeyRound,
    title: "Signed, scoped authority",
    body: "Every action carries a token naming the exact tools it may call and when it expires. Unsigned, out-of-scope, or expired means the call never leaves the process.",
    tag: "token",
  },
  {
    icon: Gauge,
    title: "Budgets that actually bind",
    body: "A spend ceiling travels inside the token itself and is enforced where the call is made — not reconciled on a dashboard after the money is gone.",
    tag: "budget",
  },
  {
    icon: UserCheck,
    title: "Approval where it matters",
    body: "Route any action class to a human before it executes. The agent halts at the gate and resumes on approval, carrying the decision into its audit trail.",
    tag: "control",
  },
  {
    icon: ScrollText,
    title: "Evidence, not log lines",
    body: "Each decision records the token, the scope, the delegation chain, and the outcome — a sequence you can replay to reconstruct exactly why an action was allowed.",
    tag: "audit",
  },
];

export function Solution() {
  return (
    <section id="solution" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal className="max-w-3xl">
          <Eyebrow index="02">The solution</Eyebrow>
          <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
            A governance layer between intent and action.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
            Skylize sits at the call site. An agent decides what it wants to do;
            the layer decides whether it may, enforces the ceiling it was given,
            and writes down what happened. Nothing reaches a target system
            without passing through it.
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
