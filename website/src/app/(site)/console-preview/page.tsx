import type { Metadata } from "next";
import Link from "next/link";
import {
  Display,
  Eyebrow,
  ScreenshotFrame,
  Section,
  SiteButton,
  SiteContainer,
  SiteFooter,
  SiteHeader,
  TextLink,
} from "@/components/site";

export const metadata: Metadata = {
  title: "Command Console",
  description:
    "The operator's view of the Skylize enforcement layer: command channel, audit log, approvals, and an organization-wide agent map.",
};

/**
 * The operator tour.
 *
 * Screens are described in full and framed at their real proportions; the
 * captures themselves are pending. Every frame carries its sample-data
 * caption structurally (see ScreenshotFrame) — these are seeded workspaces,
 * never a customer deployment, and the page says so under each one.
 */
type Screen = {
  n: string;
  route: string;
  title: string;
  body: string;
  proof: string;
  /** Set once the capture lands; until then the frame renders `pendingNote`. */
  src?: string;
  alt?: string;
  pendingNote?: string;
};

const screens: Screen[] = [
  {
    n: "01",
    route: "/console/command",
    title: "Command channel",
    body: "One request is typed in plain language. The teams that hold the relevant scope pick up their part of it, and the delegation chain builds alongside as each agent takes its step.",
    proof:
      "Delegation is explicit from the first keystroke: the chain that will carry the work is assembled and shown, so the authority behind each step exists before the step runs — and is recorded for review afterwards.",
    src: "/screens/console-command.png",
    alt: "Skylize console command channel: a single request field with a live delegation chain panel alongside it.",
  },
  {
    n: "02",
    route: "/console/audit-log",
    title: "Audit Log",
    body: "Every action is written with its actor, its action class, the target it touched and the signature it was carried under. The log streams as actions happen and can be exported as a verified file.",
    proof:
      "This is the mechanism, not a summary of it: because each row carries a signature, an action can be traced back to the grant that authorised it rather than taken on trust.",
    src: "/screens/console-audit-log.png",
    alt: "Skylize console Audit Log: rows of time, actor, action class, target and signature hash, streaming live.",
  },
  {
    n: "03",
    route: "/console/approvals",
    title: "Approvals",
    body: "Gated actions wait here. Each item names who reviewed it and in what order, what happens if it is approved, and what happens if it is declined — including the share of asks that were handled under existing rules without reaching a person.",
    proof:
      "The human in the loop is load-bearing. Until someone in the chain approves, the action does not run, and the decision itself becomes part of the record.",
    src: "/screens/console-approvals.png",
    alt: "Skylize console Approvals: pending items with their review chain, the stated outcome of approving or declining, and the share handled without a person.",
  },
  {
    n: "04",
    route: "/console/agent-map",
    title: "Agent Map",
    body: "Agents are grouped by department and tier, with counts of what is live in each. A cluster can be opened to see the individual agents inside it.",
    proof:
      "Scope is legible at the organization level: what exists, where it sits, and how much of it is currently acting — visible in one view rather than assembled from logs.",
    src: "/screens/console-agent-map.png",
    alt: "Skylize console Agent Map: an organization-wide cluster diagram of agents grouped by department and tier, with live counts per cluster.",
  },
];

export default function ConsolePreviewPage() {
  return (
    <>
      <SiteHeader current="console-preview" />
      <main>
        <section>
          <SiteContainer className="pt-[clamp(3rem,7vw,6rem)] pb-[clamp(2.25rem,4vw,3.5rem)]">
            <div className="mb-[clamp(1.75rem,4vw,3rem)] flex flex-wrap items-baseline justify-between gap-4">
              <span className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
                Command Console · /console
              </span>
              <span className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
                Four screens
              </span>
            </div>

            <Display as="h1" size="hero" className="max-w-[18ch]">
              The operator&apos;s view of the enforcement layer.
            </Display>

            <div className="mt-[clamp(1.5rem,3.5vw,2.75rem)] grid grid-cols-1 items-start gap-[clamp(1.5rem,4vw,4rem)] md:grid-cols-2">
              <p className="max-w-[60ch] text-[1.09rem] leading-[1.65] text-muted-foreground">
                Four screens from the console that runs today. They are shown as
                they are — framed, not redrawn — because what they demonstrate
                is the mechanism: delegation made explicit, signed actions,
                human approval in the path, and scope visible across an
                organization.
              </p>
              <p className="border-l border-border pl-5 font-mono text-[11.5px] leading-[1.8] tracking-[0.1em] text-muted-foreground uppercase">
                Every screen below is a sample workspace with illustrative data,
                not a customer deployment. Where the product stands today:{" "}
                <Link href="/#status" className="text-blue hover:text-foreground">
                  Status
                </Link>
                .
              </p>
            </div>
          </SiteContainer>
        </section>

        {screens.map((screen, i) => (
          <Section key={screen.route} band={i % 2 === 1}>
            <SiteContainer className="py-[clamp(3rem,6vw,6rem)]">
              <div className="grid grid-cols-1 items-start gap-[clamp(1.5rem,4vw,4.5rem)] md:grid-cols-2">
                <div>
                  <Eyebrow index={screen.n}>{screen.route}</Eyebrow>
                  <Display className="mt-4 max-w-[16ch]">{screen.title}</Display>
                </div>
                <div className="flex flex-col gap-[18px]">
                  <p className="max-w-[62ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
                    {screen.body}
                  </p>
                  <p className="max-w-[62ch] text-[1.06rem] leading-[1.7] text-foreground">
                    {screen.proof}
                  </p>
                </div>
              </div>

              <ScreenshotFrame
                route={screen.route}
                src={screen.src}
                alt={screen.alt}
                pendingNote={screen.pendingNote}
                priority={i === 0}
              />
            </SiteContainer>
          </Section>
        ))}

        <Section>
          <SiteContainer className="grid grid-cols-1 items-start gap-[clamp(1.75rem,4vw,4.5rem)] py-[clamp(3.5rem,7vw,6.5rem)] md:grid-cols-2">
            <Display className="max-w-[16ch]">
              Want to see it against your own agents?
            </Display>
            <div className="flex flex-col items-start gap-[22px]">
              <p className="max-w-[56ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
                Design partners get the layer put in front of agents they are
                actually running, and direct access to the engineer building it.
              </p>
              <SiteButton href="/#apply">Apply as a Design Partner</SiteButton>
              <TextLink href="/my-day" mono>
                See what your team sees on My Day →
              </TextLink>
            </div>
          </SiteContainer>
        </Section>
      </main>
      <SiteFooter />
    </>
  );
}
