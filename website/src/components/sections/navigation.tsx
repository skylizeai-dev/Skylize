"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/skylize/logo";
import { CtaButton } from "@/components/skylize";
import { EASE_ALTITUDE } from "@/lib/motion";

const links = [
  { label: "Platform", href: "#solution" },
  { label: "How it works", href: "#how-it-works" },
  { label: "Agents", href: "#agents" },
  { label: "Results", href: "#roi" },
  { label: "FAQ", href: "#faq" },
];

export function Navigation() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  // Modal keyboard contract: focus moves into the menu on open, Escape
  // dismisses and hands focus back to the trigger.
  useEffect(() => {
    if (!open) return;
    firstLinkRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="fixed inset-x-0 top-0 z-50">
      <div
        className={cn(
          "transition-colors duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          scrolled
            ? "border-b border-border bg-background"
            : "border-b border-transparent",
        )}
      >
        <nav className="mx-auto flex h-16 w-full max-w-[1200px] items-center justify-between px-6 sm:px-8">
          <a href="#top" aria-label="Skylize — home" className="relative z-10">
            <Logo />
          </a>

          {/* Desktop links */}
          <ul className="hidden items-center gap-8 md:flex">
            {links.map((link) => (
              <li key={link.href}>
                <a
                  href={link.href}
                  className="text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground"
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>

          <div className="hidden items-center gap-3 md:flex">
            <a
              href="/console/login"
              className="text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground"
            >
              Sign in
            </a>
            <CtaButton href="#contact" arrow>
              Book Strategy Call
            </CtaButton>
          </div>

          {/* Mobile trigger */}
          <button
            ref={triggerRef}
            type="button"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="relative z-10 inline-flex size-10 items-center justify-center rounded-md text-foreground md:hidden"
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </nav>
      </div>

      {/* Mobile menu */}
      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Site menu"
            className="fixed inset-0 top-16 z-40 bg-background md:hidden"
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.32, ease: EASE_ALTITUDE }}
          >
            <div className="flex h-full flex-col px-6 py-8">
              <ul className="flex flex-col">
                {links.map((link, i) => (
                  <motion.li
                    key={link.href}
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.06 * i, duration: 0.4, ease: EASE_ALTITUDE }}
                    className="border-b border-border"
                  >
                    <a
                      ref={i === 0 ? firstLinkRef : undefined}
                      href={link.href}
                      onClick={() => setOpen(false)}
                      className="flex items-center justify-between py-4 font-display text-xl tracking-tight text-foreground"
                    >
                      {link.label}
                      <span className="font-mono text-xs text-muted-foreground tabular-nums">
                        0{i + 1}
                      </span>
                    </a>
                  </motion.li>
                ))}
              </ul>
              <div className="mt-auto flex flex-col gap-3">
                <CtaButton
                  href="#contact"
                  size="lg"
                  arrow
                  className="w-full"
                  onClick={() => setOpen(false)}
                >
                  Book Strategy Call
                </CtaButton>
                <CtaButton
                  href="/console/login"
                  variant="secondary"
                  size="lg"
                  className="w-full"
                  onClick={() => setOpen(false)}
                >
                  Sign in
                </CtaButton>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
