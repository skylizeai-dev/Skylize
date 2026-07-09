"""Response schema for the agent-prompts endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AgentPromptResponse(BaseModel):
    agent_id: str
    system_prompt: str
    authority_level: str
    department: str
    max_token_budget: int
    failure_mode: str
    memory_read_access: list[str]
    human_in_loop_triggers: list[str]
    model_tier: Literal["frontier", "mini"]
