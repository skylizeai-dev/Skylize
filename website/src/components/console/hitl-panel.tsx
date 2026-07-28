"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { AltitudeLine, Eyebrow } from "@/components/skylize";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  ConsoleDeliverable,
  ConsoleHitlApprove,
  ConsoleHitlItem,
  ConsoleHitlList,
} from "@/lib/skylize/types";

type ListState =
  | { phase: "loading" }
  | { phase: "ready"; items: ConsoleHitlItem[]; total: number }
  | { phase: "error"; message: string };

interface ActionState {
  working: "approve" | "reject" | null;
  /** Verdict failure surfaced on the item itself — distinct per status code. */
  message: string | null;
}

/** Map a verdict failure to an honest, code-specific message. The `error`
 *  string is the backend's own detail, passed through the BFF envelope. */
function verdictFailureMessage(status: number, error: string | null): string {
  const detail = error ?? `HTTP ${status}`;
  if (status === 409) return `Already actioned — ${detail}`;
  if (status === 410)
    return `Expired — the backend refused the verdict (${detail}). Nothing was executed.`;
  if (status === 422)
    return `${detail} The stored input no longer passes the agent's current schema; the item stays pending and nothing was executed.`;
  if (status === 502)
    return `Execution failed after approval — ${detail} The item was returned to pending; approve again to retry.`;
  if (status === 401) return "Session expired — log out and sign in again.";
  return detail;
}

/** The backend does not sweep time-expired rows, so expiry is judged
 *  client-side against the row's own expires_at (brief item 12). */
function isExpired(item: ConsoleHitlItem): boolean {
  return item.expires_at !== null && Date.parse(item.expires_at) <= Date.now();
}

export function HitlPanel({
  version,
  onDeliverable,
}: {
  /** Bumped by the runner when a submission defers — triggers a reload. */
  version: number;
  onDeliverable: (deliverable: ConsoleDeliverable, hitlId: string) => void;
}) {
  const baseId = useId();
  const [list, setList] = useState<ListState>({ phase: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [actions, setActions] = useState<Record<string, ActionState>>({});

  // Pure fetch — no state writes in here, so the mount/version effect below
  // stays a plain subscribe-to-external-system effect.
  const fetchList = useCallback(async (): Promise<ListState> => {
    try {
      const res = await fetch("/api/console/hitl?limit=50", { cache: "no-store" });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { error?: string } | null;
        return {
          phase: "error",
          message:
            res.status === 401
              ? "Session expired — log out and sign in again."
              : (body?.error ?? `Could not load pending approvals (HTTP ${res.status}).`),
        };
      }
      const data = (await res.json()) as ConsoleHitlList;
      return { phase: "ready", items: data.data, total: data.pagination.total };
    } catch {
      return {
        phase: "error",
        message: "Could not reach the console gateway for pending approvals.",
      };
    }
  }, []);

  const load = useCallback(async () => {
    setList(await fetchList());
  }, [fetchList]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = await fetchList();
      if (!cancelled) setList(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchList, version]);

  function setAction(hitlId: string, next: ActionState) {
    setActions((a) => ({ ...a, [hitlId]: next }));
  }

  async function verdict(item: ConsoleHitlItem, kind: "approve" | "reject") {
    const hitlId = item.hitl_id;
    setAction(hitlId, { working: kind, message: null });
    try {
      const res = await fetch(`/api/console/hitl/${hitlId}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        if (kind === "approve") {
          const approved = (await res.json()) as ConsoleHitlApprove;
          const detail = await fetch(
            `/api/console/deliverables/${approved.deliverable_id}`,
            { cache: "no-store" },
          );
          if (detail.ok) {
            onDeliverable((await detail.json()) as ConsoleDeliverable, hitlId);
          }
          setAction(hitlId, {
            working: null,
            message: null,
          });
        } else {
          setAction(hitlId, { working: null, message: null });
        }
        await load(); // the row's status changed on the backend — re-read it
        return;
      }
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      setAction(hitlId, {
        working: null,
        message: verdictFailureMessage(res.status, body?.error ?? null),
      });
      // 409/410 mean the row's real state differs from what we showed — re-read.
      if (res.status === 409 || res.status === 410) await load();
    } catch {
      setAction(hitlId, {
        working: null,
        message: "Could not reach the console gateway — the verdict was not recorded.",
      });
    }
  }

  return (
    <section
      aria-labelledby={`${baseId}-title`}
      className="border border-border bg-card"
    >
      <div className="px-7 pt-7 pb-6 sm:px-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Eyebrow index="03">Governance · pending approvals</Eyebrow>
            <h2
              id={`${baseId}-title`}
              className="mt-4 font-display text-xl font-semibold tracking-tight"
            >
              A human closes the loop
            </h2>
            <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Requests the decision engine deferred wait here. Nothing below
              has executed — approving an item is what runs it; rejecting it
              records the verdict and runs nothing.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setRefreshing(true);
              void load().finally(() => setRefreshing(false));
            }}
            disabled={refreshing || list.phase === "loading"}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {list.phase === "loading" ? (
          <p className="mt-6 font-mono text-xs tracking-wide text-muted-foreground">
            Loading pending approvals…
          </p>
        ) : null}
        {list.phase === "error" ? (
          <p role="alert" className="mt-6 font-mono text-xs tracking-wide text-blue">
            {list.message}
          </p>
        ) : null}

        {list.phase === "ready" ? (
          list.items.length === 0 ? (
            <p className="mt-6 text-sm text-muted-foreground">
              No pending approvals — every deferred request has been decided.
            </p>
          ) : (
            <>
              <p className="mt-6 font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
                {list.total} pending
              </p>
              <ul className="mt-4 space-y-5">
                {list.items.map((item) => {
                  const action = actions[item.hitl_id] ?? {
                    working: null,
                    message: null,
                  };
                  const expired = isExpired(item);
                  return (
                    <li
                      key={item.hitl_id}
                      className={cn(
                        "border px-5 py-4",
                        expired ? "border-border/60 opacity-75" : "border-border",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                        <span className="font-display text-base font-semibold tracking-tight">
                          {item.agent_id ?? "unknown agent"}
                        </span>
                        <span className="border border-blue/40 px-2 py-0.5 font-mono text-[0.65rem] tracking-[0.15em] text-blue uppercase">
                          trigger · {item.trigger_reason}
                        </span>
                        {expired ? (
                          <span className="border border-border-strong px-2 py-0.5 font-mono text-[0.65rem] tracking-[0.15em] text-muted-foreground uppercase">
                            expired
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-2 font-mono text-xs tracking-wide text-muted-foreground">
                        submitted · {new Date(item.created_at).toLocaleString()}
                        {item.expires_at
                          ? ` · expires · ${new Date(item.expires_at).toLocaleString()}`
                          : ""}
                      </p>
                      <p className="mt-1 font-mono text-xs tracking-wide text-muted-foreground">
                        hitl · {item.hitl_id}
                      </p>

                      {item.request_input ? (
                        <div className="mt-3 border border-border/70 bg-background/40 px-4 py-3">
                          <p className="font-mono text-[0.65rem] tracking-[0.2em] text-muted-foreground uppercase">
                            requested input
                          </p>
                          <pre className="mt-2 max-h-48 overflow-auto font-mono text-xs leading-relaxed whitespace-pre-wrap">
                            {JSON.stringify(item.request_input, null, 2)}
                          </pre>
                        </div>
                      ) : (
                        <p className="mt-3 font-mono text-xs tracking-wide text-muted-foreground">
                          no replayable request stored — approval cannot execute this row
                        </p>
                      )}

                      <div className="mt-4 flex flex-wrap items-center gap-3">
                        <Button
                          onClick={() => void verdict(item, "approve")}
                          disabled={action.working !== null}
                        >
                          {action.working === "approve"
                            ? "Approving · executing…"
                            : "Approve & execute"}
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => void verdict(item, "reject")}
                          disabled={action.working !== null}
                        >
                          {action.working === "reject" ? "Rejecting…" : "Reject"}
                        </Button>
                      </div>
                      {action.message ? (
                        <p
                          role="alert"
                          className="mt-3 font-mono text-xs tracking-wide text-blue"
                        >
                          {action.message}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          )
        ) : null}
      </div>

      <div className="px-7 pb-7 sm:px-8">
        <AltitudeLine className="mb-4" />
        <p className="font-mono text-xs tracking-wide text-muted-foreground">
          Verdicts are recorded with the acting principal and are idempotent —
          a second decision on the same item is refused by the backend, never
          re-executed.
        </p>
      </div>
    </section>
  );
}
