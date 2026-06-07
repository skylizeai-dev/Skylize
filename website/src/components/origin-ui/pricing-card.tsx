import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Check } from "lucide-react";

interface PricingCardProps {
  name: string;
  price: string;
  period?: string;
  description: string;
  features: string[];
  cta: string;
  highlighted?: boolean;
  badge?: string;
  onSelect?: () => void;
  className?: string;
}

export function PricingCard({
  name,
  price,
  period = "/month",
  description,
  features,
  cta,
  highlighted = false,
  badge,
  onSelect,
  className,
}: PricingCardProps) {
  return (
    <div
      className={cn(
        "relative flex flex-col rounded-2xl border p-8",
        highlighted
          ? "border-primary bg-primary text-primary-foreground shadow-xl"
          : "border-border bg-card",
        className,
      )}
    >
      {badge && (
        <Badge
          className={cn(
            "absolute -top-3 left-1/2 -translate-x-1/2",
            highlighted ? "bg-background text-foreground" : "bg-primary text-primary-foreground",
          )}
        >
          {badge}
        </Badge>
      )}
      <div className="mb-6">
        <h3 className="text-lg font-semibold">{name}</h3>
        <div className="mt-2 flex items-baseline gap-1">
          <span className="text-4xl font-bold tracking-tight">{price}</span>
          {period && (
            <span
              className={cn(
                "text-sm",
                highlighted ? "text-primary-foreground/70" : "text-muted-foreground",
              )}
            >
              {period}
            </span>
          )}
        </div>
        <p
          className={cn(
            "mt-2 text-sm",
            highlighted ? "text-primary-foreground/80" : "text-muted-foreground",
          )}
        >
          {description}
        </p>
      </div>
      <ul className="mb-8 flex-1 space-y-3">
        {features.map((feature) => (
          <li key={feature} className="flex items-center gap-2 text-sm">
            <Check className="h-4 w-4 shrink-0" />
            {feature}
          </li>
        ))}
      </ul>
      <Button
        onClick={onSelect}
        variant={highlighted ? "secondary" : "default"}
        className="w-full"
      >
        {cta}
      </Button>
    </div>
  );
}
