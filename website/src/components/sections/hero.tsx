"use client";

import { motion } from "framer-motion";
import { Container } from "@/components/skylize";
import { CtaButton } from "@/components/skylize";
import { AltitudeDashboard } from "./altitude-dashboard";
import { EASE_ALTITUDE } from "@/lib/motion";

const headlineLines = ["Less manual work.", "More revenue.", "More scale."];

const fadeUp = {
  hidden: { opacity: 0, y: 22 },
  show: { opacity: 1, y: 0 },
};

export function Hero() {
  return (
    <section
      id="top"
      className="relative overflow-hidden pt-32 pb-20 sm:pt-40 sm:pb-24"
    >
      {/* Altitude backdrop — faint instrument grid fading into ink */}
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div
          className="altitude-grid absolute inset-0 opacity-[0.4]"
          style={{
            maskImage:
              "radial-gradient(120% 70% at 50% 0%, black, transparent 70%)",
            WebkitMaskImage:
              "radial-gradient(120% 70% at 50% 0%, black, transparent 70%)",
          }}
        />
        <div className="altitude-vignette absolute inset-0" />
      </div>

      <Container>
        <motion.div
          className="mx-auto flex max-w-3xl flex-col items-center text-center"
          initial="hidden"
          animate="show"
          variants={{ show: { transition: { staggerChildren: 0.1 } } }}
        >
          {/* Announcement chip */}
          <motion.a
            href="#agents"
            variants={fadeUp}
            transition={{ duration: 0.7, ease: EASE_ALTITUDE }}
            className="group inline-flex items-center gap-2 rounded-full border border-border px-3 py-1.5 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase transition-colors duration-200 hover:border-border-strong hover:text-foreground"
          >
            <span className="size-1.5 rounded-full" style={{ background: "var(--color-blue)" }} />
            Operational infrastructure, not another tool
          </motion.a>

          {/* Headline */}
          <h1 className="mt-7 text-balance text-[clamp(2.75rem,8vw,5.25rem)] font-semibold leading-[0.95] tracking-[-0.035em] text-foreground">
            {headlineLines.map((line, i) => (
              <motion.span
                key={line}
                variants={fadeUp}
                transition={{ duration: 0.8, ease: EASE_ALTITUDE }}
                className="block"
                style={i === headlineLines.length - 1 ? { color: "var(--color-paper)" } : undefined}
              >
                {line}
              </motion.span>
            ))}
          </h1>

          {/* Subheadline */}
          <motion.p
            variants={fadeUp}
            transition={{ duration: 0.8, ease: EASE_ALTITUDE }}
            className="mt-7 max-w-xl text-balance text-lg leading-relaxed text-muted-foreground"
          >
            AI systems and operational infrastructure that eliminate repetitive
            work and unlock growth.
          </motion.p>

          {/* CTAs */}
          <motion.div
            variants={fadeUp}
            transition={{ duration: 0.8, ease: EASE_ALTITUDE }}
            className="mt-9 flex flex-col items-center gap-3 sm:flex-row"
          >
            <CtaButton href="#contact" size="lg" arrow>
              Book Strategy Call
            </CtaButton>
            <CtaButton href="#how-it-works" size="lg" variant="secondary">
              See How It Works
            </CtaButton>
          </motion.div>

          {/* Micro trust row */}
          <motion.div
            variants={fadeUp}
            transition={{ duration: 0.8, ease: EASE_ALTITUDE }}
            className="mt-10 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 font-mono text-[11px] tracking-[0.1em] text-muted-foreground/80 uppercase"
          >
            <span>SOC 2 Type II</span>
            <span aria-hidden className="h-3 w-px bg-border-strong" />
            <span>No rip-and-replace</span>
            <span aria-hidden className="h-3 w-px bg-border-strong" />
            <span>Live in weeks</span>
          </motion.div>
        </motion.div>

        {/* Hero visual — Operational Altitude Dashboard */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, delay: 0.5, ease: EASE_ALTITUDE }}
          className="mx-auto mt-16 max-w-[1080px] sm:mt-20"
        >
          <AltitudeDashboard />
        </motion.div>
      </Container>
    </section>
  );
}
