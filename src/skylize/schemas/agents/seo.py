"""SEO crew agent I/O models (MVP: `seo_keyword_agent`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeoKeywordExecuteIn(_Base):
    topic: str
    target_market: str = "global"
    competitor_urls: list[str] = Field(default_factory=list)


class SeoKeywordExecuteOut(_Base):
    primary_keywords: list[str]
    keyword_difficulty_notes: str
    content_angle_suggestions: list[str]
