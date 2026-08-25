import Link from "next/link";
import Image from "next/image";
import { SiteContainer } from "./primitives";

/**
 * Every entry below resolves to something that exists today: the anchors on
 * `/`, the two product tours, and the console sign-in. Columns for pages we
 * have not written (docs, blog, privacy, careers) are deliberately absent —
 * a footer full of dead links is the cheapest way to look bigger than you
 * are, and this page is not doing that.
 */
const links = [
  { label: "How it works", href: "/#how" },
  { label: "Controls", href: "/#controls" },
  { label: "Command Console", href: "/console-preview" },
  { label: "My Day", href: "/my-day" },
  { label: "Status", href: "/#status" },
  { label: "FAQ", href: "/#faq" },
  { label: "Sign in", href: "/console/login" },
] as const;

export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <SiteContainer className="flex flex-wrap items-baseline justify-between gap-6 pt-9 pb-12">
        <div className="flex flex-col gap-2.5">
          <span className="flex items-center gap-[9px]">
            <Image
              src="/skylize-logo-reversed.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-auto"
            />
            <span className="font-mono text-[13px] font-medium tracking-[0.18em] text-foreground uppercase">
              Skylize
            </span>
          </span>
          <span className="text-sm leading-relaxed text-muted-foreground">
            Permission enforcement for AI agents.
          </span>
        </div>

        <nav className="flex flex-wrap items-baseline gap-[clamp(0.875rem,2.4vw,1.75rem)]">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors duration-200 hover:text-foreground"
            >
              {link.label}
            </Link>
          ))}
          <Link
            href="/#apply"
            className="font-mono text-[11px] tracking-[0.12em] text-blue uppercase transition-colors duration-200 hover:text-foreground"
          >
            Apply
          </Link>
          <span className="font-mono text-[11px] tracking-[0.08em] text-muted-foreground">
            © {new Date().getFullYear()} Skylize
          </span>
        </nav>
      </SiteContainer>
    </footer>
  );
}
