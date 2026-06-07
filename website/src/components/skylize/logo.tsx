import { cn } from "@/lib/utils";

/**
 * Skylize wordmark. The mark is the Altitude motif in miniature:
 * a framed instrument with an ascending line terminating in a blue node.
 */
export function Logo({
  className,
  showWordmark = true,
}: {
  className?: string;
  showWordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg
        width="22"
        height="22"
        viewBox="0 0 22 22"
        fill="none"
        aria-hidden
        className="shrink-0"
      >
        <rect
          x="0.75"
          y="0.75"
          width="20.5"
          height="20.5"
          rx="4.25"
          stroke="currentColor"
          strokeOpacity="0.3"
        />
        <path
          d="M4.5 15.5 L9 11 L12.5 13 L17.5 6.5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="17.5" cy="6.5" r="2" fill="#0047FF" />
      </svg>
      {showWordmark ? (
        <span className="font-display text-[1.05rem] font-semibold tracking-tight text-foreground">
          Skylize
        </span>
      ) : null}
    </span>
  );
}
