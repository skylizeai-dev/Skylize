/**
 * Pure routing helpers for the governed-workflow story.
 *
 * routeGoal maps a business goal onto a department by keyword;
 * chainForDepartment derives the CEO → executive → VP → director →
 * worker/manager delegation chain from the canonical generated data.
 * Consumers: the marketing hero demo (hero-workflow-demo.tsx) and
 * WorkflowBuildDial's StageStatus type. This module is intentionally
 * side-effect-free — no fetch, no store.
 */

import { AGENTS } from "@/components/console/agent-network.data";

export type StageStatus = "pending" | "active" | "complete" | "error";

const DEPT_KEYWORDS: Record<string, string[]> = {
  marketing: ["marketing", "campaign", "seo", "email", "newsletter", "brand awareness", "growth", "ads"],
  creative: ["hook", "copy", "creative", "design", "caption", "script", "video", "content"],
  finance: ["invoice", "budget", "expense", "revenue", "forecast", "payment", "reconcil"],
  sales: ["lead", "deal", "pipeline", "crm", "prospect", "outreach", "quota"],
  customer_success: ["ticket", "support", "customer", "churn", "onboarding", "csat", "retention"],
  operations: ["task", "overdue", "summary", "report", "daily", "weekly", "reminder", "schedule", "sync", "logistics", "fulfillment"],
  engineering: ["deploy", "api", "bug", "infra", "backend", "frontend", "ci", "devops"],
  data: ["analytics", "dashboard", "metric", "data", "bi ", "ml", "model"],
  security: ["security", "audit", "compliance", "access", "vulnerab", "incident"],
  legal: ["contract", "privacy", "gdpr", "legal", "terms"],
  people: ["performance review", "training", "hiring", "playbook"],
  procurement: ["vendor", "sourcing", "purchase", "procurement", "supplier"],
  product: ["feature", "roadmap", "experiment", "user research", "product"],
  strategy: ["competitor", "market", "expansion", "m&a", "strategy"],
};

export function routeGoal(goal: string): string {
  const g = goal.toLowerCase();
  for (const [dept, keys] of Object.entries(DEPT_KEYWORDS)) {
    if (keys.some((k) => g.includes(k))) return dept;
  }
  return "operations";
}

/** CEO → dept executive → VP → Director → worker/manager, from canonical data. */
export function chainForDepartment(deptId: string): string[] {
  const members = AGENTS.filter((a) => a.department === deptId);
  const pick = (lvl: string) => members.find((a) => a.authority === lvl)?.id;
  const chain = [
    "ceo",
    pick("executive"),
    pick("vp"),
    pick("director"),
    pick("worker") ?? pick("manager"),
  ].filter((id): id is string => Boolean(id));
  // de-dupe (executive_office's exec IS the ceo)
  return Array.from(new Set(chain));
}
