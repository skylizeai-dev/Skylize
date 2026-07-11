"use client";

import { useEffect, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "framer-motion";
import { EASE_ALTITUDE } from "@/lib/motion";
import { cn } from "@/lib/utils";

interface CounterProps {
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}

/**
 * Counts up to its target value when scrolled into view — numbers that ascend.
 */
export function Counter({
  value,
  decimals = 0,
  prefix = "",
  suffix = "",
  className,
}: CounterProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-15% 0px" });
  const reduceMotion = useReducedMotion();
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView || reduceMotion) return;
    const controls = animate(0, value, {
      duration: 1.4,
      ease: EASE_ALTITUDE,
      onUpdate: (v) => setDisplay(v),
    });
    return () => controls.stop();
  }, [inView, value, reduceMotion]);

  // MotionConfig doesn't reach imperative animate() calls, so the
  // reduced-motion path renders the final value directly.
  const shown = reduceMotion ? value : display;

  return (
    <span ref={ref} className={cn("tabular-nums", className)}>
      {prefix}
      {shown.toFixed(decimals)}
      {suffix}
    </span>
  );
}
