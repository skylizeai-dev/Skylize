"""The Asana connector — task/project creation plus governed project membership.

Three tools (integration_inputs.md 2.6, `[DRAFT]` 2026-09-02):
  * `integration.asana_create_task`         — routine (Q2.6b)
  * `integration.asana_create_project`      — routine (Q2.6b)
  * `integration.asana_add_project_member`  — HIGH-RISK, permission-gated (Q2.6b/d)

WORKSPACE-LEVEL INVITATION (`addUser`) IS DELIBERATELY NOT BUILT. Live
verification found no published Asana granular scope maps to
`POST /workspaces/{gid}/addUser` — there is no `workspaces:write`, and the
endpoint appears reachable only under "Full permissions" (every endpoint, for
every connected customer). That is disproportionate to the minimal-scope
philosophy this platform follows (Drive's `drive.file`, this connector's
`tasks:write projects:write`). Owner decision, 2.6 Q2.6a: out of scope. An
earlier pass built and gated the tool anyway to make the blocked state visible;
it is removed here rather than left half-shipped.

Org-level: every call resolves the calling org's own OAuth grant through the
provider-agnostic infrastructure from f6360b4. This module contains NO OAuth logic
of its own, no token refresh, and no client-secret handling — Asana's entire OAuth
surface is `app/credentials/asana_provider.py`, which required zero changes to the
shared primitive.

WHY RAW `httpx` AND NOT AN ASANA SDK. Same three reasons Drive gave
(`drive_tools.py:20-33`), re-checked for Asana: `pyproject.toml` carries no Asana
dependency; Asana publishes no official async Python client, and a sync one would
block the request event loop every handler runs on; and any SDK bringing its own
auth handling would bypass the RLS-scoped, audited, `connection_state`-tracking
refresh primitive in `app/credentials/oauth.py`. Two HTTP client libraries over one
credential store is precisely the duplication 2.6 forbids.

THE MEMBERSHIP HANDLER REFUSES WITHOUT AN AUTHORIZATION, and sends what the gate
approved rather than what the input asked for. That is the `2448819` chokepoint
pattern, reused verbatim rather than reinvented: `ToolPermissionProfile` is opt-in
per tool, so a membership tool registered without the profile would otherwise reach
its handler having skipped authorization entirely. It arrives with
`ctx.permission_grant is None` and is refused here. Deny-by-default is therefore a
data dependency the handler cannot satisfy on its own.

ONE GRANTEE PER CALL, DELIBERATELY. Asana's `addMembers` accepts an ARRAY of member
identifiers; this connector exposes exactly one. The gate authorizes one grantee
against one allow-list row and returns one `PermissionGrant`, so accepting an array
would let a single authorization cover recipients the gate never saw — and would
make the audit line ambiguous about who actually received access. The array is
constructed at the wire boundary from the single AUTHORIZED grantee.

NO ROLE AXIS (Q2.6e). Asana membership carries no access level. Rather than relax
`org_permission_grants`' `max_role` CHECK — which would create a role-less escape
hatch any future provider could use to skip the rank comparison — the membership
schema pins `role: Literal["writer"] = "writer"`. The gate reads the field off the
validated input (`proxy.py:435`); the type makes it unforgeable and `extra="forbid"`
prevents smuggling an alternative. `writer` is the honest rank for a grant that
confers full participation.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ...app.credentials.asana_provider import ASANA_PROVIDER
from ...app.credentials.oauth import OAuthCredentialService
from ..base import (
    PermissionGrant,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolPermissionUnavailable,
)

log = structlog.get_logger()

#: The action class keying this connector's rows in `org_permission_grants`.
ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS = "asana.project.add_members"

_API_BASE = "https://app.asana.com/api/1.0"

#: Retried statuses. 429 is Asana's documented rate-limit signal and 5xx are
#: transient. `[LIVE-VERIFIED]` 2026-09-02, developers.asana.com/docs/rate-limits:
#: 150 req/min free / 1,500 paid, 15 concurrent writes, cost-based accounting on
#: top, and a `Retry-After` header the docs say to prefer over a fixed wait. This
#: retry shape (bounded attempts, exponential backoff, `reraise=True`) mirrors the
#: Drive and HubSpot precedents; it does NOT read `Retry-After`, which is a known
#: and deliberate simplification carried from those connectors rather than a new
#: gap introduced here.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

#: Grantee values this connector refuses even when the gate authorized them.
#: Both name no external person, so an address allow-list cannot meaningfully
#: govern either, and both would make the audit row read as a grant to a literal
#: string rather than to a recipient.
#:   * 'me'     — Asana's own sentinel for the authorizing user.
#:   * 'anyone' — `ToolPermissionProfile.link_sharing_sentinel`'s default. Asana
#:                membership has NO link-grant concept, so if an operator ever set
#:                `allow_link_sharing` on an Asana action class the gate would
#:                authorize 'anyone' and this connector would otherwise post the
#:                literal string to Asana. Refused here rather than left to fail
#:                confusingly at the provider.
_REFUSED_GRANTEES = frozenset({"me", "anyone"})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AsanaCreateTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=1024, description="Task name.")
    workspace_gid: str = Field(
        min_length=1, description="Gid of the Asana workspace to create the task in."
    )
    notes: str = Field(
        default="", max_length=65_536, description="Free-form task description."
    )
    project_gids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Optional project gids to add the task to.",
    )
    assignee: str | None = Field(
        default=None,
        description=(
            "Optional assignee: a user gid or email already in the workspace. "
            "Assignment does not grant access, so it is not permission-gated."
        ),
    )
    due_on: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Optional due date, YYYY-MM-DD.",
    )


class AsanaCreateTaskOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_gid: str
    name: str
    permalink_url: str | None = None


class AsanaCreateProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=1024, description="Project name.")
    workspace_gid: str = Field(
        min_length=1, description="Gid of the Asana workspace to create the project in."
    )
    notes: str = Field(
        default="", max_length=65_536, description="Free-form project description."
    )
    team_gid: str | None = Field(
        default=None,
        description=(
            "Team gid. Required by Asana when the workspace is an organization; "
            "optional otherwise."
        ),
    )


class AsanaCreateProjectOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_gid: str
    name: str
    permalink_url: str | None = None


class AsanaAddProjectMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_gid: str = Field(min_length=1, description="Project to add the member to.")
    grantee: str = Field(
        min_length=1,
        description=(
            "Email address or user gid of the ONE person to add. Must be "
            "pre-authorized by the organization; unauthorized recipients are refused."
        ),
    )
    #: Pinned by type, not chosen by the agent. See the module docstring and 2.6
    #: Q2.6e: Asana has no role axis, and the schema's CHECK constraint is not
    #: relaxed to accommodate that.
    role: Literal["writer"] = Field(
        default="writer",
        description=(
            "Always 'writer'. Asana project membership confers full participation "
            "and has no access-level parameter; this is fixed, not selectable."
        ),
    )


class AsanaAddProjectMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_gid: str
    grantee: str
    role: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AsanaClient:
    """Thin async wrapper over the three Asana endpoints this connector needs.

    One client per call, built from the token resolved for that call's org, so a
    credential can never leak across orgs through a shared/cached client — the
    HubSpot precedent (`hubspot_tools.py:76-83`) that Drive also follows.

    Every Asana request and response body is wrapped in a top-level `data` envelope
    (`[LIVE-VERIFIED]` 2026-09-02 against developers.asana.com); `_post` owns that
    convention so no individual verb has to remember it.
    """

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, data: dict[str, Any]) -> httpx.Response:
        response = await self._client.post(f"{_API_BASE}{path}", json={"data": data})
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        return response

    async def create_task(
        self,
        *,
        name: str,
        workspace_gid: str,
        notes: str,
        project_gids: list[str],
        assignee: str | None,
        due_on: str | None,
    ) -> httpx.Response:
        payload: dict[str, Any] = {"name": name, "workspace": workspace_gid}
        if notes:
            payload["notes"] = notes
        if project_gids:
            payload["projects"] = project_gids
        if assignee:
            payload["assignee"] = assignee
        if due_on:
            payload["due_on"] = due_on
        return await self._post("/tasks", payload)

    async def create_project(
        self, *, name: str, workspace_gid: str, notes: str, team_gid: str | None
    ) -> httpx.Response:
        payload: dict[str, Any] = {"name": name, "workspace": workspace_gid}
        if notes:
            payload["notes"] = notes
        if team_gid:
            payload["team"] = team_gid
        return await self._post("/projects", payload)

    async def add_project_member(
        self, *, project_gid: str, grantee: str
    ) -> httpx.Response:
        """One grantee, wrapped into Asana's array at the wire boundary only."""
        return await self._post(
            f"/projects/{project_gid}/addMembers", {"members": [grantee]}
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _resolve_token(
    oauth: OAuthCredentialService, org_id: str, correlation_id: UUID
) -> str:
    """Guaranteed-fresh access token for this org's Asana grant.

    The ToolProxy OAuth stage has already ensured a live grant before dispatch; this
    re-resolves per call rather than caching, so a rotation or revocation between the
    gate and the call takes effect immediately.
    """
    from ...app.credentials.oauth import GrantNotConnected, GrantRevoked, RefreshUnavailable

    try:
        return await oauth.access_token(
            org_id=org_id, provider=ASANA_PROVIDER, correlation_id=correlation_id
        )
    except (GrantNotConnected, GrantRevoked) as exc:
        raise ToolExecutionError(
            f"Asana is not connected for org {org_id!r} (or the connection was "
            "revoked). Reconnect Asana in integration settings before using this tool."
        ) from exc
    except RefreshUnavailable as exc:
        raise ToolExecutionError(
            f"Asana credentials could not be refreshed: {exc}"
        ) from exc


def _asana_error(response: httpx.Response, action: str) -> ToolExecutionError:
    """Normalize an Asana API error into a clean tool error, never a raw 500.

    Asana's REST envelope is `{"errors":[{"message": ...}]}` — a different shape from
    Drive's `{"error": {"message": ...}}`, and different again from the RFC 6749
    shape its TOKEN endpoint uses (see `asana_provider.py`). All three are handled in
    their own place; none is assumed to look like another.
    """
    message = response.text[:200]
    try:
        payload = response.json()
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                parts = [
                    e["message"]
                    for e in errors
                    if isinstance(e, dict) and isinstance(e.get("message"), str)
                ]
                if parts:
                    message = "; ".join(parts)
    except ValueError:
        pass
    return ToolExecutionError(
        f"Asana {action} failed ({response.status_code}): {message}"
    )


def _exhausted(exc: httpx.HTTPStatusError, action: str) -> ToolExecutionError:
    """Retries ran out. Degrade to a clean tool error rather than a raw httpx one.

    `AsanaClient._post` re-raises a retryable status so tenacity can back off; when
    the bounded attempts are exhausted `reraise=True` lets the `HTTPStatusError`
    escape. Asana's published limits make 429 a realistic steady-state outcome
    (150 req/min on free domains, plus cost-based accounting), so this is a
    routine path, not an exotic one, and it must not surface as a 500.
    """
    return _asana_error(exc.response, f"{action} (retries exhausted)")


def _data(response: httpx.Response) -> dict[str, Any]:
    """The `data` object out of an Asana success envelope, or `{}`."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _require_grant(
    ctx: ToolContext, tool_id: str, action_class: str
) -> PermissionGrant:
    """The deny-by-default backstop for the membership handler.

    Mirrors `drive_tools.py:338-356` exactly. Two refusals, both fail-closed:
      * no grant at all  -> the tool was registered without a `ToolPermissionProfile`
                            and the gate never ran. A misconfiguration, not a denial.
      * wrong action class -> a grant for a DIFFERENT elevated action must never
                            authorize this one.
    """
    grant = ctx.permission_grant
    if grant is None:
        raise ToolPermissionUnavailable(
            f"{tool_id} was dispatched without a PermissionGrant: the "
            "elevated-action gate did not run for this call. Refusing to grant "
            "access. This means the tool was registered without a "
            "ToolPermissionProfile — a misconfiguration, not a denial."
        )
    if grant.action_class != action_class:
        raise ToolPermissionUnavailable(
            f"PermissionGrant authorizes {grant.action_class!r}, not "
            f"{action_class!r}; refusing to grant access"
        )
    if grant.grantee.strip().lower() in _REFUSED_GRANTEES:
        raise ToolPermissionUnavailable(
            f"{grant.grantee!r} is not a grantee this connector will act on: it "
            "names no external party, so an address allow-list cannot meaningfully "
            "authorize it"
        )
    return grant


# ---------------------------------------------------------------------------
# Tool builders — routine verbs
# ---------------------------------------------------------------------------

def build_asana_create_task_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """Task creation — a ROUTINE verb (Q2.6b).

    Not permission-gated, and that is a decision rather than an omission: the task
    stays inside a workspace the customer already controls, under their own retention
    and access rules. Nothing is handed to a party the agent chooses. Same split
    Drive draws between `files.create` and `permissions.create`.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, AsanaCreateTaskIn)
        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = AsanaClient(token)
        try:
            response = await client.create_task(
                name=inp.name,
                workspace_gid=inp.workspace_gid,
                notes=inp.notes,
                project_gids=inp.project_gids,
                assignee=inp.assignee,
                due_on=inp.due_on,
            )
        except httpx.HTTPStatusError as exc:
            raise _exhausted(exc, "task creation") from exc
        finally:
            await client.aclose()

        if response.status_code not in (200, 201):
            raise _asana_error(response, "task creation")
        body = _data(response)
        task_gid = body.get("gid")
        if not isinstance(task_gid, str) or not task_gid:
            raise ToolExecutionError(
                "Asana task creation returned no task gid; refusing to report a "
                "task that cannot be identified"
            )
        log.info(
            "asana.task_created",
            org_id=ctx.org_id, agent_id=ctx.agent_id, task_gid=task_gid,
        )
        return AsanaCreateTaskOut(
            task_gid=task_gid,
            name=body.get("name") or inp.name,
            permalink_url=body.get("permalink_url"),
        )

    return ToolDefinition(
        tool_id="integration.asana_create_task",
        name="Create an Asana task",
        description=(
            "Create a task in the organization's connected Asana workspace, "
            "optionally in named projects, with an optional assignee and due date."
        ),
        input_schema=AsanaCreateTaskIn,
        output_schema=AsanaCreateTaskOut,
        category="integration",
        handler=handler,
        oauth={"provider": ASANA_PROVIDER},
    )


def build_asana_create_project_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """Project creation — a ROUTINE verb (Q2.6b). Structure, not access."""

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, AsanaCreateProjectIn)
        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = AsanaClient(token)
        try:
            response = await client.create_project(
                name=inp.name,
                workspace_gid=inp.workspace_gid,
                notes=inp.notes,
                team_gid=inp.team_gid,
            )
        except httpx.HTTPStatusError as exc:
            raise _exhausted(exc, "project creation") from exc
        finally:
            await client.aclose()

        if response.status_code not in (200, 201):
            raise _asana_error(response, "project creation")
        body = _data(response)
        project_gid = body.get("gid")
        if not isinstance(project_gid, str) or not project_gid:
            raise ToolExecutionError(
                "Asana project creation returned no project gid; refusing to report "
                "a project that cannot be identified"
            )
        log.info(
            "asana.project_created",
            org_id=ctx.org_id, agent_id=ctx.agent_id, project_gid=project_gid,
        )
        return AsanaCreateProjectOut(
            project_gid=project_gid,
            name=body.get("name") or inp.name,
            permalink_url=body.get("permalink_url"),
        )

    return ToolDefinition(
        tool_id="integration.asana_create_project",
        name="Create an Asana project",
        description=(
            "Create a project in the organization's connected Asana workspace. "
            "Creating a project grants no one access to it."
        ),
        input_schema=AsanaCreateProjectIn,
        output_schema=AsanaCreateProjectOut,
        category="integration",
        handler=handler,
        oauth={"provider": ASANA_PROVIDER},
    )


# ---------------------------------------------------------------------------
# Tool builder — HIGH-RISK verb, permission-gated
# ---------------------------------------------------------------------------

def build_asana_add_project_member_tool(
    oauth: OAuthCredentialService,
) -> ToolDefinition:
    """Project membership — a HIGH-RISK verb (Q2.6b), gated by ToolPermissionProfile.

    This is Asana's direct analogue of Drive's `permissions.create`: the `members`
    field accepts an EMAIL, so the agent picks an external party at run time and
    hands them access to a customer's project. Same gate, same allow-list, same
    execute-what-was-authorized discipline.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, AsanaAddProjectMemberIn)
        grant = _require_grant(
            ctx,
            "integration.asana_add_project_member",
            ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
        )

        # Send what the gate AUTHORIZED, not what the input asked for. They are
        # normally identical; using the grant makes it impossible for the executed
        # membership change to drift from the approved one.
        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = AsanaClient(token)
        try:
            response = await client.add_project_member(
                project_gid=inp.project_gid, grantee=grant.grantee
            )
        except httpx.HTTPStatusError as exc:
            raise _exhausted(exc, "project member addition") from exc
        finally:
            await client.aclose()

        if response.status_code not in (200, 201):
            raise _asana_error(response, "project member addition")
        log.info(
            "asana.project_member_added",
            org_id=ctx.org_id, agent_id=ctx.agent_id,
            project_gid=inp.project_gid, grantee=grant.grantee,
            role=grant.role, matched_pattern=grant.matched_pattern,
        )
        return AsanaAddProjectMemberOut(
            project_gid=inp.project_gid, grantee=grant.grantee, role=grant.role
        )

    return ToolDefinition(
        tool_id="integration.asana_add_project_member",
        name="Add a member to an Asana project",
        description=(
            "Grant one named person access to an Asana project. The recipient must "
            "be pre-authorized by the organization; unauthorized recipients are "
            "refused. Adding a member may also make them a follower."
        ),
        input_schema=AsanaAddProjectMemberIn,
        output_schema=AsanaAddProjectMemberOut,
        category="integration",
        handler=handler,
        oauth={"provider": ASANA_PROVIDER},
        permission={
            "action_class": ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
            "grantee_field": "grantee",
            "role_field": "role",
        },
    )
