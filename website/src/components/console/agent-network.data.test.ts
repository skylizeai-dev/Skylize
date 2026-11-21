import { describe, expect, it } from "vitest";

import { AGENTS, DEPARTMENTS } from "@/components/console/agent-network.data";

// Owner ruling OD6 (proposal): an agent with no AgentContract is "planned";
// one with a registered contract is "governed". The roster derives this from
// `budgetSource` in scripts/gen_agent_network_data.js -- these tests pin that
// the two can never disagree.
describe("agent roster lifecycle", () => {
  it("gives every node a lifecycle from the roster vocabulary", () => {
    for (const agent of AGENTS) {
      expect(["governed", "planned"]).toContain(agent.lifecycle);
    }
  });

  it("marks exactly the nodes without a contract as planned", () => {
    const planned = AGENTS.filter((a) => a.lifecycle === "planned");
    const withoutContract = AGENTS.filter((a) => a.budgetSource !== "contract");
    expect(planned.map((a) => a.id)).toEqual(withoutContract.map((a) => a.id));
  });

  it("derives the governed count from contract-backed nodes", () => {
    const governed = AGENTS.filter((a) => a.lifecycle === "governed").length;
    const contractBacked = AGENTS.filter((a) => a.budgetSource === "contract").length;
    expect(governed).toBe(contractBacked);
    expect(governed).toBeGreaterThan(0);
    expect(governed + AGENTS.filter((a) => a.lifecycle === "planned").length).toBe(
      AGENTS.length,
    );
  });

  it("places every node in a known department", () => {
    const ids = new Set(DEPARTMENTS.map((d) => d.id));
    for (const agent of AGENTS) {
      expect(ids.has(agent.department)).toBe(true);
    }
  });
});
