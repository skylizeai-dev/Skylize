"""
The agent contract registry.

Foundation scope: an in-memory, fail-closed resolver seeded from the code-level
`AgentContract` objects in `mvp/`. It also validates that every contract's
`input_schema` / `output_schema` dotted path resolves to an importable Pydantic
model — this is exactly the check the CI contract gate runs.

Persistence (versioned rows in the `agent_contracts` table) is a DAL concern and
is intentionally NOT here: `contracts/` must remain free of a database driver
per the import-linter boundary rule. The Orchestrator (later sprint) seeds the
DB from `ALL_MVP_CONTRACTS` and then resolves through this registry's cache.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel

from .base import AgentContract
from .mvp import ALL_MVP_CONTRACTS


class AgentNotRegistered(Exception):
    """Raised when an unknown agent_id is resolved — the system fails closed."""


class ContractSchemaError(Exception):
    """Raised when a contract's input/output schema path is unresolvable."""


def resolve_model(dotted_path: str) -> type[BaseModel]:
    """Import a `pkg.module.ClassName` path and return the Pydantic model class.

    Raises ContractSchemaError if the path is unimportable or not a BaseModel.
    """
    module_path, _, class_name = dotted_path.rpartition(".")
    if not module_path:
        raise ContractSchemaError(f"not a dotted path: {dotted_path!r}")
    try:
        module = importlib.import_module(module_path)
        obj = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ContractSchemaError(f"cannot resolve schema {dotted_path!r}: {exc}") from exc
    if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
        raise ContractSchemaError(f"{dotted_path!r} is not a Pydantic model")
    return obj


class AgentRegistry:
    """In-memory cache of AgentContracts. Fail-closed on unknown agent_id."""

    def __init__(self, contracts: list[AgentContract]) -> None:
        self._cache: dict[str, AgentContract] = {}
        for contract in contracts:
            if contract.agent_id in self._cache:
                raise ValueError(f"duplicate agent_id in registry: {contract.agent_id}")
            self._cache[contract.agent_id] = contract

    def resolve(
        self,
        agent_id: str,
        *,
        tenant_overrides: dict[str, Any] | None = None,
    ) -> AgentContract:
        """Resolve a contract; fail closed on unknown agent_id.

        Tenant overrides may only TIGHTEN budgets (most-restrictive-wins),
        never loosen them below the platform contract.
        """
        contract = self._cache.get(agent_id)
        if contract is None:
            raise AgentNotRegistered(
                f"agent_id={agent_id!r} is not registered; unknown agents fail closed"
            )
        if tenant_overrides:
            contract = self._apply_overrides(contract, tenant_overrides)
        return contract

    def all(self) -> list[AgentContract]:
        return list(self._cache.values())

    def agent_ids(self) -> list[str]:
        return list(self._cache.keys())

    def validate_schemas(self) -> None:
        """Assert every contract's I/O schema paths resolve. The CI gate calls this."""
        for contract in self._cache.values():
            resolve_model(contract.input_schema)
            resolve_model(contract.output_schema)

    @staticmethod
    def _apply_overrides(
        contract: AgentContract, overrides: dict[str, Any]
    ) -> AgentContract:
        data = contract.model_dump()
        if "max_token_budget" in overrides:
            data["max_token_budget"] = min(
                data["max_token_budget"], int(overrides["max_token_budget"])
            )
        if "max_execution_time_seconds" in overrides:
            data["max_execution_time_seconds"] = min(
                data["max_execution_time_seconds"],
                int(overrides["max_execution_time_seconds"]),
            )
        return AgentContract.model_validate(data)


# The default MVP registry — the 15 governed creative + growth contracts.
MVP_REGISTRY = AgentRegistry(ALL_MVP_CONTRACTS)
