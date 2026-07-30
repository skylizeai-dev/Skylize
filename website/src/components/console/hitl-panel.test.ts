import { describe, expect, it } from "vitest";

import { verdictFailureMessage } from "@/components/console/hitl-panel";

// A verdict-failure message may only assert a state transition the response
// actually establishes. These tests pin the two codes that do NOT establish one
// (409, 502) and the one whose meaning changed with the D4 backend fix (422).

describe("verdictFailureMessage", () => {
  describe("502 — the status cannot identify what happened", () => {
    // mapSkylizeError (lib/skylize/handler.ts) returns 502 for a backend
    // 401/403 (the server's SERVICE CREDENTIAL was rejected) and for any
    // backend 5xx; skylizeFetch synthesises 502 for an unreachable backend and
    // for a malformed body. Only the backend's own HitlExecutionFailed 502
    // means the item was claimed and released.
    const CREDENTIAL_REJECTED = "The backend rejected the server's service credential.";
    const EXECUTION_FAILED = "execution failed: provider unavailable";

    it("never asserts that the item was claimed or released", () => {
      for (const detail of [CREDENTIAL_REJECTED, EXECUTION_FAILED, "Backend unreachable."]) {
        const message = verdictFailureMessage(502, detail);
        expect(message).not.toMatch(/^Execution failed after approval/);
        expect(message).not.toMatch(/The item was returned to pending/);
      }
    });

    it("states the uncertainty and defers to the re-read status", () => {
      const message = verdictFailureMessage(502, CREDENTIAL_REJECTED);
      expect(message).toContain("cannot say whether the item was claimed");
      expect(message).toContain("go by the status shown");
    });

    it("still surfaces the backend's own detail verbatim", () => {
      expect(verdictFailureMessage(502, EXECUTION_FAILED)).toContain(EXECUTION_FAILED);
      expect(verdictFailureMessage(502, CREDENTIAL_REJECTED)).toContain(
        CREDENTIAL_REJECTED,
      );
    });

    it("keeps retry advice conditional, not imperative", () => {
      const message = verdictFailureMessage(502, EXECUTION_FAILED);
      // "if execution failed after the claim ... approving again will retry it"
      expect(message).toContain("if execution failed after the claim");
      expect(message).not.toMatch(/approve again to retry\./);
    });
  });

  describe("422 — HitlReplayInvalid, which now TERMINATES the row (D4)", () => {
    const DETAIL = "replay invalid: 1 validation error for HookGeneratorInput";

    it("says the row was moved to 'expired', not that it stays pending", () => {
      const message = verdictFailureMessage(422, DETAIL);
      expect(message).toContain("moved to 'expired'");
      expect(message).toContain("cannot be approved again");
      expect(message).not.toContain("the item stays pending");
    });

    it("still states that nothing executed", () => {
      expect(verdictFailureMessage(422, DETAIL)).toContain("nothing was executed");
    });
  });

  describe("409 — three different refusals share this code", () => {
    // HitlAlreadyActioned, plus HitlNotReplayable from either a row with no
    // request_json or a stored envelope that failed validation.
    it("does not claim 'already actioned' for every 409", () => {
      const notReplayable =
        "hitl item 0d1e… carries no replayable request_json";
      expect(verdictFailureMessage(409, notReplayable)).not.toMatch(
        /^Already actioned/,
      );
    });

    it("carries the backend detail, which is what identifies the case", () => {
      const actioned = "hitl item already actioned: status=rejected";
      expect(verdictFailureMessage(409, actioned)).toContain(actioned);
    });
  });

  describe("codes whose transition IS established", () => {
    it("410 still says the verdict was refused and nothing ran", () => {
      const message = verdictFailureMessage(410, "hitl item expired at …");
      expect(message).toContain("Expired");
      expect(message).toContain("Nothing was executed");
    });

    it("401 is a console session problem, not a backend verdict", () => {
      expect(verdictFailureMessage(401, null)).toBe(
        "Session expired — log out and sign in again.",
      );
    });
  });

  it("falls back to the status when the envelope carried no detail", () => {
    expect(verdictFailureMessage(418, null)).toBe("HTTP 418");
  });
});
