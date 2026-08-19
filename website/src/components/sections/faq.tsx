import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { Container, Eyebrow, Reveal } from "@/components/skylize";

/**
 * These answer the objections an evaluator actually raises about a pre-revenue
 * governance vendor. Nothing here may claim a certification, a customer, or a
 * measured result — if an answer needs a trust signal, it names a mechanism.
 */
const faqs = [
  {
    q: "What is your security model, concretely?",
    a: "Authority is a signed artifact, not a config flag. Before an agent acts it holds an ECDSA P-384 token naming its permitted tools, its budget ceiling, its delegation chain, and an expiry measured in minutes. Every outbound action verifies that token at the call site; unsigned, expired, out-of-scope, or replayed means the call does not leave the process. Tenants are isolated at the database layer, and the kill switch is consulted on every governed action.",
  },
  {
    q: "You are not SOC 2 certified. Why should we take security seriously here?",
    a: "We are not certified, and we will not imply otherwise — a compliance audit certifies process, and we are too early to have one worth attesting. What we can offer is the architecture itself: read the enforcement path, run it against your own threat model, and hold us to what it does rather than to a badge. Certification follows revenue and incorporation; it is on the roadmap, not on this page.",
  },
  {
    q: "What does becoming a design partner involve?",
    a: "A scoped engagement on one real workflow, not a company-wide rollout. You bring a governance requirement that matters to you; we implement against it and you get direct access to the people writing the code. In exchange we ask for honest feedback and permission to learn from the deployment. Terms are written per partner, and you can walk away.",
  },
  {
    q: "How far is this from production-ready?",
    a: "The enforcement path — token issuance, authority verification, kill switch, audit record — is implemented and under test. What is not proven is our system under someone else's load, integrations, and edge cases, which is exactly what the design-partner phase is for. We will tell you where the gaps are before you commit, and any partner deployment starts in a bounded scope with a human gate in front of consequential actions.",
  },
  {
    q: "How is pricing structured?",
    a: "Custom, based on deployment scope. There is no public price sheet because we have not yet tested one against a real deployment, and inventing a number now would only mean renegotiating later. Design-partner terms are set case by case and written down before any work starts.",
  },
  {
    q: "What happens to our data?",
    a: "Your data stays scoped to your tenant, isolated at the database layer, and is never used to train shared models. Agents reach your systems through credentials you grant and can revoke, and every access they make is recorded against the token that authorized it.",
  },
  {
    q: "What if an agent does something wrong anyway?",
    a: "Two answers. First, containment: the kill switch halts an agent, a department, or the whole tenant, and budget ceilings bound the damage a runaway loop can do before anyone notices. Second, accounting: the audit record ties the action to the token, the scope, and the delegation chain that permitted it, so the post-mortem is a replay rather than a reconstruction.",
  },
];

export function Faq() {
  return (
    <section id="faq" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr] lg:gap-16">
          <Reveal>
            <Eyebrow index="06">FAQ</Eyebrow>
            <h2 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
              The hard questions first.
            </h2>
            <p className="mt-6 max-w-xs leading-relaxed text-muted-foreground">
              Something not answered here?{" "}
              <a
                href="#contact"
                className="text-foreground underline decoration-border-strong underline-offset-4 transition-colors hover:decoration-blue"
              >
                Ask us directly
              </a>{" "}
              — we would rather lose the deal than oversell it.
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
