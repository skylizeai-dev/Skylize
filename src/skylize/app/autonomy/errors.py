"""Typed failures of the autonomous-run path.

Each one is a REFUSAL, never a degraded run. An autonomous run has no human in
the loop to notice a silent fallback, so every ambiguity here fails closed and
says which invariant it failed on.
"""

from __future__ import annotations


class PrincipalUnresolvable(RuntimeError):
    """No `principal_id` could be resolved for an autonomous run.

    Raised rather than substituting any account. `work_journal` rows are
    principal-scoped, so an unresolvable principal means the run has no brief to
    appear in and no human who owns its outcome -- running it anyway would
    produce an action nobody is accountable for, which is the exact failure this
    platform exists to prevent.
    """


class ContractNotAutonomous(RuntimeError):
    """The contract cannot be run autonomously at all.

    Two causes, both structural: `lifecycle_status != "active"` (a sandbox
    contract is explicitly "never scheduled by the orchestrator",
    contracts/base.py:91-95), or an `escalation_path` that does not terminate at
    the `human_owner` sentinel (there is then no human at the top of the chain to
    attribute the run to).
    """
