import { cn } from "@/lib/utils";

interface RoiMetricProps {
  value: string;
  label: string;
  sublabel?: string;
  className?: string;
}

export function RoiMetric({ value, label, sublabel, className }: RoiMetricProps) {
  return (
    <div className={cn("text-center", className)}>
      <p className="text-5xl font-bold tracking-tight text-foreground">
        {value}
      </p>
      <p className="mt-2 text-base font-medium text-foreground">{label}</p>
      {sublabel && (
        <p className="mt-1 text-sm text-muted-foreground">{sublabel}</p>
      )}
    </div>
  );
}

interface RoiSectionProps {
  metrics: RoiMetricProps[];
  className?: string;
}

export function RoiSection({ metrics, className }: RoiSectionProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-8 sm:grid-cols-3 lg:grid-cols-4",
        className,
      )}
    >
      {metrics.map((metric, i) => (
        <RoiMetric key={i} {...metric} />
      ))}
    </div>
  );
}
