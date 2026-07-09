"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { LogOut } from "lucide-react";

/**
 * Ends the operator session. DELETE /api/console/session clears the
 * httpOnly "skylize_console" cookie server-side; the browser never holds
 * a token to clean up. Hidden on the login route, where no session exists.
 */
export function LogoutButton() {
  const pathname = usePathname();
  const router = useRouter();
  const [pending, setPending] = useState(false);

  if (pathname === "/console/login") return null;

  async function logout() {
    setPending(true);
    try {
      await fetch("/api/console/session", { method: "DELETE" });
    } catch {
      // Gateway unreachable — still route to the gate; the cookie will be
      // rejected by the BFF either way.
    }
    router.replace("/console/login");
    router.refresh();
  }

  return (
    <button
      type="button"
      onClick={logout}
      disabled={pending}
      className="inline-flex h-8 items-center gap-2 rounded-md px-3 font-mono text-xs tracking-[0.18em] text-muted-foreground uppercase transition-colors duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] outline-none hover:bg-foreground/[0.04] hover:text-foreground focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50"
    >
      <LogOut className="size-3.5" aria-hidden />
      {pending ? "Ending session" : "Log out"}
    </button>
  );
}
