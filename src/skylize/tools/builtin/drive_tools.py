"""integration.drive_create_file / integration.drive_share_file.

The Google Drive connector (integration_inputs.md 2.5, `[APPROVED]` 2026-08-31).
Org-level: every call resolves the calling org's own OAuth grant through the
provider-agnostic infrastructure from f6360b4 — this module contains NO OAuth
logic of its own, no token refresh, and no client-secret handling.

SCOPE IS `drive.file` AND ONLY `drive.file` (Q2.5a, verified 2026-08-29 against
`https://developers.google.com/workspace/drive/api/guides/api-specific-auth`:
non-sensitive/recommended, basic verification, no CASA). `drive.file` reaches only
files this app created or that a user explicitly opened with it — which is exactly
the deliverable-teslimi narrative (Q2.5c) and nothing wider. Two consequences a
future author must not "fix":
  * There is no listing, browsing, or search tool here. `drive.file` cannot see
    the customer's other files, and adding a scope that could would move the app
    into Google's Restricted tier and trigger a recurring CASA assessment.
  * There is no deletion tool, no Shared-Drive support, and no push-notification
    channel. All three are explicitly out of scope per Q2.5e.

WHY RAW `httpx` AND NOT `google-api-python-client`. Deliberate, and it is the
established precedent here (`hubspot_tools.py:76`, "Thin wrapper over HubSpot's
REST API — no SDK dependency"). Three reasons:
  1. `google-api-python-client` is SYNCHRONOUS (httplib2). Every tool handler in
     this codebase is `async` and runs on the request event loop; a sync client
     would block it on every Drive call. Google publishes no official async Drive
     client.
  2. `google-auth` would bring its own token-refresh path, bypassing the
     RLS-scoped, audited, `connection_state`-tracking refresh primitive in
     `app/credentials/oauth.py`. Two refresh mechanisms over one credential store
     is precisely the duplication 2.5 forbids.
  3. The surface actually needed is two endpoints.

THE SHARING HANDLER REFUSES WITHOUT AN AUTHORIZATION. `_share_handler` requires
`ctx.permission_grant` and raises if it is absent. That is the deny-by-default
backstop for the opt-in `ToolPermissionProfile`: a sharing tool registered without
the profile never runs the gate, arrives with `permission_grant=None`, and fails
closed here instead of dispatching an ungated share. It also sends the AUTHORIZED
grantee and role rather than the raw input, so what executes is what the gate
approved and cannot drift from it.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ...app.credentials.oauth import OAuthCredentialService
from ..base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolPermissionUnavailable,
)

log = structlog.get_logger()

#: The provider key under which the org's Drive grant is stored in
#: `oauth_credentials` and registered with `OAuthCredentialService`.
DRIVE_PROVIDER = "google_drive"

#: The action class keying this connector's rows in `org_permission_grants`.
DRIVE_SHARE_ACTION_CLASS = "drive.permissions.create"

_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"

#: Retried statuses. 429 is Drive's rate-limit signal and 5xx are transient.
#: CONSERVATIVE DEFAULTS: Google's *current* published per-project and per-user
#: quota numbers were NOT fetched in this pass, so nothing here encodes a specific
#: requests-per-second figure invented from memory. The shape (retry 429/5xx,
#: exponential backoff, small bounded attempt count, `reraise=True`) mirrors the
#: HubSpot precedent (`hubspot_tools.py:95-100`).
#:
#: AN EARLIER REVISION OF THIS COMMENT CLAIMED RETRYING WAS SAFE "because each is
#: attempted at most once per call and a failed attempt performs no partial write".
#: THAT WAS FALSE, and it is the bug this module now fixes. The case it missed is a
#: 5xx or a dropped connection AFTER Drive has already committed the write: the
#: attempt performed a COMPLETE write and merely lost the response, so a blind
#: retry created a SECOND file. Recorded as gap D.5 in
#: `docs/audits/audit_gdrive_readiness.md:392`, and named from the outside by
#: `notion_tools.py:71-79`, which declined to inherit it.
#:
#: What changed, per verb:
#:   * `create_file` now carries a PRE-GENERATED file id, so DRIVE itself rejects a
#:     duplicate with 409 — see `_PREGENERATED_ID_DOC` and `create_file`. This verb
#:     is fixed.
#:   * `create_permission` retries UNCHANGED, and its residual risk is stated rather
#:     than papered over. `[UNVERIFIED]` 2026-09-09: Google does NOT document what a
#:     repeated identical `permissions.create` does — whether it adds a second
#:     permission entry or updates the one that exists. The reference page documents
#:     only that "Concurrent permissions operations on the same file aren't
#:     supported; only the last update is applied", which is a statement about
#:     concurrency, not about duplicate-grant semantics, and it must not be read as
#:     an idempotency guarantee it does not make.
#:
#:     WHY THAT IS TOLERATED HERE, deliberately and narrowly: the worst case is a
#:     SECOND permission row granting THE SAME principal THE SAME role that the
#:     governance gate already authorized. It cannot widen access beyond the grant —
#:     `_share_handler` sends `grant.grantee`/`grant.role`, never the raw input — so
#:     a duplicate is untidy, not a confidentiality event. Contrast `create_file`,
#:     where a duplicate is a second real document delivered to a client. Closing
#:     this would need a `permissions.list` read-before-write, which `drive.file`
#:     can perform but which costs a round trip on every share to remove an effect
#:     with no security consequence. Left open ON PURPOSE, and recorded so a future
#:     author decides it with the same facts rather than rediscovering them.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

#: Google's own statement of the guarantee `create_file` relies on
#: (`https://developers.google.com/workspace/drive/api/guides/manage-uploads`,
#: `[LIVE-VERIFIED]` 2026-09-09): "You can safely retry uploads with pre-generated
#: IDs if there's an indeterminate server error or timeout", and "If the file
#: action is successful, subsequent retries return a `409 Conflict` HTTP status
#: code response and duplicate files aren't created."
#:
#: WHY PRE-GENERATED IDS AND NOT A RESUMABLE UPLOAD SESSION. Both are real Drive
#: mechanisms and both were evaluated. A resumable session solves a DIFFERENT
#: problem — an interrupted transfer of a large body, resumed by querying
#: `Content-Range: bytes */size` against a session URI. This connector posts small
#: UTF-8 text documents in a single multipart request; the body is never the thing
#: that fails halfway. A session would add a second round trip per call and a
#: week-long server-side session to every file creation, and would still not by
#: itself stop a re-initiated session from producing a second file. The
#: pre-generated id addresses the actual failure mode — a lost RESPONSE to a
#: completed write — at one extra request, and makes DRIVE the arbiter of
#: duplication rather than any state this process would have to keep.
#:
#: LIMIT, carried honestly: Google excludes Workspace-format creation (other than
#: `drive-sdk` and `folder` MIME types) and conversions from this guarantee. This
#: connector creates neither — it writes `text/plain` / `text/markdown` bodies
#: (`DriveCreateFileIn.mime_type`) — so the exclusion does not reach it. A future
#: author adding Workspace-format conversion here must revisit this.
_PREGENERATED_ID_DOC = "https://developers.google.com/workspace/drive/api/guides/manage-uploads"

_GENERATE_IDS_URL = "https://www.googleapis.com/drive/v3/files/generateIds"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DriveCreateFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=512, description="File name in Drive.")
    content: str = Field(description="UTF-8 text content of the file.")
    mime_type: str = Field(
        default="text/plain",
        description="MIME type to store the file as, e.g. text/plain or text/markdown.",
    )
    parent_folder_id: str | None = Field(
        default=None,
        description=(
            "Optional Drive folder id to create the file in. Must be a folder the "
            "app created or the user explicitly opened with it (drive.file scope)."
        ),
    )


class DriveCreateFileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str
    name: str
    web_view_link: str | None = None


class DriveShareFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=1, description="Drive file id to share.")
    grantee: str = Field(
        min_length=1,
        description=(
            "Email address of the recipient, or the literal 'anyone' to create a "
            "link grant. Must be pre-authorized by the org."
        ),
    )
    role: Literal["reader", "commenter", "writer"] = Field(
        default="reader",
        description="Access level to grant. Must not exceed the org's authorized maximum.",
    )


class DriveShareFileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_id: str
    file_id: str
    grantee: str
    role: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class DriveClient:
    """Thin async wrapper over the two Drive v3 endpoints this connector needs.

    One client per call, built from the token resolved for that call's org, so a
    credential can never leak across orgs through a shared/cached client — the
    HubSpot precedent (`hubspot_tools.py:76-83`).
    """

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def generate_id(self) -> str:
        """Reserve ONE file id from Drive, to be spent by the next `create_file`.

        Retryable in its own right and safely so: this endpoint reserves ids and
        performs no write, so a lost response costs an unused id and nothing else.
        Unused ids are not billed, not visible to the customer, and expire on their
        own — the failure mode of retrying here is waste, not duplication.
        """
        response = await self._client.post(
            _GENERATE_IDS_URL, params={"count": "1", "space": "drive"}
        )
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        if response.status_code != 200:
            raise _drive_error(response, "file id generation")
        try:
            ids = response.json().get("ids")
        except ValueError:
            ids = None
        if not isinstance(ids, list) or not ids or not isinstance(ids[0], str):
            raise ToolExecutionError(
                "Google Drive returned no pre-generated file id; refusing to create "
                "a file without one, because an un-keyed create cannot be retried "
                "safely (a lost response would duplicate the file)"
            )
        return ids[0]

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_file(
        self,
        *,
        name: str,
        content: str,
        mime_type: str,
        parent_folder_id: str | None,
        file_id: str,
    ) -> httpx.Response:
        """Multipart upload: one request carrying metadata + content.

        Built by hand rather than with `httpx`'s `files=` helper because Drive
        requires `multipart/related` with a JSON first part, which the standard
        form-data encoder does not produce.

        `file_id` is the PRE-GENERATED id from `generate_id`, sent as the metadata
        `id`. It is what makes the surrounding `@retry` safe: this coroutine is the
        unit tenacity re-invokes, so every attempt — including the one after a 5xx
        that Drive had already committed — carries the SAME id, and Drive answers
        the second one with 409 instead of creating another file. Passing a fresh
        id per attempt, or generating it inside this method, would silently restore
        the duplication bug; generate it once in the handler and pass it in.
        """
        metadata: dict[str, Any] = {"name": name, "id": file_id}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]

        boundary = "skylize-drive-boundary"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}; charset=UTF-8\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--"
        ).encode("utf-8")

        response = await self._client.post(
            _UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        return response

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_permission(
        self, *, file_id: str, grantee: str, role: str, is_link_grant: bool
    ) -> httpx.Response:
        payload: dict[str, Any] = {"role": role}
        if is_link_grant:
            payload["type"] = "anyone"
        else:
            payload["type"] = "user"
            payload["emailAddress"] = grantee

        response = await self._client.post(
            f"{_FILES_URL}/{file_id}/permissions",
            params={"fields": "id"},
            json=payload,
        )
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        return response


async def _resolve_token(
    oauth: OAuthCredentialService, org_id: str, correlation_id: UUID
) -> str:
    """Guaranteed-fresh access token for this org's Drive grant.

    The ToolProxy OAuth stage has already ensured a live grant before dispatch;
    this re-resolves per call rather than caching, so a rotation or revocation
    between the gate and the call takes effect immediately.
    """
    from ...app.credentials.oauth import GrantNotConnected, GrantRevoked, RefreshUnavailable

    try:
        return await oauth.access_token(
            org_id=org_id, provider=DRIVE_PROVIDER, correlation_id=correlation_id
        )
    except (GrantNotConnected, GrantRevoked) as exc:
        raise ToolExecutionError(
            f"Google Drive is not connected for org {org_id!r} (or the connection "
            "was revoked). Reconnect Drive in integration settings before using "
            "this tool."
        ) from exc
    except RefreshUnavailable as exc:
        raise ToolExecutionError(
            f"Google Drive credentials could not be refreshed: {exc}"
        ) from exc


def _drive_error(response: httpx.Response, action: str) -> ToolExecutionError:
    """Normalize a Drive API error into a clean tool error, never a raw 500."""
    try:
        payload = response.json()
        message = payload.get("error", {}).get("message", response.text[:200])
    except ValueError:
        message = response.text[:200]
    return ToolExecutionError(
        f"Google Drive {action} failed ({response.status_code}): {message}"
    )


# ---------------------------------------------------------------------------
# Tool builders
# ---------------------------------------------------------------------------

def build_drive_create_file_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """File creation — the ROUTINE verb (Q2.5b).

    Declares `oauth` but deliberately NO `permission` profile: the file stays
    inside the customer's own Drive, under their retention and access controls.
    Nothing leaves their custody, so there is no recipient to pre-authorize.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, DriveCreateFileIn)
        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = DriveClient(token)
        try:
            # Reserve the id ONCE, outside the create's retry loop. Every attempt
            # then spends the same id, which is the whole basis of the 409 below.
            file_id = await client.generate_id()
            response = await client.create_file(
                name=inp.name, content=inp.content,
                mime_type=inp.mime_type, parent_folder_id=inp.parent_folder_id,
                file_id=file_id,
            )
        except httpx.HTTPStatusError as exc:
            raise _drive_error(exc.response, "file creation (retries exhausted)") from exc
        finally:
            await client.aclose()

        # 409 IS A SUCCESS, and this branch is the point of the whole change.
        # Drive returns it when the pre-generated id is already taken — which, since
        # this id was minted moments ago for this call alone, can only mean an
        # EARLIER ATTEMPT OF THIS SAME CALL already committed the file and lost its
        # response to a 5xx or a dropped connection. Reporting the id we reserved is
        # therefore accurate: that file exists, and there is exactly one of it.
        # Treating 409 as an error would leave the caller believing the delivery
        # failed and inviting the very duplicate this avoids.
        if response.status_code == 409:
            log.info(
                "drive.file_create_deduplicated",
                org_id=ctx.org_id, agent_id=ctx.agent_id, file_id=file_id,
                detail=(
                    "409 on a pre-generated id: an earlier attempt of this call "
                    "already created the file; no duplicate was created"
                ),
            )
            return DriveCreateFileOut(file_id=file_id, name=inp.name, web_view_link=None)

        if response.status_code not in (200, 201):
            raise _drive_error(response, "file creation")
        body = response.json()
        log.info(
            "drive.file_created",
            org_id=ctx.org_id, agent_id=ctx.agent_id, file_id=body.get("id", file_id),
        )
        return DriveCreateFileOut(
            file_id=body.get("id") or file_id,
            name=body.get("name", inp.name),
            web_view_link=body.get("webViewLink"),
        )

    return ToolDefinition(
        tool_id="integration.drive_create_file",
        name="Create a Google Drive file",
        description=(
            "Create a text file in the connected Google Drive account and return "
            "its file id and link. Use to deliver a finished work product to a "
            "client. Only reaches files this application created."
        ),
        input_schema=DriveCreateFileIn,
        output_schema=DriveCreateFileOut,
        category="integration",
        handler=handler,
        oauth={"provider": DRIVE_PROVIDER},
    )


def build_drive_share_file_tool(oauth: OAuthCredentialService) -> ToolDefinition:
    """Sharing — the HIGH-SEVERITY verb (Q2.5b), gated by ToolPermissionProfile.

    This is the action where data leaves Skylize's custody to a party the agent
    chooses at run time, which is why it carries the third gate and file creation
    does not.
    """

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inp, DriveShareFileIn)

        # DENY BY DEFAULT. The gate is opt-in per tool, so a sharing tool that was
        # registered without a `ToolPermissionProfile` would otherwise reach this
        # handler having skipped authorization entirely. It arrives with no grant
        # and is refused here. This check is the reason forgetting the profile is
        # a fail-closed bug rather than a silent ungated share.
        grant = ctx.permission_grant
        if grant is None:
            raise ToolPermissionUnavailable(
                "integration.drive_share_file was dispatched without a "
                "PermissionGrant: the elevated-action gate did not run for this "
                "call. Refusing to share. This means the tool was registered "
                "without a ToolPermissionProfile — a misconfiguration, not a "
                "denial."
            )
        if grant.action_class != DRIVE_SHARE_ACTION_CLASS:
            raise ToolPermissionUnavailable(
                f"PermissionGrant authorizes {grant.action_class!r}, not "
                f"{DRIVE_SHARE_ACTION_CLASS!r}; refusing to share"
            )

        # Send what the gate AUTHORIZED, not what the input asked for. They are
        # normally identical; using the grant makes it impossible for the executed
        # share to drift from the approved one.
        is_link_grant = grant.grantee == "anyone"
        token = await _resolve_token(oauth, ctx.org_id, ctx.correlation_id)
        client = DriveClient(token)
        try:
            response = await client.create_permission(
                file_id=inp.file_id, grantee=grant.grantee,
                role=grant.role, is_link_grant=is_link_grant,
            )
        finally:
            await client.aclose()

        if response.status_code not in (200, 201):
            raise _drive_error(response, "sharing")
        body = response.json()
        log.info(
            "drive.file_shared",
            org_id=ctx.org_id, agent_id=ctx.agent_id, file_id=inp.file_id,
            grantee=grant.grantee, role=grant.role,
            matched_pattern=grant.matched_pattern,
        )
        return DriveShareFileOut(
            permission_id=body["id"], file_id=inp.file_id,
            grantee=grant.grantee, role=grant.role,
        )

    return ToolDefinition(
        tool_id="integration.drive_share_file",
        name="Share a Google Drive file",
        description=(
            "Grant a named recipient access to a Drive file this application "
            "created. The recipient and access level must be pre-authorized by "
            "the organization; unauthorized recipients are refused."
        ),
        input_schema=DriveShareFileIn,
        output_schema=DriveShareFileOut,
        category="integration",
        handler=handler,
        oauth={"provider": DRIVE_PROVIDER},
        permission={
            "action_class": DRIVE_SHARE_ACTION_CLASS,
            "grantee_field": "grantee",
            "role_field": "role",
        },
    )
