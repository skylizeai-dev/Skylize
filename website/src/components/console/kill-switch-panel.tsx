"use client";

import { useId, useState } from "react";
import { OctagonMinus } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AltitudeLine, CtaButton, Eyebrow } from "@/components/skylize";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/** View model for POST /api/console/kill-switch. */
interface KillSwitchResultView {
  status: string;
}

// Scope types the backend asserts on (kill_switch.py `_SCOPES`, mirrored by the
// BFF's zod enum). "workflow" is NOT a backend scope — sending it would 400.
const SCOPES = [
  { value: "tenant", label: "Tenant" },
  { value: "department", label: "Department" },
  { value: "agent", label: "Agent" },
  { value: "platform", label: "Platform" },
] as const;

type ScopeType = (typeof SCOPES)[number]["value"];

/**
 * Owner action: halt execution for a scope. Deliberately two-step — the
 * request is only sent from an explicit confirm dialog that repeats the
 * scope and reason back to the operator.
 */
export function KillSwitchPanel() {
  const baseId = useId();
  const [scopeType, setScopeType] = useState<ScopeType>("tenant");
  const [scopeId, setScopeId] = useState("");
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [result, setResult] = useState<KillSwitchResultView | null>(null);

  function requestConfirm() {
    if (scopeId.trim() === "" || reason.trim() === "") {
      setFormError("Scope ID and reason are both required.");
      return;
    }
    setFormError(null);
    setDialogError(null);
    setConfirmOpen(true);
  }

  async function engage() {
    if (pending) return;
    setPending(true);
    setDialogError(null);
    try {
      const res = await fetch("/api/console/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope_type: scopeType,
          scope_id: scopeId.trim(),
          reason: reason.trim(),
        }),
      });

      if (res.status === 401) {
        setDialogError("Session expired — log out and sign in again.");
        return;
      }
      if (!res.ok) {
        setDialogError(`The kill switch request failed (HTTP ${res.status}).`);
        return;
      }

      setResult((await res.json()) as KillSwitchResultView);
      setConfirmOpen(false);
    } catch {
      setDialogError("Could not reach the console gateway.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      aria-labelledby={`${baseId}-title`}
      className="border border-border-strong bg-card"
    >
      <div className="px-7 pt-7 pb-7 sm:px-8">
        <Eyebrow index="03">Owner action</Eyebrow>
        <h2
          id={`${baseId}-title`}
          className="mt-4 flex items-center gap-2.5 font-display text-xl font-semibold tracking-tight"
        >
          <OctagonMinus className="size-5 text-blue" aria-hidden />
          Kill switch
        </h2>
        <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Immediately halts execution for the selected scope. This is the
          hard stop — use it when an agent or workflow must not take another
          step.
        </p>

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <div className="space-y-2">
            <span
              id={`${baseId}-scope-label`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Scope
            </span>
            <div
              role="radiogroup"
              aria-labelledby={`${baseId}-scope-label`}
              className="inline-flex overflow-hidden rounded-md border border-input"
            >
              {SCOPES.map((scope) => (
                <button
                  key={scope.value}
                  type="button"
                  role="radio"
                  aria-checked={scopeType === scope.value}
                  onClick={() => setScopeType(scope.value)}
                  className={cn(
                    "h-10 px-4 font-mono text-xs tracking-[0.14em] uppercase transition-colors duration-200 outline-none not-first:border-l not-first:border-input focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-inset",
                    scopeType === scope.value
                      ? "bg-blue text-paper"
                      : "text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground",
                  )}
                >
                  {scope.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label
              htmlFor={`${baseId}-scope-id`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Scope ID
            </label>
            <Input
              id={`${baseId}-scope-id`}
              value={scopeId}
              onChange={(e) => setScopeId(e.target.value)}
              placeholder={
                scopeType === "tenant" ? "org id" : `${scopeType} id`
              }
              spellCheck={false}
              className="h-10 font-mono text-sm"
            />
          </div>

          <div className="space-y-2 sm:col-span-2">
            <label
              htmlFor={`${baseId}-reason`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Reason
            </label>
            <Textarea
              id={`${baseId}-reason`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why execution must stop — recorded with the halt."
              rows={2}
            />
          </div>
        </div>

        {formError ? (
          <p role="alert" className="mt-4 font-mono text-xs tracking-wide text-blue">
            {formError}
          </p>
        ) : null}

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <CtaButton variant="secondary" onClick={requestConfirm}>
            <OctagonMinus className="size-4" aria-hidden />
            Engage kill switch
          </CtaButton>
          {result ? (
            <span
              role="status"
              className="font-mono text-xs tracking-[0.2em] text-blue uppercase"
            >
              Engaged · status: {result.status}
            </span>
          ) : null}
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Engage the kill switch?</DialogTitle>
            <DialogDescription>
              Execution halts immediately for this scope. Confirm the target
              before proceeding.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5 border border-border bg-background/60 p-4 font-mono text-xs">
            <AltitudeLine variant="accent" className="mb-3" />
            <p className="tracking-[0.14em] uppercase">
              <span className="text-muted-foreground">scope · </span>
              {scopeType} / {scopeId.trim()}
            </p>
            <p className="text-muted-foreground">{reason.trim()}</p>
          </div>

          {dialogError ? (
            <p role="alert" className="font-mono text-xs tracking-wide text-blue">
              {dialogError}
            </p>
          ) : null}

          <DialogFooter>
            <DialogClose
              className="inline-flex h-11 items-center justify-center rounded-md px-5 text-sm font-medium text-muted-foreground transition-colors duration-200 outline-none hover:bg-foreground/[0.04] hover:text-foreground focus-visible:ring-2 focus-visible:ring-blue"
              disabled={pending}
            >
              Cancel
            </DialogClose>
            <CtaButton onClick={engage} disabled={pending}>
              {pending ? "Engaging…" : "Confirm — halt now"}
            </CtaButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
