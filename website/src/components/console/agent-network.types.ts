/* ──────────────────────────────────────────────
   AGENT NETWORK — shared data-model types.
   Imported by both the generated data module
   (agent-network.data.ts) and the component.
────────────────────────────────────────────── */

export type AuthorityLevel = "executive" | "vp" | "director" | "manager" | "worker";
export type NodeStatus = "executing" | "idle" | "queued" | "error";

export interface ToolNode {
  id: string;
  name: string;
  purpose: string;
}

export interface AgentNode {
  id: string;
  name: string;
  role: string;
  authority: AuthorityLevel;
  department: string;
  status: NodeStatus;
  tokenBudget: number;
  tokensUsed: number;
  tasksCompleted: number;
  /** Org reporting line. `null` only for the single root (CEO). */
  reportsTo: string | null;
  /** Governance escalation chain, terminating at `human_owner`. */
  escalationPath: string[];
  tools: ToolNode[];
}

export interface Department {
  id: string;
  name: string;
  tagline: string;
  color: string;
}
