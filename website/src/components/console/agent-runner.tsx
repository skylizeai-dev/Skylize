"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { AltitudeLine, CtaButton, Eyebrow } from "@/components/skylize";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type {
  BackendAgentInfo,
  ConsoleDeliverable,
  ConsoleExecuteApproved,
  ConsoleExecuteDeferred,
} from "@/lib/skylize/types";

// ---------------------------------------------------------------------------
// Input fields are derived from each agent's LIVE JSON Schema
// (GET /api/console/agents -> input_schema), never from a hardcoded form.
// ---------------------------------------------------------------------------

interface FieldSpec {
  name: string;
  kind: "string" | "integer" | "number" | "boolean" | "enum" | "json";
  required: boolean;
  options?: string[];
  hint?: string;
}

function resolveKind(prop: Record<string, unknown>): FieldSpec["kind"] {
  if (Array.isArray(prop.enum) && prop.enum.every((v) => typeof v === "string")) {
    return "enum";
  }
  let type = prop.type;
  if (type === undefined && Array.isArray(prop.anyOf)) {
    const nonNull = (prop.anyOf as Record<string, unknown>[]).filter(
      (entry) => entry.type !== "null",
    );
    if (nonNull.length === 1) {
      if (
        Array.isArray(nonNull[0].enum) &&
        (nonNull[0].enum as unknown[]).every((v) => typeof v === "string")
      ) {
        return "enum";
      }
      type = nonNull[0].type;
    }
  }
  if (type === "string") return "string";
  if (type === "integer") return "integer";
  if (type === "number") return "number";
  if (type === "boolean") return "boolean";
  return "json"; // arrays, objects, $ref, unresolved unions
}

function enumOptions(prop: Record<string, unknown>): string[] | undefined {
  if (Array.isArray(prop.enum)) return prop.enum.map(String);
  if (Array.isArray(prop.anyOf)) {
    for (const entry of prop.anyOf as Record<string, unknown>[]) {
      if (Array.isArray(entry.enum)) return entry.enum.map(String);
    }
  }
  return undefined;
}

function fieldsFor(schema: Record<string, unknown>): FieldSpec[] {
  const properties = (schema.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = new Set(
    Array.isArray(schema.required) ? (schema.required as string[]) : [],
  );
  return Object.entries(properties).map(([name, prop]) => {
    const kind = resolveKind(prop);
    return {
      name,
      kind,
      required: required.has(name),
      options: kind === "enum" ? enumOptions(prop) : undefined,
      hint:
        typeof prop.description === "string"
          ? prop.description
          : prop.default !== undefined && prop.default !== null
            ? `default: ${JSON.stringify(prop.default)}`
            : undefined,
    };
  });
}

/** Assemble the request payload from raw field text; throws a message on the
 *  first field that cannot be encoded. Empty optional fields are omitted so
 *  the backend's own defaults apply. */
function buildInput(
  fields: FieldSpec[],
  values: Record<string, string>,
  checks: Record<string, boolean>,
): Record<string, unknown> {
  const input: Record<string, unknown> = {};
  for (const field of fields) {
    if (field.kind === "boolean") {
      if (checks[field.name] !== undefined) input[field.name] = checks[field.name];
      continue;
    }
    const raw = (values[field.name] ?? "").trim();
    if (raw === "") {
      if (field.required) throw new Error(`"${field.name}" is required.`);
      continue;
    }
    if (field.kind === "integer") {
      const parsed = Number(raw);
      if (!Number.isInteger(parsed)) {
        throw new Error(`"${field.name}" must be a whole number.`);
      }
      input[field.name] = parsed;
    } else if (field.kind === "number") {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) {
        throw new Error(`"${field.name}" must be a number.`);
      }
      input[field.name] = parsed;
    } else if (field.kind === "json") {
      try {
        input[field.name] = JSON.parse(raw);
      } catch {
        throw new Error(`"${field.name}" must be valid JSON.`);
      }
    } else {
      input[field.name] = raw;
    }
  }
  return input;
}

// ---------------------------------------------------------------------------
// Outcome — the governance verdict is the headline, never a toast.
// ---------------------------------------------------------------------------

type Outcome =
  | { kind: "approved"; response: ConsoleExecuteApproved }
  | { kind: "deferred"; response: ConsoleExecuteDeferred }
  | { kind: "rejected"; reason: string };

type RunState =
  | { phase: "idle" }
  | { phase: "running" }
  | { phase: "done"; outcome: Outcome }
  | { phase: "error"; message: string };

type AgentsState =
  | { phase: "loading" }
  | { phase: "ready"; agents: BackendAgentInfo[] }
  | { phase: "error"; message: string };

const SELECT_CLASSES =
  "h-10 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30";

function StepBox({
  label,
  state,
  active,
}: {
  label: string;
  state: string;
  active: boolean;
}) {
  return (
    <div
      className={cn(
        "border px-4 py-3",
        active ? "border-blue/60 bg-blue/5" : "border-border",
      )}
    >
      <p className="font-mono text-[0.65rem] tracking-[0.2em] text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-1.5 text-sm leading-snug",
          active ? "text-blue" : "text-foreground",
        )}
      >
        {state}
      </p>
    </div>
  );
}

/** The three-stage governance strip: evaluated -> human gate -> execution. */
function GovernanceOutcome({ outcome }: { outcome: Outcome }) {
  const headline =
    outcome.kind === "approved"
      ? "APPROVED"
      : outcome.kind === "deferred"
        ? "DEFERRED TO HUMAN"
        : "REJECTED";
  return (
    <div>
      <p className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
        Decision engine verdict
      </p>
      <p
        role="status"
        className="mt-2 font-display text-2xl font-semibold tracking-tight"
      >
        {headline}
      </p>
      {outcome.kind === "deferred" ? (
        <p className="mt-1 font-mono text-xs tracking-wide text-muted-foreground">
          hitl · {outcome.response.hitl_id}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <StepBox
          label="1 · evaluated"
          state="Proposal ran the synchronous decision gate"
          active={false}
        />
        {outcome.kind === "approved" ? (
          <StepBox
            label="2 · human gate"
            state="Not required — no human-in-loop trigger on this agent"
            active={false}
          />
        ) : outcome.kind === "deferred" ? (
          <StepBox
            label="2 · human gate"
            state={`REQUIRED — ${outcome.response.reason}`}
            active
          />
        ) : (
          <StepBox label="2 · human gate" state={outcome.reason} active />
        )}
        {outcome.kind === "approved" ? (
          <StepBox
            label="3 · execution"
            state={`Executed — deliverable ${outcome.response.deliverable_id}`}
            active
          />
        ) : outcome.kind === "deferred" ? (
          <StepBox
            label="3 · execution"
            state="Blocked — nothing runs until a human approves (see pending approvals below)"
            active={false}
          />
        ) : (
          <StepBox
            label="3 · execution"
            state="Never executed — no LLM call, no deliverable, no spend"
            active={false}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The runner
// ---------------------------------------------------------------------------

export function AgentRunner({
  onDeferred,
  onDeliverable,
}: {
  onDeferred: () => void;
  onDeliverable: (deliverable: ConsoleDeliverable) => void;
}) {
  const baseId = useId();
  const [agents, setAgents] = useState<AgentsState>({ phase: "loading" });
  const [agentId, setAgentId] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [state, setState] = useState<RunState>({ phase: "idle" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/console/agents", { cache: "no-store" });
        if (!res.ok) {
          const message =
            res.status === 401
              ? "Session expired — log out and sign in again."
              : `Could not load the agent list (HTTP ${res.status}).`;
          if (!cancelled) setAgents({ phase: "error", message });
          return;
        }
        const data = (await res.json()) as { agents: BackendAgentInfo[] };
        if (cancelled) return;
        setAgents({ phase: "ready", agents: data.agents });
        const preferred =
          data.agents.find((a) => a.agent_id === "hook_generator_agent") ??
          data.agents[0];
        if (preferred) setAgentId(preferred.agent_id);
      } catch {
        if (!cancelled) {
          setAgents({
            phase: "error",
            message: "Could not reach the console gateway for the agent list.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const agent =
    agents.phase === "ready"
      ? agents.agents.find((a) => a.agent_id === agentId)
      : undefined;
  const fields = agent ? fieldsFor(agent.input_schema) : [];
  const pending = state.phase === "running";

  function selectAgent(nextId: string) {
    setAgentId(nextId);
    setValues({});
    setChecks({});
    setState({ phase: "idle" });
  }

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !agent) return;

    let input: Record<string, unknown>;
    try {
      input = buildInput(fields, values, checks);
    } catch (error) {
      setState({
        phase: "error",
        message: error instanceof Error ? error.message : "Invalid input.",
      });
      return;
    }

    setState({ phase: "running" });
    try {
      const res = await fetch("/api/console/agents/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: agent.agent_id, input }),
      });

      if (res.status === 201) {
        const approved = (await res.json()) as ConsoleExecuteApproved;
        setState({ phase: "done", outcome: { kind: "approved", response: approved } });
        // Load the persisted deliverable — the proof of execution.
        const detail = await fetch(
          `/api/console/deliverables/${approved.deliverable_id}`,
          { cache: "no-store" },
        );
        if (detail.ok) {
          onDeliverable((await detail.json()) as ConsoleDeliverable);
        }
        return;
      }
      if (res.status === 202) {
        const deferred = (await res.json()) as ConsoleExecuteDeferred;
        setState({ phase: "done", outcome: { kind: "deferred", response: deferred } });
        onDeferred();
        return;
      }
      const body = (await res.json().catch(() => null)) as {
        error?: string;
      } | null;
      const detailMessage = body?.error ?? `HTTP ${res.status}`;
      if (res.status === 403) {
        setState({
          phase: "done",
          outcome: { kind: "rejected", reason: detailMessage },
        });
        return;
      }
      if (res.status === 401) {
        setState({
          phase: "error",
          message: "Session expired — log out and sign in again.",
        });
        return;
      }
      setState({ phase: "error", message: detailMessage });
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
        <Eyebrow index="02">Governance · run an agent</Eyebrow>
        <h2
          id={`${baseId}-title`}
          className="mt-4 font-display text-xl font-semibold tracking-tight"
        >
          Every run passes the decision gate
        </h2>
        <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Pick an agent and submit real input. The decision engine evaluates
          the request before any model is called: it approves, defers to a
          human, or rejects — and the verdict below is the backend&apos;s,
          verbatim.
        </p>

        {agents.phase === "loading" ? (
          <p className="mt-6 font-mono text-xs tracking-wide text-muted-foreground">
            Loading agents…
          </p>
        ) : null}
        {agents.phase === "error" ? (
          <p role="alert" className="mt-6 font-mono text-xs tracking-wide text-blue">
            {agents.message}
          </p>
        ) : null}

        {agents.phase === "ready" ? (
          <form onSubmit={run} className="mt-6 space-y-5">
            <div className="space-y-2">
              <label
                htmlFor={`${baseId}-agent`}
                className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
              >
                Agent
              </label>
              <select
                id={`${baseId}-agent`}
                value={agentId}
                onChange={(e) => selectAgent(e.target.value)}
                disabled={pending}
                className={SELECT_CLASSES}
              >
                {agents.agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.name} · {a.department} · {a.authority_level}
                  </option>
                ))}
              </select>
              {agent ? (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {agent.description}
                </p>
              ) : null}
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              {fields.map((field) => (
                <div
                  key={`${agentId}-${field.name}`}
                  className={cn(
                    "space-y-2",
                    field.kind === "json" && "sm:col-span-2",
                  )}
                >
                  <label
                    htmlFor={`${baseId}-${field.name}`}
                    className="block font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase"
                  >
                    {field.name.replaceAll("_", " ")}
                    {field.required ? " *" : ""}
                  </label>
                  {field.kind === "boolean" ? (
                    <label className="flex h-10 items-center gap-3 text-sm">
                      <input
                        id={`${baseId}-${field.name}`}
                        type="checkbox"
                        checked={checks[field.name] ?? false}
                        onChange={(e) =>
                          setChecks((c) => ({ ...c, [field.name]: e.target.checked }))
                        }
                        disabled={pending}
                        className="size-4 accent-current"
                      />
                      <span className="text-muted-foreground">
                        {field.hint ?? field.name}
                      </span>
                    </label>
                  ) : field.kind === "enum" ? (
                    <select
                      id={`${baseId}-${field.name}`}
                      value={values[field.name] ?? ""}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [field.name]: e.target.value }))
                      }
                      disabled={pending}
                      className={SELECT_CLASSES}
                    >
                      <option value="">{field.required ? "choose…" : "(default)"}</option>
                      {(field.options ?? []).map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : field.kind === "json" ? (
                    <Textarea
                      id={`${baseId}-${field.name}`}
                      value={values[field.name] ?? ""}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [field.name]: e.target.value }))
                      }
                      placeholder={field.hint ?? "JSON value"}
                      disabled={pending}
                      rows={3}
                      className="font-mono text-xs"
                    />
                  ) : (
                    <Input
                      id={`${baseId}-${field.name}`}
                      value={values[field.name] ?? ""}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [field.name]: e.target.value }))
                      }
                      inputMode={
                        field.kind === "string" ? undefined : "numeric"
                      }
                      placeholder={field.hint}
                      disabled={pending}
                      className="h-10"
                    />
                  )}
                </div>
              ))}
            </div>

            <CtaButton type="submit" size="md" arrow disabled={pending}>
              {pending ? "Evaluating…" : "Submit through governance"}
            </CtaButton>
          </form>
        ) : null}
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
          <GovernanceOutcome outcome={state.outcome} />
        </div>
      ) : null}
    </section>
  );
}
