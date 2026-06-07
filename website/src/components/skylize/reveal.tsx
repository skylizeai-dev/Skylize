"use client";

import { motion, type HTMLMotionProps } from "framer-motion";
import { ascend, ascendSubtle, stagger, viewportOnce, EASE_ALTITUDE } from "@/lib/motion";
import type { ReactNode } from "react";

interface RevealProps {
  children: ReactNode;
  className?: string;
  /** Delay before this block ascends, in seconds. */
  delay?: number;
  /** Less travel for dense / inline content. */
  subtle?: boolean;
}

/**
 * Reveals a block with the Precision Ascent motion as it enters the viewport.
 * Rises once, eases on the altitude curve, never overshoots.
 */
export function Reveal({ children, className, delay = 0, subtle = false }: RevealProps) {
  return (
    <motion.div
      className={className}
      variants={subtle ? ascendSubtle : ascend}
      initial="hidden"
      whileInView="show"
      viewport={viewportOnce}
      transition={{ delay, duration: subtle ? 0.6 : 0.8, ease: EASE_ALTITUDE }}
    >
      {children}
    </motion.div>
  );
}

/** Staggers its RevealItem children upward in sequence. */
export function RevealGroup({
  children,
  className,
  ...props
}: { children: ReactNode; className?: string } & HTMLMotionProps<"div">) {
  return (
    <motion.div
      className={className}
      variants={stagger}
      initial="hidden"
      whileInView="show"
      viewport={viewportOnce}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function RevealItem({
  children,
  className,
  subtle = true,
}: {
  children: ReactNode;
  className?: string;
  subtle?: boolean;
}) {
  return (
    <motion.div className={className} variants={subtle ? ascendSubtle : ascend}>
      {children}
    </motion.div>
  );
}
