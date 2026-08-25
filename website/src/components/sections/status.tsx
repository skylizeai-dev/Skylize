import { Container, Eyebrow, AltitudeLine, Reveal, RevealGroup, RevealItem, CtaButton } from "@/components/skylize";

/**
 * This section exists in place of logos, testimonials, and case studies.
 * There are no customers to cite yet, and inventing them on a page that sells
 * verifiable enforcement would undo the product's only real claim. State the
 * position plainly instead; a governance buyer reads candour as a signal.
 */
const facts = [
  {
    label: "Stage",
    value: "Pre-revenue",
    body: "No paid customers. We are not quoting a price sheet we have not tested against a real deployment.",
  },
  {
    label: "Entity",
    value: "Pre-incorporation",
    body: "Incorporation lands alongside the first signed design-partner agreement, not before it.",
  },
  {
    label: "Product",
    value: "In build",
    body: "The enforcement path — token, authority check, kill switch — is implemented and under test.",
  },
];

const partnerTerms = [
  "Direct access to the people building it, not a support queue.",
  "Your governance requirements shape the roadmap while it is still soft.",
  "A deployment scoped to one real workflow, not a company-wide rollout.",
  "An honest answer about what is not ready yet, every time you ask.",
];

export function Status() {
  return (
    <section id="status" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <div className="grid gap-10 lg:grid-cols-[1fr_0.9fr] lg:items-end lg:gap-16">
          <Reveal>
            <Eyebrow index="05">Where we are</Eyebrow>
            <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
              No logos. No case studies. Not yet.
            </h2>
            <p className="mt-6 max-w-md text-lg leading-relaxed text-muted-foreground">
              Skylize is early, and a page about verifiable authority is the
              wrong place to imply customers we do not have. So: here is the
              position, and here is what a design partner actually gets.
            </p>
          </Reveal>
          <Reveal delay={0.1} className="lg:pb-2">
            <CtaButton href="#contact" arrow>
              Apply as a Design Partner
            </CtaButton>
          </Reveal>
        </div>

        <RevealGroup className="mt-14 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
          {facts.map((f) => (
            <RevealItem key={f.label} className="bg-card px-7 py-9">
              <p className="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
                {f.label}
              </p>
              <p className="mt-3 text-2xl font-medium tracking-tight text-foreground">
                {f.value}
              </p>
              <div
                aria-hidden
                className="mt-5 mb-5 h-px w-8"
                style={{ background: "var(--color-blue)" }}
              />
              <p className="leading-relaxed text-muted-foreground">{f.body}</p>
            </RevealItem>
          ))}
        </RevealGroup>

        <Reveal delay={0.05} className="mt-14">
          <div className="rounded-xl border border-border bg-card p-7 sm:p-10">
            <h3 className="text-xl font-medium tracking-tight text-foreground">
              What a design partner gets
            </h3>
            <AltitudeLine className="my-7" />
            <ul className="grid grid-cols-1 gap-x-12 gap-y-4 sm:grid-cols-2">
              {partnerTerms.map((term) => (
                <li key={term} className="flex items-start gap-3">
                  <span
                    aria-hidden
                    className="mt-2.5 size-1 shrink-0 rounded-full"
                    style={{ background: "var(--color-blue)" }}
                  />
                  <span className="leading-relaxed text-muted-foreground">{term}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
