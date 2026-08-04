"""The work-journal brief — GET /me/brief, POST /me/brief/seen.

Read-only projection of the caller's own append-only work journal
(skylize.app.principal.journal). `principal_id` is always `ctx.user_id` — the
authenticated caller's own identity — never a query parameter or body field,
mirroring spend.py's convention for `org_id`. There is deliberately no other
principal_id resolution: every live run today carries a real `user_id`
(edge/routes/agents.py:94), so there is no case to fall back from.

The summary is produced by the model from `assemble_brief`'s deterministic
STRUCTURE, never from the raw journal (journal.py's own stated invariant) —
and journal content is untrusted for prompt-injection purposes (it can
eventually carry text derived from agent runs), so it goes through
`container.llm`, the shared `GuardedLLMGateway` (bootstrap.py), never a raw
provider adapter. `GuardedLLMGateway.generate()` screens both `prompt` and
`system` before any provider call.

This is a READ path: nothing here writes a journal entry. See
dal/work_journal.py for why the run-completion write is not wired yet.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...adapters.llm.gateway import LLMGenerateRequest
from ...app.principal.journal import assemble_brief
from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/me/brief", tags=["brief"])

_ALL_ROLES = ("owner", "admin", "operator", "analyst", "viewer")

# A brief is a short narrative, not a report — keep the model call cheap.
_MAX_SUMMARY_TOKENS = 500
_NOTHING_NEW_SUMMARY = "Nothing new since you last checked."

_SUMMARY_SYSTEM_PROMPT = (
    "You write a short, plain-language morning brief for a busy person from "
    "structured work-journal data. Summarize ONLY what is in the JSON given to "
    "you — never invent an action, a number, or an outcome that is not present. "
    "Lead with anything in needs_attention. Two or three short paragraphs, no "
    "headers, no bullet lists. Return ONLY valid JSON: {\"summary\": \"...\"}. "
    "No prose before or after the JSON object."
)


class BriefResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    entry_count: int
    head_seq: int
    window_start: str | None
    window_end: str | None
    total_cost_minor: int
    needs_attention: list[dict[str, Any]]
    done_while_away: list[dict[str, Any]]
    your_own_actions: list[dict[str, Any]]


class MarkSeenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_seq: int = Field(ge=0)


@router.get("", response_model=BriefResponse)
async def get_brief(
    ctx: RequestContext = Depends(require_any_role_or_user(*_ALL_ROLES)),
    container: Container = Depends(get_container),
) -> BriefResponse:
    entries, head_seq = await container.work_journal.unseen(
        org_id=ctx.org_id, principal_id=ctx.user_id
    )
    structure = assemble_brief(entries)

    if structure["entry_count"] == 0:
        summary = _NOTHING_NEW_SUMMARY
    else:
        response = await container.llm.generate(
            LLMGenerateRequest(
                model="fast",
                system=_SUMMARY_SYSTEM_PROMPT,
                prompt=_brief_prompt(structure),
                requested_max_tokens=_MAX_SUMMARY_TOKENS,
                governance_token_id=uuid4(),
                org_id=ctx.org_id,
                correlation_id=uuid4(),
                agent_id="brief_summarizer",
            )
        )
        # Same convention as every other LLMGateway.generate() call site
        # (AgentExecutionService._build_system_prompt): JSON-only output. A
        # model that ignores the instruction falls back to the raw text rather
        # than a 500 — the brief is best-effort narration, not a governed
        # action, so a malformed response degrades gracefully.
        try:
            summary = json.loads(response.text)["summary"]
        except (json.JSONDecodeError, KeyError, TypeError):
            summary = response.text

    return BriefResponse(
        summary=summary,
        entry_count=structure["entry_count"],
        head_seq=structure["head_seq"],
        window_start=structure["window_start"],
        window_end=structure["window_end"],
        total_cost_minor=structure["total_cost_minor"],
        needs_attention=structure["needs_attention"],
        done_while_away=structure["done_while_away"],
        your_own_actions=structure["your_own_actions"],
    )


@router.post("/seen", status_code=204)
async def mark_brief_seen(
    body: MarkSeenRequest,
    ctx: RequestContext = Depends(require_any_role_or_user(*_ALL_ROLES)),
    container: Container = Depends(get_container),
) -> None:
    await container.work_journal.mark_seen(
        org_id=ctx.org_id, principal_id=ctx.user_id, to_seq=body.to_seq
    )


def _brief_prompt(structure: dict[str, Any]) -> str:
    return (
        "Summarize this work-journal window as a brief:\n\n"
        f"{json.dumps(structure, indent=2)}"
    )
