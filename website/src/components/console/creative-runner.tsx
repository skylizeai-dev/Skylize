"use client";

import { useId, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import { AltitudeLine, CtaButton, Eyebrow } from "@/components/skylize";
import { Input } from "@/components/ui/input";

/** View model for POST /api/console/workflows/creative — deliberately ours,
 *  not imported from the BFF work line. */
interface CreativeRunView {
  status: string;
  hooks: string[];
}

type RunState =
  | { phase: "idle" }
  | { phase: "running" }
  | { phase: "done"; result: CreativeRunView }
  | { phase: "error"; message: string };

const EASE_ALTITUDE = [0.16, 1, 0.3, 1] as const;

/**
 * The round-trip proof: product + audience go browser → BFF → backend
 * creative crew, and the generated hooks come all the way back.
 */
export function CreativeRunner() {
  const baseId = useId();
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
  const [count, setCount] = useState("");
  const [state, setState] = useState<RunState>({ phase: "idle" });

  const pending = state.phase === "running";

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const body: { product: string; audience: string; count?: number } = {
      product: product.trim(),
      audience: audience.trim(),
    };
    if (count.trim() !== "") {
      const parsed = Number(count);
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 10) {
        setState({
          phase: "error",
          message: "Count must be a whole number between 1 and 10.",
        });
        return;
      }
      body.count = parsed;
    }

    setState({ phase: "running" });
    try {
      const res = await fetch("/api/console/workflows/creative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (res.status === 401) {
        setState({
          phase: "error",
          message: "Session expired — log out and sign in again.",
        });
        return;
      }
      if (!res.ok) {
        setState({
          phase: "error",
          message: `The workflow run failed (HTTP ${res.status}).`,
        });
        return;
      }

      const data = (await res.json()) as CreativeRunView;
      if (!Array.isArray(data.hooks)) {
        setState({
          phase: "error",
          message: "The gateway returned an unexpected payload.",
        });
        return;
      }
      setState({ phase: "done", result: data });
    } catch {
      setState({
        phase: "error",
        message:
          "Could not reach the console gateway — is the BFF mounted and the backend up?",
      });
    }
  }

  return (
    <section
      aria-labelledby={`${baseId}-title`}
      className="border border-border bg-card"
    >
      <div className="px-7 pt-7 pb-6 sm:px-8">
        <Eyebrow index="04">Workflow · creative crew</Eyebrow>
        <h2
          id={`${baseId}-title`}
          className="mt-4 font-display text-xl font-semibold tracking-tight"
        >
          Creative hook runner
        </h2>
        <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Runs the creative workflow end to end — browser to gateway to
          backend crew and back. The hooks below are live output, not fixture
          data.
        </p>

        <form onSubmit={run} className="mt-6 grid gap-5 sm:grid-cols-[1fr_1fr_7rem]">
          <div className="space-y-2">
            <label
              htmlFor={`${baseId}-product`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Product
            </label>
            <Input
              id={`${baseId}-product`}
              value={product}
              onChange={(e) => setProduct(e.target.value)}
              placeholder="e.g. AI bookkeeping copilot"
              required
              disabled={pending}
              className="h-10"
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor={`${baseId}-audience`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Audience
            </label>
            <Input
              id={`${baseId}-audience`}
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              placeholder="e.g. solo founders"
              required
              disabled={pending}
              className="h-10"
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor={`${baseId}-count`}
              className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
            >
              Count
            </label>
            <Input
              id={`${baseId}-count`}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              inputMode="numeric"
              placeholder="5"
              disabled={pending}
              className="h-10"
            />
          </div>

          <div className="sm:col-span-3">
            <CtaButton type="submit" size="md" arrow disabled={pending}>
              {pending ? "Running workflow…" : "Run"}
            </CtaButton>
          </div>
        </form>
      </div>

      {state.phase === "error" ? (
        <div className="px-7 pb-7 sm:px-8">
          <AltitudeLine className="mb-5" />
          <p role="alert" className="font-mono text-xs tracking-wide text-blue">
            {state.message}
          </p>
        </div>
      ) : null}

      {state.phase === "done" ? (
        <div className="px-7 pb-7 sm:px-8">
          <AltitudeLine className="mb-5" />
          <div className="flex items-center justify-between gap-4">
            <span className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
              {state.result.hooks.length} hooks
            </span>
            <span className="font-mono text-xs tracking-[0.2em] text-blue/80 uppercase">
              status · {state.result.status}
            </span>
          </div>
          {state.result.hooks.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              The workflow completed but returned no hooks.
            </p>
          ) : (
            <ol className="mt-4 space-y-3">
              {state.result.hooks.map((hook, i) => (
                <motion.li
                  key={`${i}-${hook}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: 0.4,
                    delay: i * 0.06,
                    ease: EASE_ALTITUDE,
                  }}
                  className="flex gap-4 text-sm leading-relaxed"
                >
                  <span className="font-mono text-xs leading-6 text-blue/80 tabular-nums">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span>{hook}</span>
                </motion.li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </section>
  );
}
