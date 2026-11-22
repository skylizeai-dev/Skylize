"""Department contracts: what each commerce department is, and what its tool must do.

A `DepartmentSpec` is a MAPPING onto things that already exist and are governed:
- `proposing_agent_id` must resolve in the `AgentRegistry` passed at run time
  (the evaluator fails closed on an unknown agent);
- `action_kind` must already be in `KNOWN_ACTION_KINDS`
  (app/decision_engine/events.py:44-51). Extending that set is an authorized-
  enum decision (precedent: owner decision K2), so an unknown kind is refused
  HERE, at construction, instead of being rejected later as an unknown class.

PROVISIONAL MAPPINGS. The governed registry has no Market-Intelligence agent and
the engine has no research/analysis action class. `market_intelligence` is
therefore mapped onto the growth worker `seo_keyword_agent` and the internal,
spend-free `creative.review` class, and marked `provisional=True`. The real
shape (a dedicated action kind and/or contract) is an owner decision; it is
surfaced, not decided, here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ...decision_engine.events import KNOWN_ACTION_KINDS, DecisionResult


@dataclass(frozen=True, slots=True)
class DepartmentSpec:
    department_id: str
    proposing_agent_id: str
    action_kind: str
    capital_scope: str
    requires_external_launch: bool = False
    #: True when the mapping onto an existing agent/action kind is a stand-in
    #: awaiting an owner decision (see module docstring).
    provisional: bool = False

    def __post_init__(self) -> None:
        if self.action_kind not in KNOWN_ACTION_KINDS:
            raise ValueError(
                f"department {self.department_id!r}: action_kind {self.action_kind!r} is not "
                f"in KNOWN_ACTION_KINDS {sorted(KNOWN_ACTION_KINDS)}; adding one is an owner "
                "decision, not a department default"
            )
        if not self.department_id or not self.proposing_agent_id or not self.capital_scope:
            raise ValueError("department_id, proposing_agent_id and capital_scope are required")


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """What a department's planning step proposes. Money is in MINOR units
    (cents) — the budget_ledger unit (ADR-0006), which is what the evaluator's
    capital stage compares against `BudgetCeiling.ceiling_minor_units`."""

    summary: str
    partition_key: str
    spend_minor_units: int | None = None
    currency: str | None = None
    #: Signals the policy / HITL stages match on (brand_sensitive, irreversible,
    #: confidence, previously_launched, brand_safety, security_severity).
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: Carried to the next department in a chain; never read by the gates.
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.spend_minor_units is None) != (self.currency is None):
            raise ValueError("spend_minor_units and currency must be set together")


class DepartmentTool(Protocol):
    """A department's tool surface. `execute` is called ONLY after the
    evaluator returned `approved` for the draft `plan` produced."""

    async def plan(self, *, org_id: str, payload: Mapping[str, Any]) -> ProposalDraft: ...

    async def execute(
        self, *, org_id: str, draft: ProposalDraft, decision: DecisionResult
    ) -> Mapping[str, Any]: ...


#: Default mapping onto the governed MVP registry (contracts/registry.py
#: MVP_REGISTRY). Authority/HITL behaviour therefore comes from those contracts:
#: e.g. director_growth carries spend_over_ceiling + first_external_launch;
#: director_risk carries low_confidence_irreversible + spend_over_ceiling.
DEFAULT_DEPARTMENT_SPECS: tuple[DepartmentSpec, ...] = (
    DepartmentSpec(
        department_id="market_intelligence",
        proposing_agent_id="seo_keyword_agent",
        action_kind="creative.review",
        capital_scope="growth",
        provisional=True,
    ),
    DepartmentSpec(
        department_id="creative_ops",
        proposing_agent_id="creative_operations_manager",
        action_kind="creative.review",
        capital_scope="creative",
    ),
    DepartmentSpec(
        department_id="marketing_ops",
        proposing_agent_id="director_growth",
        action_kind="sales.campaign",
        capital_scope="growth",
        requires_external_launch=True,
    ),
    DepartmentSpec(
        department_id="finance_risk",
        proposing_agent_id="director_risk",
        action_kind="sales.budget_reallocation",
        capital_scope="finance",
    ),
)
