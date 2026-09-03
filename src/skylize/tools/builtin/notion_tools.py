"""The Notion connector — page/database creation and content append.

Three tools (integration_inputs.md 2.7, `[DRAFT]` 2026-09-03):
  * `integration.notion_create_page`     — routine (Q2.7b)
  * `integration.notion_create_database` — routine (Q2.7b)
  * `integration.notion_append_blocks`   — routine (Q2.7b)

NO `ToolPermissionProfile`, AND THAT IS A FINDING RATHER THAN AN OMISSION.
`[LIVE-VERIFIED]` 2026-09-03 against
https://developers.notion.com/reference/capabilities: Notion's API exposes no
sharing, permission-changing, or external-invitation capability at all. The
capability list is Read content / Update content / Insert content / Read comments
/ Insert comments plus a user-information setting, and nothing more. A connection
can only reach pages a human has already shared with it in Notion's UI, and the
API cannot widen that boundary.

So Notion has no analogue of Drive's `permissions.create` or Asana's
`addMembers` — no action hands data to a party the agent picks at run time — and
every verb here is routine. A future author must not add the third gate here to
make Notion "look like" the other two connectors; there is nothing for it to
authorize.

Org-level: every call resolves the calling org's own OAuth grant through the
provider-agnostic infrastructure. This module contains NO OAuth logic of its own
— Notion's entire OAuth surface is `app/credentials/notion_provider.py`.

=============================================================================
THE REVOCATION WIRING — the most important thing in this file
=============================================================================
A Notion grant NEVER EXPIRES (`expires_at IS NULL`, migration 0023), because
Notion's token response carries no `expires_in`. That has a consequence that is
easy to miss and severe if missed:

  `evaluate_grant` never returns NEEDS_REFRESH for such a grant, so no refresh is
  ever attempted, so the entire refresh-failure revocation path
  (`_post_refresh` -> `_classify_failure` -> `is_revocation_error` ->
  `GrantRevoked`) is UNREACHABLE. Nothing about the passage of time can ever mark
  a Notion grant dead.

Left unhandled, a customer who disconnects Skylize in Notion would keep a row
marked 'valid' forever: every call would 401 at Notion, the ToolProxy credential
gate would keep passing the grant as healthy, and nobody would ever be told to
reconnect. That is exactly the silent degradation `app/credentials/oauth.py`'s
revocation handling exists to prevent, arriving through a door the refresh path
does not cover.

`OAuthCredentialService.mark_revoked_by_provider` was added in `8f147d4` for
precisely this, and shipped with NO production caller. **This connector is that
caller** — see `_is_dead_grant` (which decides) and `_check_response` (which
records). Every Notion call in this module funnels through `_check_response`, so
the wiring is structural: a verb added later inherits it rather than having to
remember it. Removing those calls does not degrade Notion gracefully — it
reopens the gap completely.

ONLY 401 `unauthorized` COUNTS, and the narrowness is deliberate. `[LIVE-VERIFIED]`
2026-09-03, https://developers.notion.com/reference/status-codes:
  * 401 `unauthorized`        — "The bearer token is not valid." UNAMBIGUOUS:
                                the token itself is dead. -> mark revoked.
  * 403 `restricted_resource` — "The token lacks permission, or the request
                                exceeds a workspace block limit." NOT a dead
                                token: this is OUR integration registered without
                                the needed capability (2.7 Q2.7a), or the
                                customer hitting a workspace block quota.
                                Marking a customer revoked for our own
                                misconfiguration would demand a pointless
                                reconnect and hide the real fault. -> NOT revoked.
  * 404 `object_not_found`    — "the resource has not been shared with owner of
                                the bearer token." A sharing gap in Notion's UI,
                                not a dead grant. -> NOT revoked.

RETRY POLICY DIVERGES FROM DRIVE AND ASANA, on Notion's own written instruction.
`[LIVE-VERIFIED]` 2026-09-03, https://developers.notion.com/reference/request-limits:
Notion says to "Retry 500, 502, 503, 504 only for idempotent requests (GET,
DELETE)". Every verb in this module is a non-idempotent POST, so 5xx is NOT
retried here — a timeout-then-success retry would create a SECOND page. Drive and
Asana do retry 5xx; that is a real (and pre-existing, see 2.5 Q2.5's idempotency
note) duplication hazard which this connector declines to inherit. Only 429 and
529 are retried, honouring `Retry-After`, with jittered backoff capped at 30s and
a bounded attempt count — Notion's own published recommendation.

WHY RAW `httpx` AND NOT AN SDK. Same reasons Drive and Asana gave, re-checked:
`pyproject.toml` carries no Notion dependency; and an SDK bringing its own auth
handling would bypass the RLS-scoped, audited, `connection_state`-tracking
credential store — which for Notion would specifically bypass the revocation
wiring above, the one thing keeping a non-expiring grant honest.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...app.credentials.notion_provider import NOTION_PROVIDER, NOTION_VERSION
from ...app.credentials.oauth import OAuthCredentialService
from ..base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
)

log = structlog.get_logger()

_API_BASE = "https://api.notion.com/v1"

#: Retried statuses. 429 = `rate_limited`, 529 = `service_overload`. 5xx is
#: DELIBERATELY ABSENT — see the module docstring's retry section.
_RETRYABLE_STATUS = {429, 529}

#: Notion's own recommendation: exponential backoff with jitter, capped at 30s,
#: with a bounded attempt count.
_MAX_ATTEMPTS = 4
_BACKOFF_CAP_SECONDS = 30.0

#: Notion payload ceilings, enforced client-side so an oversized request is
#: refused with a clear message instead of a 400 from Notion. `[LIVE-VERIFIED]`
#: 2026-09-03: 1000 block elements and 500KB per request; rich text capped at
#: 2000 characters.
_MAX_BLOCKS_PER_REQUEST = 1000
_MAX_RICH_TEXT_CHARS = 2000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class NotionCreatePageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1, max_length=_MAX_RICH_TEXT_CHARS, description="Page title."
    )
    parent_page_id: str | None = Field(
        default=None,
        description=(
            "Id of the parent PAGE to create this page under. Must already be "
            "shared with the Skylize integration in Notion."
        ),
    )
    parent_database_id: str | None = Field(
        default=None,
        description="Id of the parent DATABASE to create this page in.",
    )
    paragraphs: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Optional paragraphs of body text to create the page with.",
    )

    @model_validator(mode="after")
    def _exactly_one_parent(self) -> "NotionCreatePageIn":
        """Notion requires exactly one parent, and picking a default would be a
        guess about where a customer's content should land."""
        chosen = [p for p in (self.parent_page_id, self.parent_database_id) if p]
        if len(chosen) != 1:
            raise ValueError(
                "exactly one of parent_page_id or parent_database_id is required"
            )
        return self


class NotionCreatePageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str
    title: str
    url: str | None = None


class NotionCreateDatabaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=_MAX_RICH_TEXT_CHARS)
    parent_page_id: str = Field(
        min_length=1,
        description="Id of the parent page. Notion requires a page parent here.",
    )
    text_properties: list[str] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Optional additional rich-text column names. A title column named "
            "'Name' is always created, because Notion requires exactly one."
        ),
    )


class NotionCreateDatabaseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_id: str
    title: str
    url: str | None = None


class NotionAppendBlocksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(
        min_length=1,
        description=(
            "Id of the page or block to append to. A page id is also a block id "
            "in Notion's model."
        ),
    )
    paragraphs: list[str] = Field(
        min_length=1,
        max_length=100,
        description="Paragraphs of text to append, in order.",
    )


class NotionAppendBlocksOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    appended: int


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def _rich_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def _paragraph_blocks(paragraphs: list[str]) -> list[dict[str, Any]]:
    """Plain paragraphs. Deliberately NOT a markdown converter.

    Notion's block model is rich (headings, lists, code, callouts, children), and
    a half-built markdown translator is the kind of thing that silently mangles a
    customer's deliverable. Paragraphs are what the narrative in 2.7 Q2.7c needs;
    richer block types are out of scope there (Q2.7f) rather than approximated.
    """
    for p in paragraphs:
        if len(p) > _MAX_RICH_TEXT_CHARS:
            raise ToolExecutionError(
                f"paragraph exceeds Notion's {_MAX_RICH_TEXT_CHARS}-character "
                f"rich-text limit ({len(p)} characters)"
            )
    if len(paragraphs) > _MAX_BLOCKS_PER_REQUEST:
        raise ToolExecutionError(
            f"cannot send {len(paragraphs)} blocks in one request; Notion's "
            f"ceiling is {_MAX_BLOCKS_PER_REQUEST}"
        )
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(p)},
        }
        for p in paragraphs
    ]


# ---------------------------------------------------------------------------
# Errors + the revocation decision
# ---------------------------------------------------------------------------

def _error_fields(response: httpx.Response) -> tuple[str, str]:
    """`(code, message)` out of Notion's error envelope.

    Notion's shape is `{"object": "error", "code": ..., "message": ..., "status": ...}`
    — a third distinct error format in this codebase, different from Drive's
    `{"error": {"message": ...}}` and Asana's `{"errors": [{"message": ...}]}`.
    Each is parsed in its own place; none is assumed to look like another.
    """
    code, message = "", response.text[:200]
    try:
        payload = response.json()
    except ValueError:
        return code, message
    if isinstance(payload, dict):
        if isinstance(payload.get("code"), str):
            code = payload["code"]
        if isinstance(payload.get("message"), str):
            message = payload["message"]
    return code, message


def _is_dead_grant(status_code: int, code: str) -> bool:
    """Whether a live REST response unambiguously proves the GRANT is dead.

    The narrowness here is the whole point; see the module docstring. Only 401
    `unauthorized` ("The bearer token is not valid") qualifies. 403
    `restricted_resource` is our own capability misconfiguration or a customer
    block-limit, and 404 `object_not_found` is an unshared page — neither says
    anything about the token, and reporting either as a revocation would demand a
    reconnect that fixes nothing.

    A bare 401 with an unparseable body still counts: Notion's only documented
    401 code is `unauthorized`, so a 401 we cannot parse is far more likely a dead
    token than anything else, and denying + prompting a reconnect is the safe
    direction for an auth failure on a grant that has no other liveness signal.
    """
    return status_code == 401 and code in ("unauthorized", "")


class NotionClient:
    """Thin async wrapper over the three Notion endpoints this connector needs.

    One client per call, built from the token resolved for that call's org, so a
    credential can never leak across orgs through a shared/cached client — the
    HubSpot precedent that Drive and Asana also follow.
    """

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _sleep_seconds(self, response: httpx.Response, attempt: int) -> float:
        """Honour `Retry-After`, else jittered exponential backoff capped at 30s.

        Notion publishes `Retry-After` on 429/529 and explicitly says to prefer it
        over a fixed wait, because the per-workspace budget is shared with every
        other integration on that workspace — so only the server knows how long
        the customer's whole workspace is actually throttled for.
        """
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), _BACKOFF_CAP_SECONDS)
            except ValueError:
                pass
        backoff = min(2.0**attempt, _BACKOFF_CAP_SECONDS)
        return backoff * (0.5 + random.random() / 2.0)  # jitter, per Notion's advice

    async def post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        """POST with retry on 429/529 ONLY. 5xx is not retried — see the module
        docstring: every verb here is a non-idempotent write."""
        last: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            response = await self._client.post(f"{_API_BASE}{path}", json=payload)
            if response.status_code not in _RETRYABLE_STATUS:
                return response
            last = response
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(self._sleep_seconds(response, attempt))
        assert last is not None
        return last

    async def patch(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        last: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            response = await self._client.patch(f"{_API_BASE}{path}", json=payload)
            if response.status_code not in _RETRYABLE_STATUS:
                return response
            last = response
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(self._sleep_seconds(response, attempt))
        assert last is not None
        return last


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _resolve_token(
    oauth: OAuthCredentialService, org_id: str, correlation_id: UUID
) -> str:
    """Guaranteed-fresh access token for this org's Notion grant."""
    from ...app.credentials.oauth import (
        GrantNotConnected,
        GrantRevoked,
        RefreshUnavailable,
    )

    try:
        return await oauth.access_token(
            org_id=org_id, provider=NOTION_PROVIDER, correlation_id=correlation_id
        )
    except (GrantNotConnected, GrantRevoked) as exc:
        raise ToolExecutionError(
            f"Notion is not connected for org {org_id!r} (or the connection was "
            "revoked). Reconnect Notion in integration settings before using this "
            "tool."
        ) from exc
    except RefreshUnavailable as exc:
        raise ToolExecutionError(
            f"Notion credentials could not be refreshed: {exc}"
        ) from exc


async def _check_response(
    response: httpx.Response,
    *,
    oauth: OAuthCredentialService,
    ctx: ToolContext,
    action: str,
) -> dict[str, Any]:
    """Return the decoded body, or raise — recording a revocation on the way out.

    THIS IS THE FUNCTION THAT CLOSES THE NULLABLE-EXPIRY GAP. Every Notion call in
    this module funnels through it, so there is exactly one place where a dead
    grant can be observed and exactly one place that records it. A new Notion verb
    added later inherits the wiring by construction rather than by remembering to
    copy it.

    The write to `connection_state` is one-directional and eventual, by design:
    this call still fails (the customer's action did not happen), and it is the
    NEXT gated tool call that reads the terminal state through the unchanged
    ToolProxy credential gate and denies with `ToolCredentialReconnectRequired`.
    Nothing here touches ToolProxy's dispatch flow, so `2448819`'s deny-by-default
    chokepoint keeps exactly the shape its tests pin.
    """
    if response.status_code in (200, 201):
        try:
            body = response.json()
        except ValueError:
            raise ToolExecutionError(
                f"Notion {action} returned a non-JSON success body"
            ) from None
        return body if isinstance(body, dict) else {}

    code, message = _error_fields(response)

    if _is_dead_grant(response.status_code, code):
        # Record the revocation BEFORE raising, so the state survives even though
        # this call is about to fail. Best-effort: if the write itself fails we
        # still deny the call rather than masking the auth failure behind a
        # bookkeeping error.
        try:
            await oauth.mark_revoked_by_provider(
                org_id=ctx.org_id,
                provider=NOTION_PROVIDER,
                reason=(
                    f"Notion returned {response.status_code} "
                    f"{code or 'unauthorized'} on {action}: {message}"
                ),
                correlation_id=ctx.correlation_id,
            )
        except Exception:  # noqa: BLE001 — see the comment above
            log.exception(
                "notion.revocation_write_failed",
                org_id=ctx.org_id, agent_id=ctx.agent_id, action=action,
            )
        log.warning(
            "notion.grant_revoked_by_api_call",
            org_id=ctx.org_id, agent_id=ctx.agent_id, action=action, code=code,
        )
        raise ToolExecutionError(
            f"Notion rejected the credentials for org {ctx.org_id!r} "
            f"({response.status_code} {code or 'unauthorized'}). The connection "
            "has been marked as needing reconnection; reconnect Notion in "
            "integration settings."
        )

    if response.status_code == 429:
        raise ToolExecutionError(
            f"Notion {action} was rate limited after {_MAX_ATTEMPTS} attempts. "
            "Notion's per-workspace budget is shared with every integration on "
            f"that workspace, so this may not be Skylize's traffic: {message}"
        )

    raise ToolExecutionError(
        f"Notion {action} failed ({response.status_code}"
        f"{' ' + code if code else ''}): {message}"
    )


def _parent(inp: NotionCreatePageIn) -> dict[str, str]:
    if inp.parent_database_id:
        return {"database_id": inp.parent_database_id}
    assert inp.parent_page_id is not None  # validator guarantees exactly one
    return {"page_id": inp.parent_page_id}


# ---------------------------------------------------------------------------
# Tool builders — all ROUTINE (Q2.7b); Notion exposes no elevated action
# ---------------------------------------------------------------------------

def build_notion_create_page_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """Page creation — ROUTINE (Q2.7b).

    Not permission-gated, and that is a finding rather than an omission: the page
    lands inside a workspace location a human already shared with the
    integration, and Notion's API cannot widen who can see it.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, NotionCreatePageIn)
        payload: dict[str, Any] = {
            "parent": _parent(inp),
            "properties": {"title": {"title": _rich_text(inp.title)}},
        }
        if inp.paragraphs:
            payload["children"] = _paragraph_blocks(inp.paragraphs)

        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = NotionClient(token)
        try:
            response = await client.post("/pages", payload)
        finally:
            await client.aclose()

        body = await _check_response(
            response, oauth=oauth, ctx=ctx, action="page creation"
        )
        page_id = body.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise ToolExecutionError(
                "Notion page creation returned no page id; refusing to report a "
                "page that cannot be identified"
            )
        log.info(
            "notion.page_created",
            org_id=ctx.org_id, agent_id=ctx.agent_id, page_id=page_id,
        )
        return NotionCreatePageOut(
            page_id=page_id, title=inp.title, url=body.get("url"),
        )

    return ToolDefinition(
        tool_id="integration.notion_create_page",
        name="Create a Notion page",
        description=(
            "Create a page in the organization's connected Notion workspace, "
            "under a page or in a database that has been shared with Skylize."
        ),
        input_schema=NotionCreatePageIn,
        output_schema=NotionCreatePageOut,
        category="integration",
        handler=handler,
        oauth={"provider": NOTION_PROVIDER},
    )


def build_notion_create_database_tool(
    oauth: OAuthCredentialService,
) -> ToolDefinition:
    """Database creation — ROUTINE (Q2.7b). Structure, not access."""

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, NotionCreateDatabaseIn)
        properties: dict[str, Any] = {"Name": {"title": {}}}
        for name in inp.text_properties:
            if name != "Name":
                properties[name] = {"rich_text": {}}

        payload = {
            "parent": {"type": "page_id", "page_id": inp.parent_page_id},
            "title": _rich_text(inp.title),
            "properties": properties,
        }

        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = NotionClient(token)
        try:
            response = await client.post("/databases", payload)
        finally:
            await client.aclose()

        body = await _check_response(
            response, oauth=oauth, ctx=ctx, action="database creation"
        )
        database_id = body.get("id")
        if not isinstance(database_id, str) or not database_id:
            raise ToolExecutionError(
                "Notion database creation returned no database id; refusing to "
                "report a database that cannot be identified"
            )
        log.info(
            "notion.database_created",
            org_id=ctx.org_id, agent_id=ctx.agent_id, database_id=database_id,
        )
        return NotionCreateDatabaseOut(
            database_id=database_id, title=inp.title, url=body.get("url"),
        )

    return ToolDefinition(
        tool_id="integration.notion_create_database",
        name="Create a Notion database",
        description=(
            "Create a database under a Notion page shared with Skylize. Creating "
            "a database grants no one access to it."
        ),
        input_schema=NotionCreateDatabaseIn,
        output_schema=NotionCreateDatabaseOut,
        category="integration",
        handler=handler,
        oauth={"provider": NOTION_PROVIDER},
    )


def build_notion_append_blocks_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """Content append — ROUTINE (Q2.7b).

    Appends paragraphs to an existing page or block. Content, not access: it
    changes what a page says, never who can read it.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, NotionAppendBlocksIn)
        children = _paragraph_blocks(inp.paragraphs)

        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = NotionClient(token)
        try:
            response = await client.patch(
                f"/blocks/{inp.block_id}/children", {"children": children}
            )
        finally:
            await client.aclose()

        await _check_response(
            response, oauth=oauth, ctx=ctx, action="content append"
        )
        log.info(
            "notion.blocks_appended",
            org_id=ctx.org_id, agent_id=ctx.agent_id,
            block_id=inp.block_id, count=len(children),
        )
        return NotionAppendBlocksOut(
            block_id=inp.block_id, appended=len(children)
        )

    return ToolDefinition(
        tool_id="integration.notion_append_blocks",
        name="Append content to a Notion page",
        description=(
            "Append paragraphs of text to a Notion page or block shared with "
            "Skylize. Changes what the page says, never who can see it."
        ),
        input_schema=NotionAppendBlocksIn,
        output_schema=NotionAppendBlocksOut,
        category="integration",
        handler=handler,
        oauth={"provider": NOTION_PROVIDER},
    )
