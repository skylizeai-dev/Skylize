"""
The MVP agent contract set: the governed creative + growth team.

15 contracts spanning executive, creative, brand, growth, and security.
`ALL_MVP_CONTRACTS` is the authoritative list the registry seeds from.
"""

from __future__ import annotations

from ..base import AgentContract
from .brand import ALL_BRAND_CONTRACTS
from .creative import ALL_CREATIVE_CONTRACTS
from .executive import ALL_EXECUTIVE_CONTRACTS
from .growth import ALL_GROWTH_CONTRACTS
from .security import ALL_SECURITY_CONTRACTS

ALL_MVP_CONTRACTS: list[AgentContract] = [
    *ALL_EXECUTIVE_CONTRACTS,  # ceo, cmo
    *ALL_CREATIVE_CONTRACTS,  # vp_creative + copy/art/ops + 5 workers
    *ALL_BRAND_CONTRACTS,  # brand_guardian_agent, tone_of_voice_agent
    *ALL_GROWTH_CONTRACTS,  # director_growth
    *ALL_SECURITY_CONTRACTS,  # fraud_detection_agent
]

__all__ = ["ALL_MVP_CONTRACTS"]
