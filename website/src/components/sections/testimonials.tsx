import { Container, Eyebrow, AltitudeLine, Reveal, RevealGroup, RevealItem } from "@/components/skylize";

const featured = {
  quote:
    "We didn't add an AI tool. We added operational capacity. Skylize now runs the parts of the business that used to run us.",
  name: "Dana Okafor",
  role: "Chief Operating Officer, Meridian Logistics",
};

const testimonials = [
  {
    quote: "The first workflow paid for the entire engagement inside a quarter.",
    name: "Marcus Rehn",
    role: "VP Operations, Northwind",
  },
  {
    quote: "Our team finally works on the roadmap instead of the queue.",
    name: "Priya Nair",
    role: "Head of Support, Helix",
  },
  {
    quote: "Visibility we never had — every action and every cost, in real time.",
    name: "Tom Vasquez",
    role: "CFO, Vantage Capital",
  },
];

export function Testimonials() {
  return (
    <section id="testimonials" className="scroll-mt-24 py-[clamp(5rem,12vh,9rem)]">
      <Container>
        <Reveal>
          <Eyebrow index="07">Testimonials</Eyebrow>
        </Reveal>

        <Reveal delay={0.05} className="mt-10 max-w-4xl">
          <span aria-hidden className="block h-px w-10" style={{ background: "var(--color-blue)" }} />
          <blockquote className="mt-8 text-balance text-[clamp(1.75rem,4vw,3rem)] font-medium leading-[1.1] tracking-[-0.02em] text-foreground">
            “{featured.quote}”
          </blockquote>
          <footer className="mt-8 flex items-center gap-4">
            <span className="flex size-10 items-center justify-center rounded-full border border-border font-mono text-xs text-muted-foreground">
              {featured.name.split(" ").map((n) => n[0]).join("")}
            </span>
            <div>
              <div className="font-medium text-foreground">{featured.name}</div>
              <div className="font-mono text-[11px] tracking-[0.08em] text-muted-foreground uppercase">
                {featured.role}
              </div>
            </div>
          </footer>
        </Reveal>

        <RevealGroup className="mt-16 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-3">
          {testimonials.map((t) => (
            <RevealItem key={t.name} className="flex flex-col bg-card p-8">
              <p className="flex-1 leading-relaxed text-foreground/90">“{t.quote}”</p>
              <AltitudeLine className="my-6" />
              <div>
                <div className="text-sm font-medium text-foreground">{t.name}</div>
                <div className="mt-0.5 font-mono text-[10px] tracking-[0.1em] text-muted-foreground uppercase">
                  {t.role}
                </div>
              </div>
            </RevealItem>
          ))}
        </RevealGroup>
      </Container>
    </section>
  );
}
