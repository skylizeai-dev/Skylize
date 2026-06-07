import type { Variants, Transition } from "framer-motion";

/**
 * Precision Ascent — Skylize's motion language.
 * Everything rises into place. Nothing bounces, falls, or overshoots.
 */
export const EASE_ALTITUDE = [0.16, 1, 0.3, 1] as const;
export const EASE_ALTITUDE_IN = [0.7, 0, 0.84, 0] as const;

export const transitionAscent: Transition = {
  duration: 0.8,
  ease: EASE_ALTITUDE,
};

/** A single block ascending into view. */
export const ascend: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.8, ease: EASE_ALTITUDE },
  },
};

/** Smaller travel, for dense lists and inline items. */
export const ascendSubtle: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: EASE_ALTITUDE },
  },
};

/** Parent that releases children in an upward stagger. */
export const stagger: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.08, delayChildren: 0.04 },
  },
};

export const staggerFast: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.05 },
  },
};

/** Standard whileInView viewport config — reveal once, slightly early. */
export const viewportOnce = { once: true, margin: "-12% 0px -12% 0px" } as const;
