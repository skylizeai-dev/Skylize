import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { Container, Eyebrow, Reveal } from "@/components/skylize";

const faqs = [
  {
    q: "How is this different from buying another AI tool?",
    a: "Tools wait for someone to use them. Skylize agents own outcomes — they run workflows end to end and report results. You're adding operational capacity, not another tab in the stack.",
  },
  {
    q: "Do we have to replace our existing systems?",
    a: "No. Skylize sits on top of the stack you already run — CRM, billing, support, data — through APIs and native integrations. There is no migration project and no rip-and-replace.",
  },
  {
    q: "How do you keep agents safe and in control?",
    a: "Every agent is scoped to specific workflows with guardrails, approval gates, and a complete audit trail. You decide what runs autonomously and what waits for a human in the loop.",
  },
  {
    q: "How long until we see results?",
    a: "Most teams have a first workflow live within a few weeks and measurable ROI inside the first quarter. Coverage expands from there as trust compounds.",
  },
  {
    q: "What about security and compliance?",
    a: "SOC 2 Type II, least-privilege access, encryption in transit and at rest, and strict data isolation per customer. Your data is never used to train shared models.",
  },
  {
    q: "How is pricing structured?",
    a: "Pricing scales with the operational load we take on — throughput and outcomes, not seats. Talk to us and we'll model pricing against your actual operation.",
  },
];

export function Faq() {
  return (
    <section id="faq" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr] lg:gap-16">
          <Reveal>
            <Eyebrow index="08">FAQ</Eyebrow>
            <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
              Questions, answered.
            </h2>
            <p className="mt-6 max-w-xs leading-relaxed text-muted-foreground">
              Still deciding?{" "}
              <a
                href="#contact"
                className="text-foreground underline decoration-border-strong underline-offset-4 transition-colors hover:decoration-blue"
              >
                Book a strategy call
              </a>{" "}
              and we&apos;ll walk through your operation.
            </p>
          </Reveal>

          <Reveal delay={0.05}>
            <Accordion className="border-t border-border">
              {faqs.map((item, i) => (
                <AccordionItem
                  key={item.q}
                  value={`item-${i}`}
                  className="border-b border-border"
                >
                  <AccordionTrigger className="gap-6 py-5 text-base font-medium text-foreground hover:no-underline sm:text-lg">
                    {item.q}
                  </AccordionTrigger>
                  <AccordionContent className="max-w-xl pb-5 text-[0.95rem] leading-relaxed text-muted-foreground">
                    {item.a}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
