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
} from "@/components/site";

export const metadata: Metadata = {
  title: "My Day",
  description:
    "The employee's side of the Skylize enforcement layer: the morning brief, inline approvals, an agent's stated perimeter, and a write-once record of finished work.",
};

/**
 * The employee tour.
 *
 * The admin console answers whether the thing can be trusted and controlled.
 * This page answers the other question a buyer asks — whether their team will
 * use it or fight it — so the framing throughout is that boundaries make the
 * day legible rather than merely constrain it.
 *
 * Captures are pending; every frame still carries its sample-data caption.
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
  footnote?: boolean;
};

const screens: Screen[] = [
  {
    n: "01",
    route: "/my-day/morning-brief",
    title: "Morning Brief",
    body: "The day opens with a plain-language account of the night: what was finished, what it cost, and where it stopped on purpose because the next step sat outside its authority.",
    proof:
      "An employee is handed a decision list, not a log to audit. The boundary checks that produced it happened before anything ran.",
    src: "/screens/myday-morning-brief.png",
    alt: "My Day morning brief: an overnight account of finished work and spend, with the two items that stopped at a boundary listed first for a decision.",
  },
  {
    n: "02",
    route: "/my-day/working-together",
    title: "Asking, in the middle of the work",
    body: "An employee asks for something in the moment; the agent answers, and when the next step falls outside its authority it says so and waits. The approval happens inline, in the same thread.",
    proof:
      "It is not a freshly briefed assistant. It reads the same live, continuously synced organizational memory the automation layer keeps — scoped to what this employee is permitted to see — so a one-off request is answered from current context, not a re-collected snapshot.",
    src: "/screens/myday-working-together.png",
    alt: "My Day co-work thread: the agent answers in the moment, shows the boundary check it ran, and holds the over-ceiling quote inline for approval.",
  },
  {
    n: "03",
    route: "/my-day/agent-map · employee-scoped",
    title: "Your agent's activity",
    body: "One screen for one person's agent: what it may do without asking, what it must always ask about first, how much of the week's spending headroom is left, and what it handed back.",
    proof:
      "The perimeter is stated to the employee in the same terms it is enforced in. Items that stopped are shown as stopped on purpose, not as failures.",
    src: "/screens/myday-agent-map.png",
    alt: "My Day agent map: the perimeter split into what the agent does alone and what it always asks about first, with the week's spending headroom and last night's run.",
    footnote: true,
  },
  {
    n: "04",
    route: "/my-day/what-it-can-do",
    title: "What it can do",
    body: "The full grant, written out: which actions carry the employee's role, which ones pause and ask, and the spending ceiling with what remains against it this week.",
    proof:
      "The agent borrows the employee's authority rather than holding its own. When their access changes, its access changes with it — checked before each action, not audited afterwards.",
    src: "/screens/myday-what-it-can-do.png",
    alt: "My Day permissions: the agent's grant written out under communicate and spend headings, each line naming the authority it borrows.",
  },
  {
    n: "05",
    route: "/my-day/past-tasks",
    title: "Past tasks",
    body: "Every finished piece of work, day by day, with the calls the employee made on the rest — approved, declined — and a trace behind each entry.",
    proof:
      "Nothing here can be edited, by the employee or by the agent. It is the same write-once guarantee the admin audit log rests on, seen from the other side.",
    src: "/screens/myday-past-tasks.png",
    alt: "My Day past tasks: finished work grouped by day, with the employee's approvals and declines and a trace behind each entry.",
  },
];

export default function MyDayPage() {
  return (
    <>
      <SiteHeader current="my-day" />
      <main>
        <section>
          <SiteContainer className="pt-[clamp(3rem,7vw,6rem)] pb-[clamp(2.25rem,4vw,3.5rem)]">
            <div className="mb-[clamp(1.75rem,4vw,3rem)] flex flex-wrap items-baseline justify-between gap-4">
              <span className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
                My Day · the employee&apos;s side
              </span>
              <Link
                href="/console-preview"
                className="font-mono text-[11px] tracking-[0.14em] text-blue uppercase transition-colors duration-200 hover:text-foreground"
              >
                ← The admin console
              </Link>
            </div>

            <Display as="h1" size="hero" className="max-w-[19ch]">
              The enforcement layer, from the side of the person it works for.
            </Display>

            <div className="mt-[clamp(1.5rem,3.5vw,2.75rem)] grid grid-cols-1 items-start gap-[clamp(1.5rem,4vw,4rem)] md:grid-cols-2">
              <div className="flex flex-col gap-[18px]">
                <p className="max-w-[60ch] text-[1.09rem] leading-[1.65] text-muted-foreground">
                  The admin console answers whether you can trust and control
                  the thing. This answers the other question a buyer asks:
                  whether their team will use it or fight it.
                </p>
                <p className="max-w-[60ch] text-[1.09rem] leading-[1.65] text-foreground">
                  Boundaries are not only a constraint here. They are what makes
                  the day legible — what was done, what was refused, and the two
                  places where a person&apos;s judgment is actually needed.
                </p>
              </div>
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
                  {screen.footnote ? (
                    <p className="border-t border-border pt-3.5 font-mono text-[11px] leading-[1.7] tracking-[0.1em] text-muted-foreground uppercase">
                      Not the same screen as the org-wide agent map on the{" "}
                      <Link
                        href="/console-preview"
                        className="text-blue hover:text-foreground"
                      >
                        admin console
                      </Link>
                      . This one is scoped to a single employee&apos;s authority.
                    </p>
                  ) : null}
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
              Both sides of the same enforcement.
            </Display>
            <div className="flex flex-col items-start gap-[22px]">
              <p className="max-w-[56ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
                Design partners get the layer put in front of agents they are
                actually running, and direct access to the engineer building it.
              </p>
              <div className="flex flex-wrap gap-3">
                <SiteButton href="/#apply">Apply as a Design Partner</SiteButton>
                <SiteButton href="/console-preview" variant="secondary">
                  See the admin console
                </SiteButton>
              </div>
            </div>
          </SiteContainer>
        </Section>
      </main>
      <SiteFooter />
    </>
  );
}
