"""
The MVP agent contract set: the governed creative + growth team.

21 contracts spanning executive, creative, brand, growth, security, seo,
finance, sdr, and agency. `ALL_MVP_CONTRACTS` is the authoritative list the
registry seeds from.
"""

from __future__ import annotations

from ..base import AgentContract
from .agency import ALL_AGENCY_CONTRACTS
from .brand import ALL_BRAND_CONTRACTS
from .creative import ALL_CREATIVE_CONTRACTS
from .executive import ALL_EXECUTIVE_CONTRACTS
from .finance import cfo_agent
from .growth import ALL_GROWTH_CONTRACTS
from .sdr import ALL_SDR_CONTRACTS
from .security import ALL_SECURITY_CONTRACTS
from .seo import ALL_SEO_CONTRACTS

ALL_MVP_CONTRACTS: list[AgentContract] = [
    *ALL_EXECUTIVE_CONTRACTS,  # ceo, cmo
    *ALL_CREATIVE_CONTRACTS,  # vp_creative + copy/art/ops + 5 workers
    *ALL_BRAND_CONTRACTS,  # brand_guardian_agent, tone_of_voice_agent
    *ALL_GROWTH_CONTRACTS,  # director_growth
    *ALL_SECURITY_CONTRACTS,  # fraud_detection_agent
    *ALL_SEO_CONTRACTS,  # seo_keyword_agent (tool-enabled: search.web + memory.search)
    cfo_agent,  # finance's first tool-enabled capability: budget_summary
    *ALL_SDR_CONTRACTS,  # sdr_outreach_agent, lead_qualifier_agent
    *ALL_AGENCY_CONTRACTS,  # agency_requirements_analyst, agency_deliverable_drafter
]

__all__ = ["ALL_MVP_CONTRACTS"]
