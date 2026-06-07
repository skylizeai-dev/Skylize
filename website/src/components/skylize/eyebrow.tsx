import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface EyebrowProps {
  children: ReactNode;
  /** Optional two-digit section index, e.g. "04". */
  index?: string;
  className?: string;
}

/**
 * A mono, uppercase section label preceded by a short altitude tick.
 * Used to title every section with editorial restraint.
 */
export function Eyebrow({ children, index, className }: EyebrowProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase",
        className,
      )}
    >
      <span aria-hidden className="h-px w-6 bg-border-strong" />
      {index ? <span className="text-blue/80 tabular-nums">{index}</span> : null}
      <span>{children}</span>
    </div>
  );
}
