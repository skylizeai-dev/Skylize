import { Reveal } from "@/components/skylize";
import { TextLink } from "../button";
import { Display, Eyebrow, Section, SectionBody } from "../primitives";

/**
 * This section stands in place of logos, testimonials and case studies.
 *
 * There are no customers to cite yet, and inventing them on a page whose
 * entire claim is verifiable enforcement would undo that claim. The count of
 * design partners is deliberately not stated here: it was left out rather
 * than asserted, in keeping with 05fc015 (remove fabricated proof). Add a
 * number here only when it is true and someone is willing to stand behind it.
 */
const partnerTerms = [
  {
    title: "Direct access to the engineer building it",
    body: "Not a support queue. The person writing the enforcement layer answers you.",
  },
  {
    title: "Influence over what gets built next",
    body: "The controls your deployment needs move up the roadmap ahead of the ones it does not.",
  },
  {
    title: "Deployment against your real agent stack",
    body: "The layer is put in front of agents you are actually running, not a sandbox demo.",
  },
] as const;

export function Status() {
  return (
    <Section id="status" band>
      <SectionBody>
        <Eyebrow index="05">Status</Eyebrow>
        <div className="mt-[18px] grid grid-cols-1 items-start gap-[clamp(2rem,5vw,5rem)] md:grid-cols-2">
          <div>
            <Reveal>
              <Display className="max-w-[18ch]">
                This is where the logos would go.
              </Display>
            </Reveal>
            <p className="mt-[22px] max-w-[60ch] text-[1.09rem] leading-[1.7] text-muted-foreground">
              Proof over promises. No case studies. No SOC 2 badge. Rather you
              know that up front than find out later. What you can see: the
              enforcement layer, actively in build, and the two console screens
              on this page — running today, not mocked up for a pitch.
              We&apos;re looking for a small number of design partners to build
              this with, not a waitlist.
            </p>
          </div>

          <div>
            <div className="border-b border-border pb-3 font-mono text-[10.5px] tracking-[0.16em] text-muted-foreground uppercase">
              What a design partner gets
            </div>
            {partnerTerms.map((term) => (
              <div key={term.title} className="border-b border-border py-5">
                <h3 className="text-base leading-[1.4] font-semibold text-foreground">
                  {term.title}
                </h3>
                <p className="mt-2 max-w-[50ch] text-[0.97rem] leading-[1.65] text-muted-foreground">
                  {term.body}
                </p>
              </div>
            ))}
            <TextLink href="#apply" mono className="mt-6 inline-block">
              Apply as a design partner →
            </TextLink>
          </div>
        </div>
      </SectionBody>
    </Section>
  );
}
