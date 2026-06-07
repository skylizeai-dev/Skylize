import { cn } from "@/lib/utils";
import type { ElementType, ReactNode } from "react";

interface ContainerProps {
  children: ReactNode;
  className?: string;
  /** Use the wider measure for hero / dashboard rows. */
  wide?: boolean;
  as?: ElementType;
}

/**
 * Centered max-width measure with consistent gutters.
 * The framing of every section — keeps the page on a single grid.
 */
export function Container({
  children,
  className,
  wide = false,
  as: Tag = "div",
}: ContainerProps) {
  return (
    <Tag
      className={cn(
        "mx-auto w-full px-6 sm:px-8",
        wide ? "max-w-[1280px]" : "max-w-[1200px]",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

interface SectionProps {
  children: ReactNode;
  className?: string;
  id?: string;
  /** Renders a top Altitude Line as the section's altitude marker. */
  ruled?: boolean;
}

/**
 * A vertical band of the page with the standard altitude rhythm.
 * Generous, intentional whitespace — aggressive negative space by design.
 */
export function Section({ children, className, id }: SectionProps) {
  return (
    <section
      id={id}
      className={cn(
        "relative scroll-mt-24 py-[clamp(5rem,12vh,9rem)]",
        className,
      )}
    >
      {children}
    </section>
  );
}
