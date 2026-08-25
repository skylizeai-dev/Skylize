"use client";

import { useState, type FormEvent } from "react";
import { SiteButton } from "../button";
import { Display, Eyebrow, Section, SectionBody } from "../primitives";

type SubmitStatus = "idle" | "pending" | "sent" | "error";

const fields = [
  { name: "name", label: "Name", type: "text", autoComplete: "name" },
  { name: "email", label: "Work email", type: "email", autoComplete: "email" },
  {
    name: "company",
    label: "Company",
    type: "text",
    autoComplete: "organization",
  },
] as const;

const inputClass =
  "border border-border bg-background px-3.5 py-3 text-base text-foreground outline-none transition-colors duration-200 focus:border-blue";

/**
 * The design-partner application.
 *
 * Posts the shape /api/contact already accepts — name, email, company,
 * message — so the mail transport behind it is untouched by this form. The
 * route owns validation and configuration failure; this component only
 * reports what the route says back.
 */
export function Apply() {
  const [status, setStatus] = useState<SubmitStatus>("idle");
  const [note, setNote] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (status === "pending") return;

    const form = e.currentTarget;
    const data = Object.fromEntries(
      new FormData(form).entries(),
    ) as Record<string, string>;

    // The route requires these three; don't spend a request to be told so.
    if (!data.name?.trim() || !data.email?.trim() || !data.message?.trim()) {
      setStatus("error");
      setNote("Name, work email, and a description are required.");
      return;
    }

    setStatus("pending");
    setNote(null);
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        signal: AbortSignal.timeout(10_000),
      });
      const payload = (await res.json().catch(() => null)) as {
        error?: string;
      } | null;
      if (!res.ok) {
        throw new Error(payload?.error ?? "Something went wrong. Please try again.");
      }
      form.reset();
      setStatus("sent");
      setNote("Received. You will hear back from the engineer building this.");
    } catch (err) {
      setStatus("error");
      setNote(
        err instanceof Error && err.name === "TimeoutError"
          ? "The request timed out. Please try again."
          : err instanceof Error && err.message
            ? err.message
            : "Something went wrong. Please try again.",
      );
    }
  }

  return (
    <Section id="apply" band>
      <SectionBody>
        <Eyebrow index="07">Apply</Eyebrow>
        <div className="mt-[18px] grid grid-cols-1 items-start gap-[clamp(2rem,5vw,5rem)] md:grid-cols-2">
          <div>
            <Display className="max-w-[16ch]">
              Bring us an agent that touches something real.
            </Display>
            <p className="mt-[22px] max-w-[52ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
              Tell us what your agents are allowed to do today, and how you
              know. The reply comes from the engineer building the enforcement
              layer.
            </p>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-5">
            {fields.map((field) => (
              <label key={field.name} className="flex flex-col gap-2">
                <span className="font-mono text-[10.5px] tracking-[0.16em] text-muted-foreground uppercase">
                  {field.label}
                </span>
                <input
                  name={field.name}
                  type={field.type}
                  autoComplete={field.autoComplete}
                  required={field.name !== "company"}
                  className={inputClass}
                />
              </label>
            ))}

            <label className="flex flex-col gap-2">
              <span className="font-mono text-[10.5px] tracking-[0.16em] text-muted-foreground uppercase">
                What your agents do
              </span>
              <textarea
                name="message"
                rows={4}
                required
                className={`${inputClass} resize-y leading-relaxed`}
              />
            </label>

            <div className="flex flex-wrap items-center gap-4">
              <SiteButton type="submit" disabled={status === "pending"}>
                {status === "pending" ? "Sending…" : "Apply as a Design Partner"}
              </SiteButton>
              {note ? (
                <span
                  role={status === "error" ? "alert" : "status"}
                  className="font-mono text-[11px] tracking-[0.08em] text-muted-foreground"
                >
                  {note}
                </span>
              ) : null}
            </div>
          </form>
        </div>
      </SectionBody>
    </Section>
  );
}
