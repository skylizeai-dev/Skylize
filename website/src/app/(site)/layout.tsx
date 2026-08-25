import type { Viewport } from "next";
import type { ReactNode } from "react";

/**
 * The public marketing surface.
 *
 * Everything under this group renders on the paper skin: warm off-white
 * ground, ink type, hairline rules, Instrument Serif headlines. The token
 * override is deliberately scoped to this wrapper rather than :root — the
 * operator console at /console keeps the dark instrument identity, which is
 * also what the console screenshots on these pages depict.
 */
export const viewport: Viewport = {
  themeColor: "#FAF9F6",
  colorScheme: "light",
  width: "device-width",
  initialScale: 1,
};

export default function SiteLayout({ children }: { children: ReactNode }) {
  return (
    <div className="theme-paper min-h-dvh bg-background text-foreground">
      {/*
        Fail visible. The scroll reveals ship their hidden state inline
        (opacity:0), so without JS the copy is in the HTML but invisible —
        blank sections on the page whose entire argument is that it does not
        oversell. If the animation cannot run, show the content instead.
      */}
      <noscript>
        <style
          dangerouslySetInnerHTML={{
            __html:
              '.theme-paper [style*="opacity:0"]{opacity:1!important;transform:none!important}',
          }}
        />
      </noscript>
      {children}
    </div>
  );
}
