import { headers } from "next/headers";
import { AltitudeLine, Container, Eyebrow } from "@/components/skylize";
import { CreativeRunner } from "@/components/console/creative-runner";
import { KillSwitchPanel } from "@/components/console/kill-switch-panel";
import { cn } from "@/lib/utils";

/** View models for the BFF read endpoints — ours, kept deliberately small. */
interface HealthView {
  status: string;
  backend: string;
}

interface TenantView {
  org_id: string;
  display_name: string;
  status: string;
}

type Loaded<T> = { ok: true; data: T } | { ok: false; reason: string };

const OK_STATUSES = new Set(["ok", "healthy", "up", "active", "ready"]);

/**
 * Same-origin fetch against the BFF. The session cookie is forwarded from
 * the incoming request; the Railway URL and service key never appear here —
 * that hop belongs to the BFF alone.
 */
async function loadJson<T>(url: string, cookie: string): Promise<Loaded<T>> {
  try {
    const res = await fetch(url, {
      headers: cookie ? { cookie } : undefined,
      cache: "no-store",
    });
    if (res.status === 401) {
      return {
        ok: false,
        reason: "Unauthenticated — the session cookie is missing or expired.",
      };
    }
    if (!res.ok) {
      return { ok: false, reason: `Gateway responded HTTP ${res.status}.` };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return {
      ok: false,
      reason: "Gateway unreachable — BFF routes not mounted or backend down.",
    };
  }
}

function StatusDot({ status }: { status: string }) {
  const ok = OK_STATUSES.has(status.toLowerCase());
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 rounded-full",
        ok ? "bg-blue" : "border border-border-strong",
      )}
    />
  );
}

function PanelError({ reason }: { reason: string }) {
  return (
    <div className="mt-4 space-y-2">
      <p className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
        Offline
      </p>
      <p className="text-sm leading-relaxed text-muted-foreground">{reason}</p>
    </div>
  );
}

export default async function ConsolePage() {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost:3000";
  const proto = requestHeaders.get("x-forwarded-proto") ?? "http";
  const origin = `${proto}://${host}`;
  const cookie = requestHeaders.get("cookie") ?? "";

  const [health, tenant] = await Promise.all([
    loadJson<HealthView>(`${origin}/api/console/health`, cookie),
    loadJson<TenantView>(`${origin}/api/console/tenant`, cookie),
  ]);

  return (
    <Container wide className="flex-1 py-10 sm:py-14">
      <Eyebrow index="01">Operator console</Eyebrow>
      <h1 className="mt-4 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
        {tenant.ok ? tenant.data.display_name : "Skylize Console"}
      </h1>
      <p className="mt-2 font-mono text-xs tracking-[0.18em] text-muted-foreground uppercase">
        {tenant.ok ? `org · ${tenant.data.org_id}` : "Live operator surface"}
      </p>

      <div className="mt-10 grid gap-6 md:grid-cols-2">
        <section
          aria-label="System health"
          className="border border-border bg-card px-7 pt-6 pb-7"
        >
          <p className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
            Link · system health
          </p>
          {health.ok ? (
            <div className="mt-4 space-y-3">
              <p className="flex items-center gap-3 font-display text-2xl font-semibold tracking-tight capitalize">
                <StatusDot status={health.data.status} />
                {health.data.status}
              </p>
              <p className="font-mono text-xs tracking-wide text-muted-foreground">
                backend · {health.data.backend}
              </p>
            </div>
          ) : (
            <PanelError reason={health.reason} />
          )}
        </section>

        <section
          aria-label="Tenant identity"
          className="border border-border bg-card px-7 pt-6 pb-7"
        >
          <p className="font-mono text-xs tracking-[0.2em] text-muted-foreground uppercase">
            Tenant · identity
          </p>
          {tenant.ok ? (
            <div className="mt-4 space-y-3">
              <p className="flex items-center gap-3 font-display text-2xl font-semibold tracking-tight">
                <StatusDot status={tenant.data.status} />
                {tenant.data.display_name}
              </p>
              <p className="font-mono text-xs tracking-wide text-muted-foreground">
                {tenant.data.org_id} · {tenant.data.status}
              </p>
            </div>
          ) : (
            <PanelError reason={tenant.reason} />
          )}
        </section>
      </div>

      <AltitudeLine className="my-12" />

      <div className="space-y-8">
        <CreativeRunner />
        <KillSwitchPanel />
      </div>
    </Container>
  );
}
