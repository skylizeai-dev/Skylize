"""
Agent contract definitions — one module per department.

All contracts defined here are exact implementations of the agent specs in
docs/03_agents/. Values are sourced directly from those docs; nothing is invented.
"""

from __future__ import annotations

from ..base import AgentContract
from .creative import ALL_CREATIVE_CONTRACTS
from .finance import ALL_FINANCE_CONTRACTS
from .security import ALL_SECURITY_CONTRACTS

ALL_DEFINITION_CONTRACTS: list[AgentContract] = [
    *ALL_FINANCE_CONTRACTS,
    *ALL_SECURITY_CONTRACTS,
    *ALL_CREATIVE_CONTRACTS,
]

__all__ = [
    "ALL_DEFINITION_CONTRACTS",
    "ALL_FINANCE_CONTRACTS",
    "ALL_SECURITY_CONTRACTS",
    "ALL_CREATIVE_CONTRACTS",
]
