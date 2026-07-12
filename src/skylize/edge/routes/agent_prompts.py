"""Agent-prompt endpoint — called by n8n before every agent LLM call."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ...app.agent_prompts.service import AgentPromptService
from ...contracts.registry import AgentNotRegistered, MVP_REGISTRY
from ...schemas.agent_prompt import AgentPromptResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-prompts", tags=["agent-prompts"])


async def _verify_api_key(
    request: Request,
    x_skylize_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Validate X-Skylize-API-Key header against SKYLIZE_N8N_API_KEY setting."""
    from ...config import get_settings
    settings = get_settings()
    expected = settings.n8n_api_key
    if not expected:
        # Key not configured — fail closed rather than accidentally open
        raise HTTPException(status_code=503, detail="agent-prompts endpoint not configured")
    if not x_skylize_api_key or x_skylize_api_key != expected:
        logger.warning(
            "api_key_rejected agent_prompts remote=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@router.get("/{agent_id}", response_model=AgentPromptResponse, dependencies=[Depends(_verify_api_key)])
async def get_agent_prompt(agent_id: str) -> AgentPromptResponse:
    """Return system prompt and metadata for agent_id. Called by n8n LLM nodes."""
    svc = AgentPromptService(MVP_REGISTRY)

    try:
        response = svc.get_prompt(agent_id, org_id="platform")
    except AgentNotRegistered:
        logger.info("agent_not_found agent_id=%s", agent_id)
        raise HTTPException(status_code=404, detail=f"agent '{agent_id}' not registered")

    logger.info(
        "agent_prompt_served agent_id=%s authority=%s model_tier=%s",
        response.agent_id,
        response.authority_level,
        response.model_tier,
    )
    return response
