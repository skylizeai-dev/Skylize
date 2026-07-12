"""
AgentRegistrySync — application-layer bridge between the in-memory
AgentRegistry (contracts/) and the agent_contracts table (dal/).

Responsibilities:
  sync_to_db   — upsert every in-memory contract into Postgres (idempotent).
  load_from_db — reconstitute an AgentRegistry from the active DB rows.
  get_contract_db — fetch and validate a single contract by agent_id.

Import boundary: this module is in skylize.app and therefore may not import
asyncpg, skylize.dal.repositories, or skylize.dal.connection directly.
It codes against the ContractRepository port only.
"""

from __future__ import annotations

import json
import logging

from skylize.contracts.base import AgentContract
from skylize.contracts.registry import AgentRegistry
from skylize.dal.ports import ContractRepository

logger = logging.getLogger(__name__)

_VERSION = 1  # contracts are not versioned in MVP


class AgentRegistrySync:
    def __init__(
        self,
        contract_repo: ContractRepository,
        in_memory_registry: AgentRegistry,
    ) -> None:
        self._repo = contract_repo
        self._registry = in_memory_registry

    async def sync_to_db(self) -> int:
        contracts = self._registry.all()
        for contract in contracts:
            await self._repo.upsert(
                contract.agent_id,
                _VERSION,
                contract.model_dump_json(),
            )
        count = len(contracts)
        logger.info("synced %d contracts to DB", count)
        return count

    async def load_from_db(self) -> AgentRegistry:
        rows = await self._repo.load_all_active()
        validated: list[AgentContract] = []
        for agent_id, contract_json in rows:
            try:
                validated.append(AgentContract.model_validate(json.loads(contract_json)))
            except Exception as exc:
                raise ValueError(
                    f"contract validation failed for agent_id={agent_id!r}: {exc}"
                ) from exc
        registry = AgentRegistry(contracts=validated)
        logger.info("loaded %d contracts from DB", len(validated))
        return registry

    async def get_contract_db(self, agent_id: str) -> AgentContract | None:
        raw = await self._repo.get_latest_active(agent_id)
        if raw is None:
            return None
        return AgentContract.model_validate(json.loads(raw))
