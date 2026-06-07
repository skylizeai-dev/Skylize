import { cn } from "@/lib/utils";
import type { CSSProperties } from "react";

interface AltitudeLineProps {
  /** Orientation of the line. */
  orientation?: "horizontal" | "vertical";
  /**
   * Edge treatment.
   * - `fade` (default): fades to transparent at both ends — the signature.
   * - `solid`: a constant hairline edge-to-edge.
   * - `accent`: a fading line that carries the brand blue at its centre.
   */
  variant?: "fade" | "solid" | "accent";
  className?: string;
  style?: CSSProperties;
}

/**
 * The Altitude Line — Skylize's signature 0.5px rule.
 *
 * A single, precise hairline used to mark altitude between sections,
 * inside cards, and across the navigation and footer. It is the most
 * recognizable element in the system.
 */
export function AltitudeLine({
  orientation = "horizontal",
  variant = "fade",
  className,
  style,
}: AltitudeLineProps) {
  const isHorizontal = orientation === "horizontal";
  const axis = isHorizontal ? "90deg" : "180deg";

  const background =
    variant === "solid"
      ? "var(--border-strong)"
      : variant === "accent"
        ? `linear-gradient(${axis}, transparent, var(--color-blue) 50%, transparent)`
        : `linear-gradient(${axis}, transparent, var(--border-strong) 12%, var(--border-strong) 88%, transparent)`;

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      aria-hidden="true"
      style={{
        background,
        width: isHorizontal ? "100%" : "0.5px",
        height: isHorizontal ? "0.5px" : "100%",
        flexShrink: 0,
        ...style,
      }}
      className={cn(isHorizontal ? "block" : "self-stretch", className)}
    />
  );
}
