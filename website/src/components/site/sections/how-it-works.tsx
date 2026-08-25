import Link from "next/link";
import { Reveal } from "@/components/skylize";
import { Display, Eyebrow, Section, SectionBody } from "../primitives";

const steps = [
  {
    n: "01",
    title: "Grant",
    body: "A scope, a budget ceiling and an expiry are written into a token and signed. Gated scopes wait for a named human to approve them.",
  },
  {
    n: "02",
    title: "Present",
    body: "The agent presents its token with every action it attempts. There is no unauthenticated path to the systems behind the layer.",
  },
  {
    n: "03",
    title: "Verify",
    body: "Signature, scope, ceiling and expiry are checked at the call site, before execution. If any check fails, the action is refused.",
  },
  {
    n: "04",
    title: "Record",
    body: "The action and its delegation chain are written to a replayable log, so who authorised what can be reconstructed after the fact.",
  },
] as const;

export function HowItWorks() {
  return (
    <Section id="how">
      <SectionBody>
        <Eyebrow index="03">How it works</Eyebrow>
        <Display className="mt-[18px] max-w-[22ch]">
          Four steps, every action.
        </Display>
        <p className="mt-5 max-w-[62ch] text-[1.03rem] leading-[1.7] text-muted-foreground">
          Operators watch this run in the{" "}
          <Link
            href="/console-preview"
            className="text-blue transition-colors duration-200 hover:text-foreground"
          >
            admin console
          </Link>
          . The employee-facing counterpart,{" "}
          <Link
            href="/my-day"
            className="text-blue transition-colors duration-200 hover:text-foreground"
          >
            My Day
          </Link>
          , shows the same enforcement to the person the agent works for.
        </p>

        <div className="mt-[clamp(2.25rem,5vw,4rem)] flex flex-col">
          {steps.map((step) => (
            <Reveal key={step.n}>
              <div className="grid grid-cols-1 gap-4 border-t border-border py-[clamp(1.5rem,3vw,2.25rem)] md:grid-cols-2 md:gap-[clamp(1rem,4vw,4rem)]">
                <div className="flex items-baseline gap-[18px]">
                  <span className="font-mono text-xs tracking-[0.1em] text-blue tabular-nums">
                    {step.n}
                  </span>
                  <Display as="h3" size="card">
                    {step.title}
                  </Display>
                </div>
                <p className="max-w-[58ch] text-[1.03rem] leading-[1.7] text-muted-foreground">
                  {step.body}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </SectionBody>
    </Section>
  );
}
