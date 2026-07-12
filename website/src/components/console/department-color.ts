/**
 * Canonical department color lookup, shared by every surface that tags a
 * department (network nodes, ActionCard tags, workflow diagrams, dashboard
 * tables). Colors live in the GENERATED palette (agent-network.data.ts,
 * regenerable via scripts/gen_agent_network_data.js) — never hardcode them
 * at a call site.
 */
import { DEPARTMENTS } from "./agent-network.data";

const colorById = new Map(DEPARTMENTS.map((d) => [d.id, d.color]));

export function departmentColor(departmentId: string): string {
  return colorById.get(departmentId) ?? "var(--color-border-strong)";
}

export { DEPARTMENTS };
