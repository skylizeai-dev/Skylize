import { cn } from "@/lib/utils";
import { ReactNode } from "react";

interface TrustBadgeProps {
  icon: ReactNode;
  label: string;
  sublabel?: string;
  className?: string;
}

export function TrustBadge({ icon, label, sublabel, className }: TrustBadgeProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3",
        className,
      )}
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground">
        {icon}
      </div>
      <div>
        <p className="text-sm font-medium text-foreground">{label}</p>
        {sublabel && (
          <p className="text-xs text-muted-foreground">{sublabel}</p>
        )}
      </div>
    </div>
  );
}

interface TrustGridProps {
  items: TrustBadgeProps[];
  className?: string;
}

export function TrustGrid({ items, className }: TrustGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4",
        className,
      )}
    >
      {items.map((item, i) => (
        <TrustBadge key={i} {...item} />
      ))}
    </div>
  );
}
