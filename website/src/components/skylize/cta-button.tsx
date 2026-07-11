import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { ArrowUpRight } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

const ctaVariants = cva(
  "group/cta relative inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap outline-none transition-[transform,background-color,border-color,color] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] select-none focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-2 focus-visible:ring-offset-background active:translate-y-px disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary:
          "bg-blue text-paper hover:bg-[color-mix(in_srgb,var(--color-blue)_88%,white)]",
        secondary:
          "border border-border-strong bg-transparent text-foreground hover:border-foreground/40 hover:bg-foreground/[0.04]",
        ghost:
          "text-foreground hover:bg-foreground/[0.04]",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-11 px-5 text-sm",
        lg: "h-12 px-6 text-[0.95rem]",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type CtaButtonProps = {
  children: ReactNode;
  href?: string;
  className?: string;
  /** Show the trailing ascent arrow that lifts on hover. */
  arrow?: boolean;
  icon?: ReactNode;
} & VariantProps<typeof ctaVariants> &
  Omit<ComponentPropsWithoutRef<"a"> & ComponentPropsWithoutRef<"button">, "ref">;

/**
 * The primary call-to-action. Crisp, weighted, and precise — the trailing
 * arrow rises on hover (never falls), reinforcing Precision Ascent.
 */
export function CtaButton({
  children,
  href,
  variant,
  size,
  arrow = false,
  icon,
  className,
  ...props
}: CtaButtonProps) {
  const classes = cn(ctaVariants({ variant, size }), className);
  const content = (
    <>
      {icon}
      {children}
      {arrow ? (
        <ArrowUpRight
          className="size-4 transition-transform duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover/cta:-translate-y-0.5 group-hover/cta:translate-x-0.5"
          aria-hidden
        />
      ) : null}
    </>
  );

  if (href) {
    return (
      <a href={href} className={classes} {...props}>
        {content}
      </a>
    );
  }

  return (
    <button className={classes} {...props}>
      {content}
    </button>
  );
}
