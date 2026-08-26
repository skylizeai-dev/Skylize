"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { AnimatePresence, motion } from "framer-motion";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { SiteButton } from "./button";
import { SiteContainer } from "./primitives";
import { EASE_ALTITUDE } from "@/lib/motion";

/**
 * Every destination here resolves to a route that exists: the marketing
 * anchors on `/`, the two product tours, and the real console entry. Nothing
 * is linked ahead of the page that answers it.
 *
 * "What we solve" is deliberately absent, not missing. It is the first
 * section after the hero, so it is on screen before a nav link could take
 * anyone there — this list is for reaching what sits further down. Adding it
 * was considered and declined; don't file it as a gap.
 */
const links = [
  { label: "How it works", href: "/#how" },
  { label: "Controls", href: "/#controls" },
  { label: "Command Console", href: "/console-preview" },
  { label: "My Day", href: "/my-day" },
  { label: "Status", href: "/#status" },
  { label: "FAQ", href: "/#faq" },
] as const;

export type SitePage = "home" | "console-preview" | "my-day";

const currentHref: Record<SitePage, string | null> = {
  home: null,
  "console-preview": "/console-preview",
  "my-day": "/my-day",
};

export function SiteHeader({ current = "home" }: { current?: SitePage }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);

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

  const active = currentHref[current];

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background">
      <SiteContainer className="flex min-h-16 flex-wrap items-center justify-between gap-x-6 gap-y-3 py-2.5">
        <Link href="/" className="flex items-center gap-[9px]">
          <Image
            src="/skylize-logo-reversed.png"
            alt=""
            width={22}
            height={22}
            priority
            className="h-[22px] w-auto"
          />
          <span className="font-mono text-[13px] font-medium tracking-[0.18em] text-foreground">
            Skylize
          </span>
        </Link>

        {/* Desktop navigation */}
        <nav className="hidden flex-wrap items-center gap-x-[clamp(0.875rem,2.2vw,1.875rem)] gap-y-2.5 lg:flex">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active === link.href ? "page" : undefined}
              className={cn(
                "font-mono text-[11px] tracking-[0.12em] uppercase transition-colors duration-200",
                active === link.href
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {link.label}
            </Link>
          ))}
          <SiteButton href="/#apply" size="sm">
            Apply as a Design Partner
          </SiteButton>
        </nav>

        {/* Mobile trigger */}
        <button
          ref={triggerRef}
          type="button"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex size-10 items-center justify-center text-foreground lg:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </SiteContainer>

      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Site menu"
            className="fixed inset-0 top-16 z-40 bg-background lg:hidden"
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.32, ease: EASE_ALTITUDE }}
          >
            <div className="flex h-full flex-col px-[clamp(1.25rem,4vw,3rem)] py-8">
              <ul className="flex flex-col border-t border-border">
                {links.map((link, i) => (
                  <li key={link.href} className="border-b border-border">
                    <Link
                      ref={i === 0 ? firstLinkRef : undefined}
                      href={link.href}
                      onClick={() => setOpen(false)}
                      aria-current={active === link.href ? "page" : undefined}
                      className="flex items-baseline justify-between gap-4 py-4 font-serif text-2xl font-normal text-foreground"
                    >
                      {link.label}
                      <span className="font-mono text-[11px] tracking-[0.12em] text-muted-foreground tabular-nums">
                        0{i + 1}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              <div className="mt-auto pt-8">
                <SiteButton
                  href="/#apply"
                  className="w-full"
                  onClick={() => setOpen(false)}
                >
                  Apply as a Design Partner
                </SiteButton>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
