"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";

/**
 * Honors prefers-reduced-motion for framer-motion animations. The global
 * CSS media query only neutralizes CSS transitions/animations — it cannot
 * stop framer-motion's JS-driven transforms, so this provider is required
 * for the motion language to actually respect the OS setting. Transform
 * travel is dropped; opacity still animates, so revealed content always
 * ends visible.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
