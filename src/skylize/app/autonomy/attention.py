"""Did this autonomous run hit a HITL-eligible boundary?

THE CORRECTION THIS MODULE ENCODES
----------------------------------
`work_journal.requires_attention` is NOT "the run finished". Every autonomous run
finishes; flagging them all would make the brief's `needs_attention` section
identical to `done_while_away` and therefore worthless -- the human would learn
to ignore both.

The right condition is: **the run reached a boundary this agent's own contract
says a human adjudicates.** That is a property of the contract plus the run's
outcome, not of the fact that a run happened.

WHY THIS IS NOT A HARDCODED `if agent_id == ...`
-------------------------------------------------
Every rule below names the `HumanInLoopTrigger` it evaluates, and
`evaluate_attention` only runs a rule whose trigger the resolving contract
ACTUALLY DECLARES in `human_in_loop_triggers`. So:

  * removing `SECURITY_SEVERITY_HIGH` from a contract silently stops that rule
    from firing -- no edit here is needed, and none can be forgotten;
  * a rule can never manufacture attention for a boundary its contract never
    claimed to have;
  * adding one of the other 22 agents means registering rules for ITS declared
    triggers; the dispatch above them does not change.

The contract is the source of truth for WHICH boundaries exist
(agent_governance.md 9, contracts/base.py:110-127). These rules only supply the
predicate for "has this one been reached", which a `HumanInLoopTrigger` enum
member cannot express on its own.

TERMINAL STATES THAT BYPASS THE RULES ENTIRELY
----------------------------------------------
A defer, a governance rejection, and an outright failure are attention-worthy on
their own terms and are decided by `runner.py`, not here -- they have no
validated output for a predicate to read. This module answers only the harder
question: the run SUCCEEDED; does a human still need to look?
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ...contracts.base import AgentContract, HumanInLoopTrigger

#: One predicate over a run's validated output model. Returns (fired, why).
#: `why` is recorded on the journal row so the human sees WHICH boundary, not
#: merely that one was hit.
AttentionRule = Callable[[Any], "tuple[bool, str] | None"]


# --------------------------------------------------------------------------- #
# Pilot rules: fraud_detection_agent
#
# Its output is `FraudVerdictOut{entity_id, outcome, confidence, reasons}`
# (schemas/agents/security.py:16-19) and its contract declares exactly two
# triggers (contracts/mvp/security.py:33-36). One rule per declared trigger.
# --------------------------------------------------------------------------- #

#: `outcome` is documented as 'allow' | 'reject' | 'review'
#: (schemas/agents/security.py:17). Only 'allow' is a clean pass; both other
#: values are the agent saying a human should decide. Compared case-folded
#: because the value is model-produced free text, not an enum -- the schema
#: types it `str`, so "Allow" must not read as a flag.
_FRAUD_CLEAR_OUTCOME = "allow"

#: Below this, the agent is not confident enough for its verdict to stand
#: unreviewed. 0.7 is a PILOT-SPECIFIC THRESHOLD and is flagged as such: it is
#: not derived from the contract, which carries no confidence field. It lives
#: here as a named constant rather than inline so the owner can move it in one
#: place, and so the next agent's rules do not inherit it by accident.
_FRAUD_LOW_CONFIDENCE = 0.7


def _fraud_outcome_not_clear(output: Any) -> tuple[bool, str] | None:
    """SECURITY_SEVERITY_HIGH: the agent flagged the entity rather than clearing it."""
    outcome = getattr(output, "outcome", None)
    if not isinstance(outcome, str):
        return None
    if outcome.strip().lower() == _FRAUD_CLEAR_OUTCOME:
        return None
    return True, f"fraud verdict outcome={outcome!r} is not {_FRAUD_CLEAR_OUTCOME!r}"


def _fraud_low_confidence(output: Any) -> tuple[bool, str] | None:
    """LOW_CONFIDENCE_IRREVERSIBLE: the verdict is too uncertain to stand alone."""
    confidence = getattr(output, "confidence", None)
    if not isinstance(confidence, (int, float)):
        return None
    if confidence >= _FRAUD_LOW_CONFIDENCE:
        return None
    return True, (
        f"fraud verdict confidence={confidence} is below {_FRAUD_LOW_CONFIDENCE}"
    )


# --------------------------------------------------------------------------- #
# brand_guardian_agent
#
# Its output is `BrandVerdictOut{brief_id, outcome, violations, confidence}`
# (schemas/agents/brand.py:22-26) and its contract declares exactly ONE trigger,
# BRAND_LEGAL_SENSITIVE (contracts/mvp/brand.py:32).
#
# ONE TRIGGER, THREE CONDITIONS -- WHY THE PREDICATE IS COMPOSITE. The pilot got
# one rule per trigger because it declares two. This contract declares one, so
# the choice is a composite predicate under BRAND_LEGAL_SENSITIVE or inventing
# triggers the contract never claimed. It is the former, because the contract is
# the source of truth for WHICH boundaries exist and a rule table must not add to
# it. Registering LOW_CONFIDENCE_IRREVERSIBLE here instead would be strictly
# worse: `evaluate_attention` only consults rules whose trigger the contract
# declares, so it would be DEAD CODE that never fires -- and it would read, to
# the next person, as calibrated coverage that does not exist.
#
# Each condition is genuinely a brand/legal judgement a human adjudicates, which
# is what the trigger means. Each contributes its own reason string, so the brief
# says WHICH one fired rather than merely that something did.
# --------------------------------------------------------------------------- #

#: `outcome` is documented as 'approve' | 'reject' (schemas/agents/brand.py:24).
#: Only 'approve' is a clean pass. Case-folded for the same reason as the pilot's:
#: the schema types it `str`, so "Approve" must not read as a flag.
_BRAND_CLEAR_OUTCOME = "approve"

#: Below this, the verdict is too uncertain to stand unreviewed.
#:
#: 0.85, NOT the pilot's 0.7, and the gap is argued rather than inherited:
#:
#:  * THE PILOT HAS A PRESSURE VALVE THIS AGENT DOES NOT. `FraudVerdictOut.outcome`
#:    is 'allow' | 'reject' | 'review' -- three values, one of which IS "a human
#:    should look". Its 0.7 therefore only governs how certain an `allow` must be,
#:    with `review` available whenever the model wants to hedge. `BrandVerdictOut`
#:    is BINARY: approve or reject, no middle. Confidence is the only channel this
#:    agent has for "I am not sure", so this threshold carries work the pilot's
#:    does not and has to sit higher to do it.
#:  * THE CONTRACT'S OWN POSTURE. `failure_mode=FAIL_CLOSED` (contracts/mvp/brand.py:29)
#:    -- when in doubt, stop. A threshold at or below the pilot's would let this
#:    agent's uncertainty pass more freely than a contract that fails closed says
#:    it should.
#:  * THE COSTS ARE NOT SYMMETRIC. A missed brand/legal violation ships in content
#:    that goes out to an audience and is slow and expensive to retract. An
#:    unnecessary flag costs one line in one human's brief. Where the error costs
#:    differ by that much, the threshold belongs on the cheap side.
#:
#: Named here, like the pilot's, so the owner moves it in one place -- and so the
#: NEXT agent does not inherit 0.85 by accident either.
_BRAND_LOW_CONFIDENCE = 0.85


def _brand_needs_a_human(output: Any) -> tuple[bool, str] | None:
    """BRAND_LEGAL_SENSITIVE: the verdict is a call a human should confirm."""
    reasons: list[str] = []

    outcome = getattr(output, "outcome", None)
    if isinstance(outcome, str) and outcome.strip().lower() != _BRAND_CLEAR_OUTCOME:
        reasons.append(f"outcome={outcome!r} is not {_BRAND_CLEAR_OUTCOME!r}")

    # Checked INDEPENDENTLY of `outcome`, which is the non-obvious one. An
    # `approve` that still lists violations is the agent saying "shippable, but
    # here is what is wrong with it" -- a named brand/legal defect. Letting that
    # land in `done_while_away` because the verdict was nominally clean would
    # file the agent's own findings where nobody reads them.
    violations = getattr(output, "violations", None)
    if isinstance(violations, (list, tuple)) and violations:
        shown = ", ".join(str(v) for v in violations[:3])
        more = f" (+{len(violations) - 3} more)" if len(violations) > 3 else ""
        reasons.append(f"{len(violations)} violation(s): {shown}{more}")

    confidence = getattr(output, "confidence", None)
    if isinstance(confidence, (int, float)) and confidence < _BRAND_LOW_CONFIDENCE:
        reasons.append(f"confidence={confidence} is below {_BRAND_LOW_CONFIDENCE}")

    if not reasons:
        return None
    return True, "; ".join(reasons)


#: agent_id -> {declared trigger: predicate}.
#:
#: SCOPED, DELIBERATELY. Only the agents in `pilot.PILOT_AGENT_IDS` have rules,
#: and `assert_pilot_agent` refuses to trigger one that does not. An agent absent
#: from this table, or a declared trigger with no rule, contributes no attention
#: signal -- `evaluate_attention` returns "no boundary reached" and the run lands
#: in `done_while_away`. That is the correct posture: the mechanism is generic,
#: the calibration is per-agent, and an uncalibrated agent must not be silently
#: treated as if it had been calibrated. Registering the other 21 means adding
#: entries here, not changing any logic -- which is exactly what adding the
#: second one required.
ATTENTION_RULES: Mapping[str, Mapping[HumanInLoopTrigger, AttentionRule]] = {
    "fraud_detection_agent": {
        HumanInLoopTrigger.SECURITY_SEVERITY_HIGH: _fraud_outcome_not_clear,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE: _fraud_low_confidence,
    },
    "brand_guardian_agent": {
        HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE: _brand_needs_a_human,
    },
}


def has_attention_rules(agent_id: str) -> bool:
    """Whether any boundary predicate is calibrated for this agent.

    The scheduler uses this to refuse to trigger an agent whose success path
    could never raise attention: that agent's runs would all read as clean, which
    is indistinguishable from "nothing was ever wrong" and is exactly the silent
    failure this pilot exists to rule out.
    """
    return bool(ATTENTION_RULES.get(agent_id))


def evaluate_attention(
    contract: AgentContract, output: Any
) -> tuple[bool, Sequence[str]]:
    """Did a SUCCESSFUL run reach a human-adjudicated boundary?

    Returns `(requires_attention, reasons)`. Only rules whose trigger appears in
    `contract.human_in_loop_triggers` are consulted, so the contract -- not this
    table -- decides which boundaries exist for the agent.
    """
    rules = ATTENTION_RULES.get(contract.agent_id)
    if not rules:
        return False, []

    declared = set(contract.human_in_loop_triggers)
    reasons: list[str] = []
    for trigger, rule in rules.items():
        if trigger not in declared:
            # The contract dropped this boundary; the rule is inert by design.
            continue
        verdict = rule(output)
        if verdict is None:
            continue
        fired, why = verdict
        if fired:
            reasons.append(f"{trigger.value}: {why}")
    return bool(reasons), reasons
