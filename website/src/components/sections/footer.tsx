import { Container, AltitudeLine, Logo } from "@/components/skylize";

// Only sections that exist on the site today. Add a column back once its
// pages (About, Careers, Docs, Blog, Status, Privacy, Terms, DPA, ...) ship.
const columns = [
  {
    title: "Product",
    links: [
      { label: "Controls", href: "#controls" },
      { label: "How it works", href: "#how-it-works" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "Status", href: "#status" },
      { label: "Contact", href: "#contact" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="relative border-t border-border">
      <Container>
        <div className="grid grid-cols-2 gap-10 py-16 sm:grid-cols-[2fr_1fr_1fr]">
          <div className="col-span-2 sm:col-span-1">
            <Logo />
            <p className="mt-5 max-w-xs text-sm leading-relaxed text-muted-foreground">
              A governance layer for AI agents. Signed authority, enforced
              budgets, and an audit trail for every action.
            </p>
          </div>

          {columns.map((col) => (
            <nav key={col.title} aria-label={col.title}>
              <h3 className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground/70 uppercase">
                {col.title}
              </h3>
              <ul className="mt-4 flex flex-col gap-3">
                {col.links.map((link) => (
                  <li key={link.label}>
                    <a
                      href={link.href}
                      className="text-sm text-foreground/80 transition-colors duration-200 hover:text-foreground"
                    >
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <AltitudeLine variant="solid" />

        <div className="flex flex-col items-start justify-between gap-4 py-7 sm:flex-row sm:items-center">
          <p className="font-mono text-[11px] tracking-[0.08em] text-muted-foreground">
            © {new Date().getFullYear()} Skylize
          </p>

          <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.08em] text-muted-foreground">
            <span className="size-1.5 rounded-full" style={{ background: "var(--color-blue)" }} />
            Pre-launch
          </div>
        </div>
      </Container>

      {/* Oversized watermark — brand presence, clipped at the baseline */}
      <div
        aria-hidden
        className="pointer-events-none flex justify-center overflow-hidden select-none"
      >
        <span className="-mb-[0.18em] translate-y-[0.22em] font-display text-[18vw] leading-none font-semibold tracking-[-0.04em] text-foreground/[0.04]">
          Skylize
        </span>
      </div>
    </footer>
  );
}
