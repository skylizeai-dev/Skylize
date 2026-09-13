"""Agent-prompt endpoint — called by n8n before every agent LLM call."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ...app.agent_prompts.service import AgentPromptService
from ...contracts.registry import AgentNotRegistered, MVP_REGISTRY
from ...schemas.agent_prompt import AgentPromptResponse
from ..deps import enforce_anonymous_rate_limit

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
    # Constant-time: a plain `!=` leaks the shared secret's prefix through
    # comparison timing. Same pattern as knowledge.py's `_verify_hmac`
    # (knowledge.py:101). Outcomes are unchanged -- 200/401/503 as before.
    # Compared as BYTES: `compare_digest` raises TypeError on a str with any
    # codepoint > 127, and a header is latin-1 decodable, so comparing the raw
    # strs would turn a non-ASCII key from a 401 into a 500.
    if not x_skylize_api_key or not hmac.compare_digest(
        x_skylize_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        logger.warning(
            "api_key_rejected agent_prompts remote=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@router.get(
    "/{agent_id}",
    response_model=AgentPromptResponse,
    # `enforce_anonymous_rate_limit` (peer address), not `enforce_rate_limit`
    # (org_id): this route's auth is a pre-auth static shared-secret check, so
    # there is no RequestContext/org_id to key on. It is the route's only
    # limiter -- the gateway installs no rate-limit middleware
    # (gateway.py:48,87), so nothing double-applies.
    dependencies=[Depends(enforce_anonymous_rate_limit), Depends(_verify_api_key)],
)
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
