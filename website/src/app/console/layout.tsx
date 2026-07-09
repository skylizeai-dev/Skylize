import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { AltitudeLine, Container, Logo } from "@/components/skylize";
import { LogoutButton } from "@/components/console/logout-button";

export const metadata: Metadata = {
  title: "Operator Console",
  robots: { index: false, follow: false },
};

/**
 * Console shell — the operator instrument frame.
 *
 * Server component: route protection lives in the proxy (owned by the BFF
 * work line), and the session is an httpOnly cookie this layout never reads.
 * The shell only frames: mono header, altitude hairlines, container rhythm.
 */
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <header className="sticky top-0 z-40 bg-background/90 backdrop-blur-sm">
        <Container wide className="flex h-14 items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              href="/console"
              className="rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Logo />
            </Link>
            <AltitudeLine
              orientation="vertical"
              variant="solid"
              style={{ height: "1.25rem" }}
            />
            <span className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
              Operator console
            </span>
          </div>
          <LogoutButton />
        </Container>
        <AltitudeLine />
      </header>

      <main className="flex flex-1 flex-col">{children}</main>

      <footer>
        <AltitudeLine />
        <Container wide className="flex h-12 items-center justify-between gap-4">
          <span className="font-mono text-[11px] tracking-[0.18em] text-muted-foreground/70 uppercase">
            Skylize · single-owner console
          </span>
          <span className="font-mono text-[11px] tracking-[0.18em] text-muted-foreground/70 uppercase">
            Interim gate · OIDC next
          </span>
        </Container>
      </footer>
    </div>
  );
}
