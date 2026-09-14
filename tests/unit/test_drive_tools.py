"""The Google Drive connector (integration_inputs.md 2.5).

Covers the two verbs, their profile declarations, the deny-by-default backstop in
the sharing handler, and error normalization. The Drive API is faked with
`httpx.MockTransport` — no network, and no live Google account.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.google_provider import (
    DRIVE_FILE_SCOPE,
    build_google_drive_provider_config,
)
from skylize.app.credentials.oauth import OAuthCredentialService
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import PermissionGrant, ToolContext, ToolExecutionError, ToolPermissionUnavailable
from skylize.tools.builtin.drive_tools import (
    DRIVE_PROVIDER,
    DRIVE_SHARE_ACTION_CLASS,
    DriveCreateFileIn,
    DriveShareFileIn,
    build_drive_create_file_tool,
    build_drive_share_file_tool,
)

ORG = "org-drive-1"
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _ctx(grant: PermissionGrant | None = None) -> ToolContext:
    return ToolContext(
        org_id=ORG, agent_id="agency_agent",
        correlation_id=uuid.uuid4(), permission_grant=grant,
    )


def _grant(grantee="alice@example.com", role="reader") -> PermissionGrant:
    return PermissionGrant(
        action_class=DRIVE_SHARE_ACTION_CLASS, grantee=grantee,
        role=role, matched_pattern="example.com",
    )


async def _oauth_service(handler) -> OAuthCredentialService:
    """OAuth service holding a live Drive grant, with Drive's HTTP faked."""
    enc = FernetEncryptor(TEST_KEY)
    repo = InMemoryOAuthCredentialRepository()
    now = datetime.now(timezone.utc)
    await repo.insert(OAuthCredentialRow(
        cred_id=uuid.uuid4(), org_id=ORG, provider=DRIVE_PROVIDER, label="",
        provider_account_id="acct", key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("drive-access-token"),
        encrypted_refresh_token=enc.encrypt("drive-refresh"),
        expires_at=now + timedelta(hours=1), scopes=(DRIVE_FILE_SCOPE,),
        connection_state="valid", state_reason=None,
        created_at=now, updated_at=now, refreshed_at=None,
    ))
    return OAuthCredentialService(
        encryptor=enc, repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )


def _patch_drive_http(monkeypatch, handler) -> list[httpx.Request]:
    """Route DriveClient's httpx.AsyncClient at a MockTransport."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recording)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    return seen


#: The pre-generated id the fake Drive hands out. Every create in this module
#: spends this one, so a test asserting on the created file's id and a test
#: asserting on the 409 dedupe path are talking about the same file.
FAKE_GENERATED_ID = "file-123"


def _with_generate_ids(handler, *, generated_id: str = FAKE_GENERATED_ID):
    """Wrap a create-handler so `files/generateIds` is answered for it.

    Every `create_file` now reserves an id first (`drive_tools.py` `generate_id`),
    so a handler that answers only the upload would see the id call too and return
    the wrong body. This dispatches on URL and leaves each test's handler concerned
    only with the request it is actually about.
    """

    def dispatch(request: httpx.Request) -> httpx.Response:
        if "generateIds" in str(request.url):
            return httpx.Response(200, json={"ids": [generated_id]})
        return handler(request)

    return dispatch


# ---------------------------------------------------------------------------
# Profile declarations — the Q2.5b severity split
# ---------------------------------------------------------------------------

async def test_create_file_declares_oauth_but_not_permission() -> None:
    tool = build_drive_create_file_tool(await _oauth_service(None))
    assert tool.oauth is not None and tool.oauth.provider == DRIVE_PROVIDER
    assert tool.permission is None


async def test_share_declares_both_oauth_and_permission() -> None:
    tool = build_drive_share_file_tool(await _oauth_service(None))
    assert tool.oauth is not None
    assert tool.permission is not None
    assert tool.permission.action_class == DRIVE_SHARE_ACTION_CLASS
    assert tool.permission.grantee_field == "grantee"
    assert tool.permission.role_field == "role"


def test_provider_config_requests_only_drive_file_scope() -> None:
    """Q2.5a: widening this is a CASA cost decision, not a code change."""
    config = build_google_drive_provider_config(client_id="cid", client_secret="sec")
    assert config.scopes == (DRIVE_FILE_SCOPE,)
    assert DRIVE_FILE_SCOPE.endswith("/drive.file")
    assert config.token_url == "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

async def test_create_file_uploads_and_returns_ids(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "file-123", "name": "report.md",
            "webViewLink": "https://drive.google.com/file/d/file-123",
        })

    seen = _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    out = await tool.handler(
        DriveCreateFileIn(name="report.md", content="hello", mime_type="text/markdown"),
        _ctx(),
    )
    assert out.file_id == "file-123"
    assert out.web_view_link.endswith("file-123")
    upload = [r for r in seen if "upload/drive" in str(r.url)]
    assert upload, "expected a multipart upload request"
    assert b"multipart/related" in upload[0].headers["content-type"].encode()
    assert b"report.md" in upload[0].content


async def test_create_file_normalizes_a_drive_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota exceeded"}})

    _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    with pytest.raises(ToolExecutionError, match="quota exceeded"):
        await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())


# ---------------------------------------------------------------------------
# Duplicate-on-5xx — the D.5 hazard (audit_gdrive_readiness.md:392)
# ---------------------------------------------------------------------------

async def test_create_file_sends_the_pregenerated_id(monkeypatch) -> None:
    """The id must reach Drive in the metadata part, or 409 dedupe never engages."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": FAKE_GENERATED_ID, "name": "a"})

    seen = _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())

    upload = [r for r in seen if "upload/drive" in str(r.url)]
    assert upload, "expected a multipart upload request"
    assert f'"id": "{FAKE_GENERATED_ID}"'.encode() in upload[0].content


async def test_a_5xx_after_server_side_success_does_not_duplicate(monkeypatch) -> None:
    """THE REGRESSION TEST FOR THIS FIX.

    Models the exact hazard: Drive COMMITS the file, then the response is lost to a
    503. tenacity retries; the retry carries the same pre-generated id, so Drive
    answers 409. Before this fix that retry was an un-keyed re-POST and Drive
    created a SECOND file.

    The assertion that matters is not merely that the call succeeded — it is that
    exactly ONE id was ever reserved, so both upload attempts named the same file.
    """
    uploads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        uploads += 1
        if uploads == 1:
            # Committed server-side; the response is lost.
            return httpx.Response(503, json={"error": {"message": "backend error"}})
        # The retry re-presents the same id: Drive refuses to duplicate.
        return httpx.Response(409, json={"error": {"message": "A file with that id already exists"}})

    seen = _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    out = await tool.handler(
        DriveCreateFileIn(name="deliverable.md", content="x"), _ctx()
    )

    # The caller is told the truth: the file exists, under the id we reserved.
    assert out.file_id == FAKE_GENERATED_ID
    assert uploads == 2, "expected exactly one retry after the 503"

    id_calls = [r for r in seen if "generateIds" in str(r.url)]
    assert len(id_calls) == 1, (
        "the id must be reserved ONCE, outside the retry loop; reserving per "
        "attempt would give each attempt a different id and restore the duplicate"
    )


async def test_409_on_the_first_attempt_is_still_reported_as_success(monkeypatch) -> None:
    """A connection drop can lose the response before any status is seen.

    tenacity then retries and the FIRST status this code observes is the 409. The
    handler must not report failure for a file that demonstrably exists.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"message": "already exists"}})

    _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    out = await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())
    assert out.file_id == FAKE_GENERATED_ID


async def test_retry_after_a_genuine_failure_still_succeeds(monkeypatch) -> None:
    """The fix must not cost real resilience: a 503 with NO server-side write
    still retries and still creates the file normally."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": {"message": "transient"}})
        return httpx.Response(200, json={
            "id": FAKE_GENERATED_ID, "name": "a",
            "webViewLink": "https://drive.google.com/file/d/file-123",
        })

    _patch_drive_http(monkeypatch, _with_generate_ids(handler))
    tool = build_drive_create_file_tool(await _oauth_service(None))
    out = await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())
    assert out.file_id == FAKE_GENERATED_ID
    assert out.web_view_link.endswith("file-123")
    assert attempts == 2


async def test_exhausted_retries_surface_a_clean_tool_error(monkeypatch) -> None:
    """Persistent 5xx must degrade to ToolExecutionError, never a raw httpx error."""
    _patch_drive_http(
        monkeypatch,
        _with_generate_ids(lambda r: httpx.Response(503, json={"error": {"message": "down"}})),
    )
    tool = build_drive_create_file_tool(await _oauth_service(None))
    with pytest.raises(ToolExecutionError, match="retries exhausted"):
        await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())


async def test_refuses_to_create_when_no_id_can_be_reserved(monkeypatch) -> None:
    """Fail closed. An un-keyed create is exactly the unsafe state this fix removes,
    so it must not be silently fallen back to."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "generateIds" in str(request.url):
            return httpx.Response(200, json={"ids": []})
        return httpx.Response(200, json={"id": "unexpected"})

    seen = _patch_drive_http(monkeypatch, handler)
    tool = build_drive_create_file_tool(await _oauth_service(None))
    with pytest.raises(ToolExecutionError, match="pre-generated file id"):
        await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())
    assert not [r for r in seen if "upload/drive" in str(r.url)], (
        "no upload may be attempted without a reserved id"
    )


# ---------------------------------------------------------------------------
# Sharing — deny by default
# ---------------------------------------------------------------------------

async def test_share_refuses_without_a_permission_grant(monkeypatch) -> None:
    """The backstop that makes the opt-in profile safe.

    Asserted on the absence of any HTTP request, not just the exception: the
    point is that no share reaches Google, not merely that an error surfaced.
    """
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no Drive request may be made without a grant")

    seen = _patch_drive_http(monkeypatch, handler)
    tool = build_drive_share_file_tool(await _oauth_service(None))

    with pytest.raises(ToolPermissionUnavailable, match="without a PermissionGrant"):
        await tool.handler(DriveShareFileIn(file_id="f1", grantee="x@y.com"), _ctx(None))
    assert seen == []


async def test_share_refuses_a_grant_for_a_different_action_class(monkeypatch) -> None:
    seen = _patch_drive_http(monkeypatch, lambda r: httpx.Response(200, json={"id": "p"}))
    tool = build_drive_share_file_tool(await _oauth_service(None))
    wrong = PermissionGrant(
        action_class="some.other.action", grantee="a@example.com",
        role="reader", matched_pattern="example.com",
    )
    with pytest.raises(ToolPermissionUnavailable, match="not 'drive.permissions.create'"):
        await tool.handler(DriveShareFileIn(file_id="f1", grantee="a@example.com"), _ctx(wrong))
    assert seen == []


async def test_share_sends_the_authorized_values_not_the_raw_input(monkeypatch) -> None:
    """What executes must be what the gate approved.

    The input asks for `writer` for mallory@evil.com; the grant authorizes
    `reader` for alice@example.com. The request must carry the grant's values.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.update(_json.loads(request.content))
        return httpx.Response(200, json={"id": "perm-1"})

    _patch_drive_http(monkeypatch, handler)
    tool = build_drive_share_file_tool(await _oauth_service(None))
    out = await tool.handler(
        DriveShareFileIn(file_id="f1", grantee="mallory@evil.com", role="writer"),
        _ctx(_grant(grantee="alice@example.com", role="reader")),
    )
    assert captured["emailAddress"] == "alice@example.com"
    assert captured["role"] == "reader"
    assert captured["type"] == "user"
    assert out.grantee == "alice@example.com"


async def test_link_grant_sends_type_anyone(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.update(_json.loads(request.content))
        return httpx.Response(200, json={"id": "perm-2"})

    _patch_drive_http(monkeypatch, handler)
    tool = build_drive_share_file_tool(await _oauth_service(None))
    await tool.handler(
        DriveShareFileIn(file_id="f1", grantee="anyone", role="reader"),
        _ctx(_grant(grantee="anyone", role="reader")),
    )
    assert captured["type"] == "anyone"
    assert "emailAddress" not in captured


async def test_share_normalizes_a_drive_error(monkeypatch) -> None:
    _patch_drive_http(
        monkeypatch,
        lambda r: httpx.Response(404, json={"error": {"message": "File not found"}}),
    )
    tool = build_drive_share_file_tool(await _oauth_service(None))
    with pytest.raises(ToolExecutionError, match="File not found"):
        await tool.handler(
            DriveShareFileIn(file_id="missing", grantee="a@example.com"), _ctx(_grant())
        )


# ---------------------------------------------------------------------------
# Credential degradation
# ---------------------------------------------------------------------------

async def test_missing_grant_degrades_to_a_clean_tool_error(monkeypatch) -> None:
    """"Drive not connected" is an expected state, never an unhandled 500 —
    the HubSpot precedent."""
    _patch_drive_http(monkeypatch, lambda r: httpx.Response(200, json={"id": "x"}))
    empty = OAuthCredentialService(
        encryptor=FernetEncryptor(TEST_KEY),
        repo=InMemoryOAuthCredentialRepository(),
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )
    tool = build_drive_create_file_tool(empty)
    with pytest.raises(ToolExecutionError, match="not connected"):
        await tool.handler(DriveCreateFileIn(name="a", content="b"), _ctx())


# ---------------------------------------------------------------------------
# Q2.5e — out of scope stays out of scope
# ---------------------------------------------------------------------------

async def test_connector_exposes_no_delete_list_or_search_tool() -> None:
    """Q2.5e: deletion, Shared Drives, push webhooks, and full-text search are
    out of scope. Nothing here should have grown one."""
    import skylize.tools.builtin.drive_tools as mod

    builders = [n for n in dir(mod) if n.startswith("build_")]
    assert sorted(builders) == [
        "build_drive_create_file_tool",
        "build_drive_share_file_tool",
    ], f"unexpected Drive tool builders: {builders}"
    source = mod.__doc__ or ""
    assert "out of scope" in source.lower()
