"""
Thin adapters that bridge the runtime.LLMAgentRunner Protocol seams to the
concrete AgentRegistry and GovernanceAuthority implementations.

These adapters live in app/ because they depend on both contracts/ (AgentRegistry)
and app/governance/ (GovernanceAuthority) — the import-linter layer that is
allowed to import from both.

Bridging gaps:
  AgentRegistry.resolve(agent_id) is synchronous and raises AgentNotRegistered
  → AgentRegistryProtocol.get_contract(agent_id) is async and returns None on miss.

  GovernanceAuthority.mint(contract, *, org_id, correlation_id, scope) takes a
  full AgentContract + correlation UUID
  → GovernanceAuthorityProtocol.mint_token(*, agent_id, org_id, scope,
     max_token_budget, max_execution_time_seconds) takes scalar args.

  GovernanceAuthority has no check_live_state — the equivalent is assert_active
  which raises GovernanceDenied instead of returning (bool, str).
"""

from __future__ import annotations

from uuid import uuid4

from ..contracts.base import AgentContract, GovernanceToken
from ..contracts.registry import AgentNotRegistered, AgentRegistry
from .governance.authority import GovernanceAuthority, GovernanceDenied


class AgentRegistryAdapter:
    """Wraps the synchronous AgentRegistry to satisfy AgentRegistryProtocol."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    async def get_contract(self, agent_id: str) -> AgentContract | None:
        try:
            return self._registry.resolve(agent_id)
        except AgentNotRegistered:
            return None


class GovernanceAuthorityAdapter:
    """Wraps GovernanceAuthority to satisfy GovernanceAuthorityProtocol.

    check_live_state — calls assert_active and converts GovernanceDenied to
    (True, reason); returns (False, "") when the agent is active.

    mint_token — resolves the contract from the registry (needed for mint's
    full AgentContract arg), generates a correlation_id, and delegates to
    authority.mint with the narrowed scope and budget.
    """

    def __init__(self, authority: GovernanceAuthority, registry: AgentRegistry) -> None:
        self._authority = authority
        self._registry = registry

    async def check_live_state(self, agent_id: str, org_id: str) -> tuple[bool, str]:
        try:
            await self._authority.assert_active(agent_id, org_id)
            return False, ""
        except GovernanceDenied as exc:
            return True, str(exc)

    async def mint_token(
        self,
        *,
        agent_id: str,
        org_id: str,
        scope: list[str],
        max_token_budget: int,
        max_execution_time_seconds: int,
    ) -> GovernanceToken:
        contract = self._registry.resolve(agent_id)
        # Tighten the contract's budgets to what the runner requested.
        tightened = contract.model_copy(
            update={
                "max_token_budget": min(max_token_budget, contract.max_token_budget),
                "max_execution_time_seconds": min(
                    max_execution_time_seconds, contract.max_execution_time_seconds
                ),
            }
        )
        return await self._authority.mint(
            tightened,
            org_id=org_id,
            correlation_id=uuid4(),
            scope=scope,
        )
