import { cn } from "@/lib/utils";
import type { ElementType, ReactNode } from "react";

/**
 * Primitives for the paper surface — the public marketing pages.
 *
 * These are deliberately separate from @/components/skylize, which is shared
 * with the operator console and carries the dark instrument identity. The two
 * vocabularies do not mix: this one is editorial (serif display, square
 * corners, full 1px rules), that one is instrumental.
 */

/** The measure every marketing section is set to. */
export function SiteContainer({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: ElementType;
}) {
  return (
    <Tag
      className={cn(
        "mx-auto w-full max-w-[1180px] px-[clamp(1.25rem,4vw,3rem)]",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/**
 * A band of the page. Sections are separated by a hairline rule rather than
 * whitespace alone — the rule is what makes the page read as a document.
 */
export function Section({
  children,
  className,
  id,
  band = false,
  ruled = true,
}: {
  children: ReactNode;
  className?: string;
  id?: string;
  /** Fill with the warm secondary ground to break up a long page. */
  band?: boolean;
  /** Hairline at the top edge. On by default. */
  ruled?: boolean;
}) {
  return (
    <section
      id={id}
      className={cn(
        "scroll-mt-20",
        ruled && "border-t border-border",
        band && "bg-band",
        className,
      )}
    >
      {children}
    </section>
  );
}

/** The standard vertical rhythm inside a section. */
export function SectionBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <SiteContainer
      className={cn("py-[clamp(4rem,8vw,7.5rem)]", className)}
    >
      {children}
    </SiteContainer>
  );
}

/**
 * The mono section label. Numbered, because the page is meant to be read in
 * order — the numbers are the table of contents.
 */
export function Eyebrow({
  index,
  children,
  className,
}: {
  index?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "block font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase",
        className,
      )}
    >
      {index ? `${index} — ` : null}
      {children}
    </span>
  );
}

/** A short mono label, used for column heads and inline captions. */
export function MonoLabel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "font-mono text-[10.5px] leading-relaxed tracking-[0.14em] text-muted-foreground uppercase",
        className,
      )}
    >
      {children}
    </span>
  );
}

const displaySizes = {
  /** Page headline. */
  hero: "text-[clamp(2.375rem,6.4vw,4.75rem)] leading-[1.02] tracking-[-0.02em]",
  /** Section headline. */
  section: "text-[clamp(2rem,4.4vw,3.375rem)] leading-[1.08] tracking-[-0.015em]",
  /** Sub-section headline. */
  sub: "text-[clamp(1.625rem,3vw,2.25rem)] leading-[1.1] tracking-[-0.01em]",
  /** Card / step headline. */
  card: "text-[clamp(1.5rem,2.8vw,2.125rem)] leading-[1.12]",
} as const;

/**
 * The serif display heading. Instrument Serif ships one weight, so these are
 * always 400 — the size and the measure do the work, never the weight.
 */
export function Display({
  children,
  as: Tag = "h2",
  size = "section",
  className,
}: {
  children: ReactNode;
  as?: ElementType;
  size?: keyof typeof displaySizes;
  className?: string;
}) {
  return (
    <Tag
      className={cn(
        "font-serif font-normal text-foreground",
        displaySizes[size],
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/** Body copy at the page's reading size. */
export function Lede({
  children,
  className,
  tone = "muted",
}: {
  children: ReactNode;
  className?: string;
  /** `strong` promotes the line to full ink — use it for the point. */
  tone?: "muted" | "strong";
}) {
  return (
    <p
      className={cn(
        "text-[1.09rem] leading-[1.7]",
        tone === "strong" ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      {children}
    </p>
  );
}

/**
 * The key/value specification table. Every row states a property of the
 * architecture — what is signed, where it is checked, what the failure mode
 * is. Nothing here may become a performance number or an adoption figure.
 */
export function SpecTable({
  title = "Specification",
  rows,
  className,
}: {
  title?: string;
  rows: ReadonlyArray<{ k: string; v: string }>;
  className?: string;
}) {
  return (
    <div className={cn("border border-border px-[18px] pt-1 pb-1.5", className)}>
      <div className="py-3 font-mono text-[10.5px] tracking-[0.16em] text-muted-foreground uppercase">
        {title}
      </div>
      <dl>
        {rows.map((row) => (
          <div
            key={row.k}
            className="flex justify-between gap-5 border-t border-border py-[11px] font-mono text-xs tracking-[0.04em]"
          >
            <dt className="text-muted-foreground">{row.k}</dt>
            <dd className="text-right text-foreground">{row.v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
