import { describe, expect, it } from "vitest";

import { AGENTS } from "@/components/console/agent-network.data";
import { chainForDepartment, routeGoal } from "@/lib/workflow-build";

describe("routeGoal", () => {
  it("routes goals to the department whose keywords match", () => {
    expect(routeGoal("Launch an SEO campaign for the spring line")).toBe("marketing");
    expect(routeGoal("Reconcile vendor invoices against the ledger")).toBe("finance");
    expect(routeGoal("Triage support tickets and reduce churn")).toBe("customer_success");
  });

  it("matches case-insensitively", () => {
    expect(routeGoal("PREPARE THE BUDGET FORECAST")).toBe("finance");
  });

  it("falls back to operations when nothing matches", () => {
    expect(routeGoal("xyzzy")).toBe("operations");
  });
});

describe("chainForDepartment", () => {
  it("builds a ceo-rooted chain of agents from the department", () => {
    const chain = chainForDepartment("finance");
    expect(chain[0]).toBe("ceo");
    expect(chain).toContain("cfo");
    expect(chain).toContain("vp_finance");
    const byId = new Map(AGENTS.map((a) => [a.id, a]));
    for (const id of chain.slice(1)) {
      expect(byId.get(id)?.department).toBe("finance");
    }
  });

  it("never repeats an agent, even when the executive is the ceo", () => {
    for (const dept of ["executive_office", "finance", "marketing"]) {
      const chain = chainForDepartment(dept);
      expect(new Set(chain).size).toBe(chain.length);
    }
  });

  it("returns only the ceo for an unknown department", () => {
    expect(chainForDepartment("does_not_exist")).toEqual(["ceo"]);
  });
});
