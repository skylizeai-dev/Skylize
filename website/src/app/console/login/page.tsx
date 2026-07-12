"use client";

import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AltitudeLine, CtaButton, Eyebrow } from "@/components/skylize";
import { Input } from "@/components/ui/input";

/**
 * Interim single-owner gate. The passphrase travels once, in a POST body,
 * to /api/console/session — never in a URL, never logged, never stored in
 * the browser. A 204 means the BFF set the httpOnly "skylize_console"
 * cookie and the operator can enter.
 */
export default function ConsoleLoginPage() {
  const router = useRouter();
  const fieldId = useId();
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);

    try {
      const res = await fetch("/api/console/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (res.status === 204) {
        // Keep the button disabled while navigating; refresh() re-renders
        // server components with the fresh session cookie.
        router.replace("/console");
        router.refresh();
        return;
      }

      setError(
        res.status === 401
          ? "Incorrect passphrase."
          : `Sign-in failed (HTTP ${res.status}). Try again.`,
      );
    } catch {
      setError("Could not reach the console gateway. Try again.");
    }
    setPending(false);
  }

  return (
    <div className="flex flex-1 items-center justify-center px-6 py-20">
      <div className="w-full max-w-sm">
        <AltitudeLine variant="accent" />
        <div className="border-x border-b border-border bg-card px-8 pt-8 pb-9">
          <Eyebrow index="00">Operator access</Eyebrow>
          <h1 className="mt-5 font-display text-2xl font-semibold tracking-tight">
            Enter the console
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Single-owner interim gate. The session lives in an httpOnly
            cookie — nothing is kept in this browser.
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-5">
            <div className="space-y-2">
              <label
                htmlFor={fieldId}
                className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
              >
                Passphrase
              </label>
              <Input
                id={fieldId}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
                required
                spellCheck={false}
                disabled={pending}
                aria-invalid={error ? true : undefined}
                className="h-11"
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="font-mono text-xs tracking-wide text-blue"
              >
                {error}
              </p>
            ) : null}

            <CtaButton type="submit" disabled={pending} className="w-full">
              {pending ? "Verifying…" : "Enter console"}
            </CtaButton>
          </form>
        </div>
      </div>
    </div>
  );
}
