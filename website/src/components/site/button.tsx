import Link from "next/link";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

/**
 * The paper surface's action. Square, weighted, no ornament — the primary
 * fills with brand blue and darkens to ink on hover, which is the only
 * colour transition the marketing pages make.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center font-medium whitespace-nowrap outline-none transition-colors duration-200 select-none focus-visible:outline-2 focus-visible:outline-offset-[3px] focus-visible:outline-blue disabled:pointer-events-none disabled:opacity-55",
  {
    variants: {
      variant: {
        primary: "bg-blue text-[#FAF9F6] hover:bg-ink",
        secondary:
          "border border-border text-foreground hover:border-foreground",
      },
      size: {
        sm: "px-[15px] py-[9px] text-[13px]",
        md: "px-[22px] py-[14px] text-[15px]",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type SiteButtonProps = {
  children: ReactNode;
  href?: string;
  className?: string;
} & VariantProps<typeof buttonVariants> &
  Omit<
    ComponentPropsWithoutRef<"a"> & ComponentPropsWithoutRef<"button">,
    "ref"
  >;

export function SiteButton({
  children,
  href,
  variant,
  size,
  className,
  ...props
}: SiteButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);

  if (href) {
    // In-page anchors stay plain <a>; route changes go through the router.
    const isAnchor = href.startsWith("#");
    if (isAnchor) {
      return (
        <a href={href} className={classes} {...props}>
          {children}
        </a>
      );
    }
    return (
      <Link href={href} className={classes} {...props}>
        {children}
      </Link>
    );
  }

  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}

/**
 * The understated text action — a blue mono line with a trailing arrow.
 * Used where a button would be too loud for the hierarchy.
 */
export function TextLink({
  href,
  children,
  className,
  mono = false,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  /** Mono-uppercase treatment, for section-foot links. */
  mono?: boolean;
}) {
  const classes = cn(
    "text-blue transition-colors duration-200 hover:text-foreground",
    mono
      ? "font-mono text-[11px] tracking-[0.14em] uppercase"
      : "text-[15px] font-medium",
    className,
  );

  if (href.startsWith("#")) {
    return (
      <a href={href} className={classes}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={classes}>
      {children}
    </Link>
  );
}
