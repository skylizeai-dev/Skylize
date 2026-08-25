"use client";

import { useState, type FormEvent } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Check } from "lucide-react";
import { Container, AltitudeLine, CtaButton } from "@/components/skylize";
import { EASE_ALTITUDE } from "@/lib/motion";

type SubmitStatus = "idle" | "pending" | "sent" | "error";

export function FinalCta() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<SubmitStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || status === "pending") return;
    setStatus("pending");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: trimmed,
          email: trimmed,
          message: "Design partner application from the landing-page contact form.",
        }),
        signal: AbortSignal.timeout(10_000),
      });
      const data = (await res.json().catch(() => null)) as { error?: string } | null;
      if (!res.ok) {
        throw new Error(data?.error ?? "Something went wrong. Please try again.");
      }
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error && err.name === "TimeoutError"
          ? "The request timed out. Please try again."
          : err instanceof Error && err.message
            ? err.message
            : "Something went wrong. Please try again.",
      );
    }
  }

  return (
    <section id="contact" className="relative scroll-mt-24 overflow-hidden">
      <AltitudeLine variant="accent" />

      {/* Altitude backdrop */}
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div
          className="altitude-grid absolute inset-0 opacity-[0.4]"
          style={{
            maskImage: "radial-gradient(100% 80% at 50% 50%, black, transparent 75%)",
            WebkitMaskImage: "radial-gradient(100% 80% at 50% 50%, black, transparent 75%)",
          }}
        />
      </div>

      <Container>
        <div className="mx-auto flex max-w-2xl flex-col items-center py-[clamp(6rem,16vh,11rem)] text-center">
          <h2 className="text-balance text-[clamp(2.5rem,6vw,4.5rem)] font-semibold leading-[0.98] tracking-[-0.035em] text-foreground">
            Govern the agents before you trust them.
          </h2>
          <p className="mt-6 max-w-lg text-lg leading-relaxed text-muted-foreground">
            We are taking a small number of design partners. Tell us the
            workflow you cannot yet let an agent touch, and we&apos;ll show you the
            enforcement path that would make it safe.
          </p>

          <div className="mt-10 w-full max-w-md">
            <AnimatePresence mode="wait">
              {status === "sent" ? (
                <motion.div
                  key="sent"
                  role="status"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, ease: EASE_ALTITUDE }}
                  className="flex items-center justify-center gap-3 rounded-md border border-border bg-card px-5 py-4"
                >
                  <span className="flex size-6 items-center justify-center rounded-full" style={{ background: "var(--color-blue)" }}>
                    <Check className="size-3.5 text-paper" strokeWidth={3} />
                  </span>
                  <span className="text-sm text-foreground">
                    Thanks — we&apos;ll be in touch within one business day.
                  </span>
                </motion.div>
              ) : (
                <motion.form
                  key="form"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  onSubmit={submit}
                  className="flex flex-col gap-3 sm:flex-row"
                >
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    aria-label="Work email"
                    className="h-12 flex-1 rounded-md border border-border-strong bg-transparent px-4 text-sm text-foreground outline-none transition-colors duration-200 placeholder:text-muted-foreground/70 focus:border-blue focus:ring-2 focus:ring-blue/30"
                  />
                  <CtaButton
                    size="lg"
                    arrow
                    type="submit"
                    disabled={status === "pending"}
                    className="sm:shrink-0"
                  >
                    {status === "pending" ? "Sending…" : "Apply as a Design Partner"}
                  </CtaButton>
                </motion.form>
              )}
            </AnimatePresence>
            {status === "error" && errorMessage ? (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {errorMessage}
              </p>
            ) : null}
            <p className="mt-4 font-mono text-[11px] tracking-[0.1em] text-muted-foreground/70 uppercase">
              Pre-revenue · No customer data in shared models
            </p>
          </div>
        </div>
      </Container>
    </section>
  );
}
