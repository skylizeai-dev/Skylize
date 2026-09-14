"""Resolve the human an autonomous run is attributed to.

THE QUESTION THIS ANSWERS
-------------------------
`execution.py` defines the autonomous shape as `principal_id is None`
(`_principal_scope_for`: "Returns None for the autonomous shape"). But
`work_journal` rows are principal-scoped, and `GET /me/brief` reads them as
`principal_id = ctx.user_id` (edge/routes/brief.py:5-8). So an autonomous run
with no principal can never appear in anybody's brief -- it is, by construction,
invisible work. This module supplies the missing identity.

OWNER DECISION (2026-09-13): an autonomous run's `principal_id` is whoever sits
at the top of that agent's `reports_to` chain -- the human at the end of
`escalation_path`.

WHY THE CONTRACT AND NOT `_generation_manifest.csv`
---------------------------------------------------
The owner decision named the CSV. The CSV is not the load path and does not
agree with the code:

  * the running registry is seeded from the code-level contracts in
    `contracts/mvp/` (contracts/registry.py:23, contracts/mvp/__init__.py:29);
    nothing imports the CSV at runtime;
  * the CSV describes 15 of the 23 live contracts. The other 8 -- including
    `cfo_agent`, `seo_keyword_agent` and both sdr agents -- have no row at all,
    so a CSV-driven resolver could not attribute their runs;
  * of the 15 that DO overlap, 14 carry an `escalation_path` that differs from
    the contract's. For the pilot agent:
        code: manager_security_operations > director_cybersecurity > ...
        csv : manager_incident_response   > director_ai_safety     > ...

  What makes following the code safe rather than a reinterpretation of the owner
  decision: the TERMINAL element is `human_owner` in 100% of rows on BOTH sides,
  and the terminal element is the only thing this module reads. The two sources
  disagree about the middle of the chain and agree about its end, so the owner's
  decision resolves to the same human either way. The drift is reported for a
  separate doc-reconciliation decision; it is not silently adopted here.

HOW `human_owner` BECOMES A REAL ACCOUNT
----------------------------------------
`human_owner` is a sentinel string -- it names a ROLE at the top of every agent
chain, not a row. Migration 0020 already settled the derivation it maps through,
and this module deliberately reuses that exact predicate rather than inventing a
second one::

    'owner' = ANY(users.roles)     -- migration 0020 upgrade(), and the same
                                      predicate the partial unique index
                                      `users_one_owner_per_org` enforces
                                      (migration 0017), so AT MOST ONE row per
                                      org is possible by construction
      -> principal.principal_id = users.user_id::text   (migration 0020)
      -> RequestContext.user_id  = the JWT sub, minted from users.user_id
                                   (edge/deps.py:75, edge/routes/auth.py:136)
      -> which is exactly what GET /me/brief scopes its journal read on.

So the id this module returns is the same string the brief endpoint will look
for. That is not a coincidence to be preserved by care -- migration 0020 states
it as a rule ("Any future provisioning path MUST use the same derivation or the
two identity spaces silently diverge"), and this IS such a path.

WHAT THIS MODULE WILL NOT DO
----------------------------
It never falls back. Not to the first user in the org, not to a platform service
account, not to the agent itself. An org with no owner row yields
`PrincipalUnresolvable` and the run does not dispatch. An autonomous action
attributed to the wrong human is worse than one that did not happen.
"""

from __future__ import annotations

from ...contracts.base import AgentContract
from ...dal.ports import UserRepository, UserRow
from .errors import ContractNotAutonomous, PrincipalUnresolvable

#: The sentinel every `escalation_path` terminates at (contracts/base.py:9-10).
#: Not a user, not an agent_id -- the role of "the human who owns this org".
HUMAN_OWNER = "human_owner"

#: The role string that identifies that human on a `users` row. Migration 0020
#: and the `users_one_owner_per_org` index (migration 0017) both key on it.
OWNER_ROLE = "owner"


def terminal_human_role(contract: AgentContract) -> str:
    """The last link in the agent's reports_to chain.

    Raises `ContractNotAutonomous` when the chain is empty or does not end at
    `human_owner`: the platform's stated invariant is that `escalation_path` is
    "an ordered chain up the org tree ending at `human_owner`"
    (contracts/base.py:9-10), and a contract that violates it has no human at the
    top to attribute a run to. Checked rather than assumed -- all 23 live
    contracts satisfy it today, and this is what keeps a 24th from quietly
    becoming unattributable.
    """
    if not contract.escalation_path:
        raise ContractNotAutonomous(
            f"agent_id={contract.agent_id!r} declares an empty escalation_path; "
            "an autonomous run has no human to attribute to"
        )
    terminal = contract.escalation_path[-1]
    if terminal != HUMAN_OWNER:
        raise ContractNotAutonomous(
            f"agent_id={contract.agent_id!r} escalation_path ends at {terminal!r}, "
            f"not {HUMAN_OWNER!r}; the chain must terminate at a human role for an "
            "autonomous run to be attributable"
        )
    return terminal


class AutonomousPrincipalResolver:
    """agent_id + org_id -> the `principal_id` its autonomous runs belong to.

    GENERIC BY DESIGN. Nothing here is specific to the pilot agent: it reads the
    contract the registry already holds and the users the org already has, so
    wiring the other 22 agents needs no change to this class. The pilot scope
    limit lives in the TRIGGER (which agents are scheduled), never here.
    """

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def resolve(self, *, org_id: str, contract: AgentContract) -> str:
        """The `principal_id` for an autonomous run of `contract` in `org_id`.

        Raises `ContractNotAutonomous` if the contract is not eligible to run
        autonomously at all, and `PrincipalUnresolvable` if the org has no owner.
        Never returns a substitute.
        """
        if contract.lifecycle_status != "active":
            # contracts/base.py:91-95 -- a sandbox contract is "NOT part of the
            # autonomous fleet and never scheduled by the orchestrator", and a
            # retired one exists only to resolve historical tokens.
            raise ContractNotAutonomous(
                f"agent_id={contract.agent_id!r} has "
                f"lifecycle_status={contract.lifecycle_status!r}; only 'active' "
                "contracts may run autonomously"
            )
        terminal_human_role(contract)
        owner = await self._org_owner(org_id)
        # str() of the UUID, matching migration 0020's `users.user_id::text`.
        return str(owner.user_id)

    async def _org_owner(self, org_id: str) -> UserRow:
        """The single active owner of `org_id`.

        Reads through the EXISTING `UserRepository.list_by_org` port rather than
        adding a resolver-specific query: the `users_one_owner_per_org` index
        already guarantees at most one owner per org, so the filter below cannot
        be ambiguous, and no shared DAL interface has to change for the pilot.

        `is_active` is filtered too. A deactivated owner is not a human who can
        act on a brief, and attributing new autonomous work to a disabled account
        would park it where nobody is watching.
        """
        owners = [
            u
            for u in await self._users.list_by_org(org_id)
            if OWNER_ROLE in u.roles and u.is_active
        ]
        if not owners:
            raise PrincipalUnresolvable(
                f"org_id={org_id!r} has no active user with the {OWNER_ROLE!r} role, "
                f"so the {HUMAN_OWNER!r} at the end of the escalation chain resolves "
                "to no account; refusing to attribute an autonomous run to a "
                "substitute principal"
            )
        if len(owners) > 1:
            # Unreachable while `users_one_owner_per_org` is enforced. Checked
            # anyway: if that index is ever dropped, the failure must be a refusal
            # to guess, not an arbitrary [0].
            raise PrincipalUnresolvable(
                f"org_id={org_id!r} has {len(owners)} active owners "
                f"({sorted(u.email for u in owners)}); the users_one_owner_per_org "
                "invariant (migration 0017) does not hold, so the autonomous "
                "principal is ambiguous"
            )
        return owners[0]
