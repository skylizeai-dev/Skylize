import { describe, expect, it } from "vitest";

import { refusalFor } from "@/components/console/agent-runner";

// POST /api/console/agents/execute answers 403 for THREE different causes. Until
// the backend carried a machine-readable `code` (src/skylize/edge/errors.py) all
// three arrived as a bare message and were rendered identically, as a decision
// engine REJECTED verdict — so a console whose SERVICE CREDENTIAL lacked the
// required role showed the operator a governance verdict for what is actually a
// configuration fault. These tests pin that the three now render distinctly and
// that each says something true about what happened.

const DECISION_MESSAGE = "decision rejected: spend over ceiling";
const DENIED_MESSAGE = "governance denied: tenant kill switch engaged";
const SUSPENDED_MESSAGE = "governance denied: agent suspended (circuit breaker)";
const ROLE_MESSAGE = "requires one of roles: ['admin', 'operator', 'owner']";

describe("refusalFor", () => {
  it("renders a decision rejection as the governance verdict it is", () => {
    const refusal = refusalFor("decision_rejected", DECISION_MESSAGE);
    expect(refusal).toEqual({
      render: "verdict",
      outcome: { kind: "rejected", reason: DECISION_MESSAGE },
    });
  });

  it("renders a platform denial as a control, not as a verdict", () => {
    const refusal = refusalFor("governance_denied", DENIED_MESSAGE);
    expect(refusal.render).toBe("verdict");
    if (refusal.render !== "verdict") throw new Error("unreachable");
    // A distinct outcome kind: the strip headlines it as a platform control and
    // states the proposal was never evaluated.
    expect(refusal.outcome.kind).toBe("blocked");
  });

  it("names the control that blocked it — kill switch or suspension", () => {
    const killed = refusalFor("governance_denied", DENIED_MESSAGE);
    const suspended = refusalFor("governance_denied", SUSPENDED_MESSAGE);
    if (killed.render !== "verdict" || suspended.render !== "verdict") {
      throw new Error("unreachable");
    }
    if (killed.outcome.kind !== "blocked" || suspended.outcome.kind !== "blocked") {
      throw new Error("unreachable");
    }
    // The backend's own reason names WHICH control, and it is carried verbatim.
    expect(killed.outcome.reason).toContain("kill switch");
    expect(suspended.outcome.reason).toContain("suspended");
    expect(killed.outcome.reason).not.toEqual(suspended.outcome.reason);
  });

  it("names an authorization failure as THIS console's misconfiguration", () => {
    const refusal = refusalFor("authorization_failed", ROLE_MESSAGE);
    // Not a verdict at all — it never reached governance.
    expect(refusal.render).toBe("error");
    if (refusal.render !== "error") throw new Error("unreachable");
    expect(refusal.message).toContain("service credential");
    expect(refusal.message).toContain("Nothing was submitted to governance");
    // It must NOT blame the request the operator submitted.
    expect(refusal.message).not.toMatch(/rejected|REJECTED/);
    // The backend's own message is still carried for the operator.
    expect(refusal.message).toContain(ROLE_MESSAGE);
  });

  it("renders the three causes distinctly", () => {
    const rendered = [
      refusalFor("decision_rejected", DECISION_MESSAGE),
      refusalFor("governance_denied", DENIED_MESSAGE),
      refusalFor("authorization_failed", ROLE_MESSAGE),
    ].map((r) =>
      r.render === "error" ? `error:${r.message}` : `verdict:${r.outcome.kind}`,
    );
    expect(new Set(rendered).size).toBe(3);
  });

  it("keeps the pre-code rendering for a 403 that carries no code", () => {
    // An older backend, or a proxy that stripped the key: the cause cannot be
    // attributed, so it must not be guessed at.
    for (const code of [undefined, null, "", "something_new", 7]) {
      expect(refusalFor(code, DECISION_MESSAGE)).toEqual({
        render: "verdict",
        outcome: { kind: "rejected", reason: DECISION_MESSAGE },
      });
    }
  });
});
