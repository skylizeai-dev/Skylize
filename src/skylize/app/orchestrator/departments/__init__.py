"""Autonomous commerce departments as governed LangGraph flow skeletons (SCAFFOLD).

Four departments — Market Intelligence, Creative Ops, Marketing Ops, Finance &
Risk — each run the same governed shape: plan -> govern -> (act | await_human |
rejected). The govern node is the REAL inline `DecisionEvaluator`
(app/decision_engine/evaluator.py), so authority, policy, capital (budget
ceiling) and HITL gates are the production rules, not stand-ins. Only the tools
are supplied by the caller; this package ships no tool and makes no external call.

Deliberately NOT done (owner decisions, see contracts.py):
- No new agent contracts and no new action kinds: each department is MAPPED onto
  an agent already in the governed registry and an action kind already in
  `KNOWN_ACTION_KINDS`. Reshaping the agent org chart is not this package's call.
- Not wired into bootstrap, the Orchestrator facade, or any route.
- A deferral stops the flow; it does not write a HITL queue row or publish
  `decision.*` events — that remains the DecisionEngine facade's job on the async
  path (app/decision_engine/engine.py).
"""

from .contracts import (
    DEFAULT_DEPARTMENT_SPECS,
    DepartmentSpec,
    DepartmentTool,
    ProposalDraft,
)
from .flows import (
    ActiveAgentGate,
    DepartmentDeps,
    DepartmentRunResult,
    build_department_graph,
    run_department,
    run_department_chain,
)

__all__ = [
    "ActiveAgentGate",
    "DEFAULT_DEPARTMENT_SPECS",
    "DepartmentDeps",
    "DepartmentRunResult",
    "DepartmentSpec",
    "DepartmentTool",
    "ProposalDraft",
    "build_department_graph",
    "run_department",
    "run_department_chain",
]
