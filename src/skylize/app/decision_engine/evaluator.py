"""
The six-stage deterministic evaluator (decision_engine.md §4).

`DecisionEvaluator.evaluate` runs a proposal through, in order:

  1. authority_check     — agent authority_level vs the action's required level
  2. policy_check        — inline guardrail rules (the MVP stand-in for OPA Rego)
  3. score               — deterministic 0–100 decision score
  4. capital_check       — spend against the org/department budget ceiling
  5. conflict_detection  — competing proposals on the same partition_key
  6. hitl_check          — human-in-the-loop triggers from the agent contract

The first stage that produces a terminal outcome short-circuits the rest
(most-restrictive-wins). There are NO LLM calls and NO I/O beyond the injected
capital ledger read — given the same inputs the verdict is always identical,
which is what makes a decision replayable and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ...contracts.base import AgentContract, AuthorityLevel, HumanInLoopTrigger
from ...contracts.registry import AgentNotRegistered, AgentRegistry
from ...dal.ports import BudgetCeiling, CapitalRepository
from .events import (
    KNOWN_ACTION_KINDS,
    Conflict,
    DecisionProposal,
    DecisionResult,
    DecisionScore,
    decision_id_for,
)

POLICY_VERSION = "mvp-inline-1.0"

# Authority ordering (agent_governance.md §2); higher rank == more authority.
_RANK: dict[AuthorityLevel, int] = {
    "worker": 1,
    "manager": 2,
    "director": 3,
    "vp": 4,
    "executive": 5,
}

# Stage names (mirrored into the emitted DecisionEvaluated record).
STAGE_AUTHORITY = "authority_check"
STAGE_POLICY = "opa_policy"
STAGE_SCORING = "scoring"
STAGE_CAPITAL = "capital_allocation"
STAGE_CONFLICT = "conflict_resolution"
STAGE_HITL = "hitl_gate"


@dataclass(frozen=True, slots=True)
class _ProposalRecord:
    """A proposal the evaluator has already cleared into the active window —
    the incumbent that later proposals on the same partition collide with."""

    proposal_id: UUID
    agent_id: str
    authority_level: AuthorityLevel
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class _Stage:
    """Outcome of one stage. `terminal` short-circuits the pipeline."""

    terminal: bool = False
    outcome: str | None = None  # 'rejected' | 'deferred_to_human'
    reasons: list[str] = field(default_factory=list)
    hitl_trigger: str | None = None
    routed_to: str | None = None
    conflicts: list[Conflict] = field(default_factory=list)


_PASS = _Stage()


class DecisionEvaluator:
    def __init__(self, *, registry: AgentRegistry, capital: CapitalRepository) -> None:
        self._registry = registry
        self._capital = capital
        # Active proposals per (org, partition_key) for conflict detection. An
        # MVP single-process window; at Scale this is a short-TTL shared store.
        self._recent: dict[tuple[str, str], list[_ProposalRecord]] = {}

    # -- public API ---------------------------------------------------------
    async def evaluate(self, proposal: DecisionProposal) -> DecisionResult:
        stages: list[str] = []

        # Resolve the proposing agent's contract (fail closed on unknown agent).
        try:
            contract = self._registry.resolve(proposal.proposing_agent_id)
        except AgentNotRegistered as exc:
            stages.append(STAGE_AUTHORITY)
            return self._reject(proposal, None, stages, STAGE_AUTHORITY, [str(exc)])

        # 1. authority --------------------------------------------------------
        stages.append(STAGE_AUTHORITY)
        s = self.authority_check(proposal, contract)
        if s.terminal:
            return self._terminate(proposal, contract, stages, STAGE_AUTHORITY, s)

        # 2. policy -----------------------------------------------------------
        stages.append(STAGE_POLICY)
        s = self.policy_check(proposal, contract)
        if s.terminal:
            return self._terminate(proposal, contract, stages, STAGE_POLICY, s)

        # 3. scoring (deterministic; never terminal, recorded for ranking) ----
        stages.append(STAGE_SCORING)
        ceiling = (
            await self._capital.get_ceiling(proposal.org_id, proposal.capital_scope)
            if proposal.involves_spend
            else None
        )
        score = self.score(proposal, contract, ceiling)

        # 4. capital ----------------------------------------------------------
        stages.append(STAGE_CAPITAL)
        s = self.capital_check(proposal, ceiling)
        if s.terminal:
            return self._terminate(proposal, contract, stages, STAGE_CAPITAL, s, score)

        # 5. conflict resolution ---------------------------------------------
        stages.append(STAGE_CONFLICT)
        s = self.conflict_detection(proposal, contract)
        # Whether it won, lost, or has no rival, the proposal now joins the
        # active window so the NEXT proposal on this partition sees it.
        self._remember(proposal, contract)
        if s.terminal:
            return self._terminate(proposal, contract, stages, STAGE_CONFLICT, s, score)

        # 6. HITL gate --------------------------------------------------------
        stages.append(STAGE_HITL)
        h = self.hitl_check(proposal, contract)
        if h.terminal:
            return self._terminate(proposal, contract, stages, STAGE_HITL, h, score)

        # All six passed → approved.
        return DecisionResult(
            proposal_id=proposal.proposal_id,
            decision_id=decision_id_for(proposal.proposal_id),
            proposing_agent=proposal.proposing_agent_id,
            action_kind=proposal.action_kind,
            outcome="approved",
            stages_completed=stages,
            score=score,
            conflicts=s.conflicts,  # any conflict the proposal *won*
            policy_version=POLICY_VERSION,
            authority_level=contract.authority_level,
        )

    # -- stage 1 ------------------------------------------------------------
    def authority_check(self, proposal: DecisionProposal, contract: AgentContract) -> _Stage:
        """An external launch demands director+; a worker that proposes one has
        exceeded its authority → defer up the escalation path (not a flat reject;
        decision_engine.md §4.1)."""
        required: AuthorityLevel = "director" if proposal.requires_external_launch else "worker"
        if _RANK[contract.authority_level] < _RANK[required]:
            return _Stage(
                terminal=True,
                outcome="deferred_to_human",
                reasons=[
                    f"authority_exceeded: {contract.authority_level} cannot perform "
                    f"{proposal.action_kind} (requires {required}+)"
                ],
                hitl_trigger=HumanInLoopTrigger.AUTHORITY_EXCEEDED.value,
                routed_to=_escalate_to(contract),
            )
        return _PASS

    # -- stage 2 ------------------------------------------------------------
    def policy_check(self, proposal: DecisionProposal, contract: AgentContract) -> _Stage:
        """Inline guardrail rules — the MVP stand-in for OPA Rego (guardrails.md).
        Any violated rule denies the proposal (rejected) naming the rule."""
        reasons: list[str] = []

        if proposal.action_kind not in KNOWN_ACTION_KINDS:
            # An unknown action class is never guessed (decision_engine.md §6).
            reasons.append(f"unknown_action_class: {proposal.action_kind}")
            return _Stage(terminal=True, outcome="rejected", reasons=reasons)

        if proposal.involves_spend:
            amount = proposal.spend_minor_units
            if amount is None or amount <= 0:
                reasons.append("invalid_spend_amount: spend must be a positive amount")
            elif _RANK[contract.authority_level] < _RANK["director"]:
                reasons.append("spend_requires_director: spend actions require director+")

        if proposal.metadata.get("brand_safety") == "blocked":
            reasons.append("brand_sensitive_requires_legal_review: blocked by brand safety")

        if reasons:
            return _Stage(terminal=True, outcome="rejected", reasons=reasons)
        return _PASS

    # -- stage 3 ------------------------------------------------------------
    def score(
        self,
        proposal: DecisionProposal,
        contract: AgentContract,
        ceiling: BudgetCeiling | None,
    ) -> DecisionScore:
        """Deterministic 0–100 score: authority weight + policy pass + budget
        headroom. Same inputs → same score, always."""
        authority = _RANK[contract.authority_level] * 8.0  # 8..40
        policy = 30.0  # reaching stage 3 means policy passed

        if proposal.involves_spend and ceiling is not None and ceiling.ceiling_minor_units > 0:
            projected = ceiling.committed_minor_units + (proposal.spend_minor_units or 0)
            utilization = projected / ceiling.ceiling_minor_units
            budget = max(0.0, 1.0 - utilization) * 30.0
        else:
            budget = 30.0  # no spend (or no ceiling to weigh) → full headroom credit

        value = max(0, min(100, round(authority + policy + budget)))
        return DecisionScore(
            value=value,
            components={
                "authority_weight": round(authority, 2),
                "policy_pass": policy,
                "budget_headroom": round(budget, 2),
            },
            rationale=(
                f"authority={contract.authority_level} policy=pass "
                f"spend={'yes' if proposal.involves_spend else 'no'}"
            ),
        )

    # -- stage 4 ------------------------------------------------------------
    def capital_check(self, proposal: DecisionProposal, ceiling: BudgetCeiling | None) -> _Stage:
        """Spend over the ceiling defers to a human (HITL SPEND_OVER_CEILING).
        No spend → pass. No ceiling configured → fail closed (also defer)."""
        if not proposal.involves_spend:
            return _PASS
        if ceiling is None:
            return _Stage(
                terminal=True,
                outcome="deferred_to_human",
                reasons=[f"no_budget_ceiling_configured: scope={proposal.capital_scope}"],
                hitl_trigger=HumanInLoopTrigger.SPEND_OVER_CEILING.value,
                routed_to="human_owner",
            )
        projected = ceiling.committed_minor_units + (proposal.spend_minor_units or 0)
        if projected > ceiling.ceiling_minor_units:
            return _Stage(
                terminal=True,
                outcome="deferred_to_human",
                reasons=[
                    f"spend_over_ceiling: {projected} > {ceiling.ceiling_minor_units} "
                    f"({proposal.currency or 'minor_units'}) for scope={proposal.capital_scope}"
                ],
                hitl_trigger=HumanInLoopTrigger.SPEND_OVER_CEILING.value,
                routed_to="human_owner",
            )
        return _PASS

    # -- stage 5 ------------------------------------------------------------
    def conflict_detection(self, proposal: DecisionProposal, contract: AgentContract) -> _Stage:
        """Detect a competing proposal on the same partition_key + correlation
        from a *different* agent, then resolve deterministically:
        authority → recency → escalate to a human if unresolvable."""
        window = self._recent.get((proposal.org_id, proposal.partition_key), [])
        rivals = [r for r in window if r.agent_id != proposal.proposing_agent_id]
        if not rivals:
            return _PASS

        # Strongest incumbent: highest authority, earliest arrival on a tie.
        incumbent = min(rivals, key=lambda r: (-_RANK[r.authority_level], r.occurred_at))
        winner_id, rule = self._resolve(proposal, contract, incumbent)
        conflict = Conflict(
            partition_key=proposal.partition_key,
            proposal_ids=[incumbent.proposal_id, proposal.proposal_id],
            rule_applied=rule,
            winning_proposal_id=winner_id,
        )

        if rule == "escalated":
            return _Stage(
                terminal=True,
                outcome="deferred_to_human",
                reasons=["conflict_unresolvable: equal authority and recency"],
                hitl_trigger=HumanInLoopTrigger.AUTHORITY_EXCEEDED.value,
                routed_to=_escalate_to(contract),
                conflicts=[conflict],
            )
        if winner_id != proposal.proposal_id:
            return _Stage(
                terminal=True,
                outcome="rejected",
                reasons=[f"conflict_lost: superseded on {proposal.partition_key} via {rule}"],
                conflicts=[conflict],
            )
        # The proposal won — carry the conflict record forward, keep evaluating.
        return _Stage(conflicts=[conflict])

    def _resolve(
        self,
        proposal: DecisionProposal,
        contract: AgentContract,
        incumbent: _ProposalRecord,
    ) -> tuple[UUID | None, str]:
        cr, ir = _RANK[contract.authority_level], _RANK[incumbent.authority_level]
        if cr != ir:  # 1. authority — higher authority wins
            winner = proposal.proposal_id if cr > ir else incumbent.proposal_id
            return winner, "authority"
        if proposal.occurred_at != incumbent.occurred_at:  # 2. recency — newer wins
            winner = (
                proposal.proposal_id
                if proposal.occurred_at > incumbent.occurred_at
                else incumbent.proposal_id
            )
            return winner, "recency"
        return None, "escalated"  # 3. unresolvable → human

    # -- stage 6 ------------------------------------------------------------
    def hitl_check(self, proposal: DecisionProposal, contract: AgentContract) -> _Stage:
        """Defer if any of the agent contract's human_in_loop_triggers match the
        proposal. SPEND_OVER_CEILING / AUTHORITY_EXCEEDED are owned by earlier
        stages, so they are not re-matched here."""
        for trigger in contract.human_in_loop_triggers:
            if self._trigger_matches(trigger, proposal):
                return _Stage(
                    terminal=True,
                    outcome="deferred_to_human",
                    reasons=[f"hitl_trigger: {trigger.value}"],
                    hitl_trigger=trigger.value,
                    routed_to=_escalate_to(contract),
                )
        return _PASS

    @staticmethod
    def _trigger_matches(trigger: HumanInLoopTrigger, proposal: DecisionProposal) -> bool:
        meta = proposal.metadata
        if trigger is HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH:
            return proposal.requires_external_launch and meta.get("previously_launched") is not True
        if trigger is HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE:
            return bool(meta.get("brand_sensitive"))
        if trigger is HumanInLoopTrigger.SECURITY_SEVERITY_HIGH:
            return meta.get("security_severity") == "high"
        if trigger is HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE:
            return bool(meta.get("irreversible")) and float(meta.get("confidence", 1.0)) < 0.5
        # SPEND_OVER_CEILING and AUTHORITY_EXCEEDED handled in stages 4 / 1.
        return False

    # -- result builders ----------------------------------------------------
    def _terminate(
        self,
        proposal: DecisionProposal,
        contract: AgentContract | None,
        stages: list[str],
        stage: str,
        s: _Stage,
        score: DecisionScore | None = None,
    ) -> DecisionResult:
        assert s.outcome in ("rejected", "deferred_to_human")
        return DecisionResult(
            proposal_id=proposal.proposal_id,
            decision_id=decision_id_for(proposal.proposal_id),
            proposing_agent=proposal.proposing_agent_id,
            action_kind=proposal.action_kind,
            outcome=s.outcome,
            stages_completed=stages,
            stage_failed_at=stage,
            reasons=s.reasons,
            score=score,
            hitl_trigger=s.hitl_trigger,
            routed_to=s.routed_to,
            conflicts=s.conflicts,
            policy_version=POLICY_VERSION,
            authority_level=contract.authority_level if contract else None,
        )

    def _reject(
        self,
        proposal: DecisionProposal,
        contract: AgentContract | None,
        stages: list[str],
        stage: str,
        reasons: list[str],
    ) -> DecisionResult:
        return self._terminate(
            proposal, contract, stages, stage,
            _Stage(terminal=True, outcome="rejected", reasons=reasons),
        )

    def _remember(self, proposal: DecisionProposal, contract: AgentContract) -> None:
        key = (proposal.org_id, proposal.partition_key)
        self._recent.setdefault(key, []).append(
            _ProposalRecord(
                proposal_id=proposal.proposal_id,
                agent_id=proposal.proposing_agent_id,
                authority_level=contract.authority_level,
                occurred_at=proposal.occurred_at,
            )
        )


def _escalate_to(contract: AgentContract) -> str:
    """The immediate target up the escalation chain (ends at a human role)."""
    return contract.escalation_path[0] if contract.escalation_path else "human_owner"
