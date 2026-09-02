"""The Asana connector (integration_inputs.md 2.6).

Covers the three verbs, their profile declarations (the Q2.6b severity split), the
deny-by-default backstop in the membership handler, the fixed-`writer` role
mapping (Q2.6e), the one-grantee-per-call invariant, and error normalization
against Asana's `errors` envelope. Asana's API is faked with `httpx.MockTransport`
— no network, no live Asana account. Same harness as `test_drive_tools.py`.

Workspace-level `addUser` was removed (2.6 Q2.6a, owner decision): no published
Asana granular scope covers it, and enabling it would require Full permissions —
every endpoint, for every connected customer. Only `addMembers` (project-level,
`projects:write`) ships.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.asana_provider import ASANA_SCOPES
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import OAuthCredentialService
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import (
    PermissionGrant,
    ToolContext,
    ToolExecutionError,
    ToolPermissionUnavailable,
)
from skylize.tools.builtin.asana_tools import (
    ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
    AsanaAddProjectMemberIn,
    AsanaCreateProjectIn,
    AsanaCreateTaskIn,
    build_asana_add_project_member_tool,
    build_asana_create_project_tool,
    build_asana_create_task_tool,
)
from skylize.tools.builtin.asana_tools import ASANA_PROVIDER

ORG = "org-asana-1"
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _ctx(grant: PermissionGrant | None = None) -> ToolContext:
    return ToolContext(
        org_id=ORG, agent_id="agency_agent",
        correlation_id=uuid.uuid4(), permission_grant=grant,
    )


def _grant(
    grantee: str = "alice@example.com",
    role: str = "writer",
    action_class: str = ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
) -> PermissionGrant:
    return PermissionGrant(
        action_class=action_class, grantee=grantee,
        role=role, matched_pattern="example.com",
    )


async def _oauth_service() -> OAuthCredentialService:
    """OAuth service holding a live Asana grant."""
    enc = FernetEncryptor(TEST_KEY)
    repo = InMemoryOAuthCredentialRepository()
    now = datetime.now(timezone.utc)
    await repo.insert(OAuthCredentialRow(
        cred_id=uuid.uuid4(), org_id=ORG, provider=ASANA_PROVIDER, label="",
        provider_account_id="4673218951", key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("asana-access-token"),
        encrypted_refresh_token=enc.encrypt("asana-refresh"),
        expires_at=now + timedelta(hours=1), scopes=ASANA_SCOPES,
        connection_state="valid", state_reason=None,
        created_at=now, updated_at=now, refreshed_at=None,
    ))
    return OAuthCredentialService(
        encryptor=enc, repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )


def _patch_http(monkeypatch, handler) -> list[httpx.Request]:
    """Route AsanaClient's httpx.AsyncClient at a MockTransport."""
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


def _ok(data: dict) -> httpx.Response:
    """Asana's success envelope: everything wrapped in `data`."""
    return httpx.Response(201, json={"data": data})


def _err(status: int, *messages: str) -> httpx.Response:
    """Asana's REST error envelope, the shape verified 2026-09-02."""
    return httpx.Response(status, json={"errors": [{"message": m} for m in messages]})


# ---------------------------------------------------------------------------
# Profile declarations — the Q2.6b severity split
# ---------------------------------------------------------------------------

async def test_create_task_declares_oauth_but_not_permission() -> None:
    tool = build_asana_create_task_tool(await _oauth_service())
    assert tool.oauth is not None and tool.oauth.provider == ASANA_PROVIDER
    assert tool.permission is None, (
        "task creation stays inside the customer's own workspace; gating it would "
        "misclassify the severity split in 2.6 Q2.6b"
    )


async def test_create_project_declares_oauth_but_not_permission() -> None:
    tool = build_asana_create_project_tool(await _oauth_service())
    assert tool.oauth is not None
    assert tool.permission is None


async def test_add_project_member_declares_both_profiles() -> None:
    tool = build_asana_add_project_member_tool(await _oauth_service())
    assert tool.oauth is not None
    assert tool.permission is not None
    assert tool.permission.action_class == ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS
    assert tool.permission.grantee_field == "grantee"
    assert tool.permission.role_field == "role"


# ---------------------------------------------------------------------------
# Q2.6e — the fixed 'writer' role mapping
# ---------------------------------------------------------------------------

class TestFixedWriterRole:
    """Asana has no role axis; the schema pins 'writer' so an agent cannot vary it."""

    def test_role_defaults_to_writer(self) -> None:
        assert AsanaAddProjectMemberIn(
            project_gid="p1", grantee="a@example.com"
        ).role == "writer"

    @pytest.mark.parametrize("bad_role", ["reader", "commenter", "owner", "admin", ""])
    def test_no_other_role_is_accepted(self, bad_role: str) -> None:
        """The Literal is what makes the mapping unforgeable by an agent."""
        with pytest.raises(Exception):
            AsanaAddProjectMemberIn(
                project_gid="p1", grantee="a@example.com", role=bad_role
            )

    def test_writer_is_a_value_the_schema_check_constraint_permits(self) -> None:
        """Q2.6e: mapped in application code, so migration 0022 stays untouched."""
        from skylize.dal.permission_grants import ROLE_RANK

        assert "writer" in ROLE_RANK
        assert set(ROLE_RANK) == {"reader", "commenter", "writer"}, (
            "the role vocabulary changed — 2.6 Q2.6e's mapping decision needs "
            "re-checking, and the CHECK constraint must not have been relaxed"
        )

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(Exception):
            AsanaAddProjectMemberIn(
                project_gid="p1", grantee="a@example.com", access_level="full"
            )


# ---------------------------------------------------------------------------
# Deny-by-default — the 2448819 chokepoint pattern, reused
# ---------------------------------------------------------------------------

class TestDenyByDefault:
    """The membership handler refuses without a PermissionGrant.

    This is the backstop for the opt-in profile: a tool registered WITHOUT a
    `ToolPermissionProfile` never runs the gate, arrives with
    `permission_grant=None`, and must fail closed here rather than granting access.
    """

    async def test_project_member_refuses_without_grant(self, monkeypatch) -> None:
        calls = _patch_http(monkeypatch, lambda r: _ok({"gid": "1"}))
        tool = build_asana_add_project_member_tool(await _oauth_service())
        with pytest.raises(ToolPermissionUnavailable, match="without a PermissionGrant"):
            await tool.handler(
                AsanaAddProjectMemberIn(project_gid="p1", grantee="a@example.com"),
                _ctx(grant=None),
            )
        assert calls == [], "refused call must never reach Asana"

    async def test_grant_for_a_different_action_class_is_refused(
        self, monkeypatch
    ) -> None:
        """A grant authorized for a DIFFERENT elevated action must never be reused."""
        calls = _patch_http(monkeypatch, lambda r: _ok({"gid": "1"}))
        tool = build_asana_add_project_member_tool(await _oauth_service())
        with pytest.raises(ToolPermissionUnavailable, match="not 'asana.project.add_members'"):
            await tool.handler(
                AsanaAddProjectMemberIn(project_gid="p1", grantee="a@example.com"),
                _ctx(_grant(action_class="drive.permissions.create")),
            )
        assert calls == []

    @pytest.mark.parametrize("sentinel", ["me", "ME", " me ", "anyone", "Anyone"])
    async def test_sentinel_grantees_are_refused(self, monkeypatch, sentinel) -> None:
        """Neither sentinel names an external party.

        'me' is Asana's own self-reference. 'anyone' is the gate's link-sharing
        sentinel — Asana membership has no link-grant concept, so if an operator
        ever set `allow_link_sharing` on an Asana action class the gate would
        authorize it and this connector would otherwise post the literal string.
        """
        calls = _patch_http(monkeypatch, lambda r: _ok({"gid": "1"}))
        tool = build_asana_add_project_member_tool(await _oauth_service())
        with pytest.raises(ToolPermissionUnavailable, match="not a grantee"):
            await tool.handler(
                AsanaAddProjectMemberIn(project_gid="p1", grantee=sentinel),
                _ctx(_grant(grantee=sentinel)),
            )
        assert calls == []


# ---------------------------------------------------------------------------
# Execute-what-was-authorized
# ---------------------------------------------------------------------------

class TestExecutesTheAuthorizedGrant:
    async def test_sends_the_granted_recipient_not_the_input(self, monkeypatch) -> None:
        """The executed membership change cannot drift from the approved one."""
        import json

        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"gid": "p1"})

        _patch_http(monkeypatch, handler)
        tool = build_asana_add_project_member_tool(await _oauth_service())
        out = await tool.handler(
            # Input asks for mallory; the gate authorized alice. Alice must win.
            AsanaAddProjectMemberIn(project_gid="p1", grantee="mallory@evil.test"),
            _ctx(_grant(grantee="alice@example.com")),
        )
        assert bodies[0]["data"]["members"] == ["alice@example.com"]
        assert out.grantee == "alice@example.com"

    async def test_exactly_one_grantee_reaches_the_wire(self, monkeypatch) -> None:
        """One authorization, one recipient — the array is a wire detail only."""
        import json

        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"gid": "p1"})

        _patch_http(monkeypatch, handler)
        tool = build_asana_add_project_member_tool(await _oauth_service())
        await tool.handler(
            AsanaAddProjectMemberIn(project_gid="p1", grantee="alice@example.com"),
            _ctx(_grant()),
        )
        members = bodies[0]["data"]["members"]
        assert isinstance(members, list) and len(members) == 1


# ---------------------------------------------------------------------------
# Happy paths and wire shape
# ---------------------------------------------------------------------------

class TestRoutineVerbs:
    async def test_create_task_posts_data_envelope_and_returns_gid(
        self, monkeypatch
    ) -> None:
        import json

        seen: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((str(request.url), json.loads(request.content)))
            return _ok({
                "gid": "1201",
                "name": "Draft the brief",
                "permalink_url": "https://app.asana.com/0/1/1201",
            })

        _patch_http(monkeypatch, handler)
        tool = build_asana_create_task_tool(await _oauth_service())
        out = await tool.handler(
            AsanaCreateTaskIn(
                name="Draft the brief", workspace_gid="w1",
                notes="body", project_gids=["p1"], due_on="2026-09-30",
            ),
            _ctx(),
        )
        url, body = seen[0]
        assert url == "https://app.asana.com/api/1.0/tasks"
        assert body["data"]["name"] == "Draft the brief"
        assert body["data"]["workspace"] == "w1"
        assert body["data"]["projects"] == ["p1"]
        assert body["data"]["due_on"] == "2026-09-30"
        assert out.task_gid == "1201"
        assert out.permalink_url == "https://app.asana.com/0/1/1201"

    async def test_create_project_posts_to_projects(self, monkeypatch) -> None:
        seen = _patch_http(monkeypatch, lambda r: _ok({"gid": "77", "name": "Q4"}))
        tool = build_asana_create_project_tool(await _oauth_service())
        out = await tool.handler(
            AsanaCreateProjectIn(name="Q4", workspace_gid="w1", team_gid="t1"), _ctx()
        )
        assert str(seen[0].url) == "https://app.asana.com/api/1.0/projects"
        assert out.project_gid == "77"

    async def test_bearer_token_is_sent(self, monkeypatch) -> None:
        seen = _patch_http(monkeypatch, lambda r: _ok({"gid": "1"}))
        tool = build_asana_create_task_tool(await _oauth_service())
        await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())
        assert seen[0].headers["authorization"] == "Bearer asana-access-token"

    async def test_optional_fields_are_omitted_when_unset(self, monkeypatch) -> None:
        import json

        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"gid": "1"})

        _patch_http(monkeypatch, handler)
        tool = build_asana_create_task_tool(await _oauth_service())
        await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())
        data = bodies[0]["data"]
        assert set(data) == {"name", "workspace"}


class TestMembershipHappyPath:
    async def test_add_project_member_targets_the_right_path(self, monkeypatch) -> None:
        seen = _patch_http(monkeypatch, lambda r: _ok({"gid": "p1"}))
        tool = build_asana_add_project_member_tool(await _oauth_service())
        out = await tool.handler(
            AsanaAddProjectMemberIn(project_gid="p1", grantee="alice@example.com"),
            _ctx(_grant()),
        )
        assert str(seen[0].url).endswith("/projects/p1/addMembers")
        assert out.role == "writer"


# ---------------------------------------------------------------------------
# Error handling — Asana's `errors` envelope, not Drive's and not RFC 6749's
# ---------------------------------------------------------------------------

class TestErrorNormalization:
    async def test_asana_error_envelope_is_surfaced(self, monkeypatch) -> None:
        _patch_http(monkeypatch, lambda r: _err(400, "workspace: Missing input"))
        tool = build_asana_create_task_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="workspace: Missing input"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())

    async def test_multiple_errors_are_joined(self, monkeypatch) -> None:
        _patch_http(monkeypatch, lambda r: _err(400, "first problem", "second problem"))
        tool = build_asana_create_task_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="first problem; second problem"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())

    async def test_non_json_error_does_not_crash(self, monkeypatch) -> None:
        _patch_http(monkeypatch, lambda r: httpx.Response(403, text="Forbidden"))
        tool = build_asana_create_task_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="403"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())

    async def test_exhausted_retries_degrade_to_a_tool_error(self, monkeypatch) -> None:
        """A persistent 429 must not escape as a raw httpx.HTTPStatusError.

        Asana's published limits (150 req/min on free domains, plus cost-based
        accounting) make sustained 429 a realistic outcome, so this is a routine
        path. Without the handler-level catch it would surface as a 500.
        """
        calls = _patch_http(monkeypatch, lambda r: httpx.Response(429, json={
            "errors": [{"message": "Rate limit exceeded"}]
        }))
        tool = build_asana_create_task_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="retries exhausted"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())
        assert len(calls) == 3, "bounded retry: three attempts, then give up"

    async def test_exhausted_retries_on_membership_also_degrade(self, monkeypatch) -> None:
        _patch_http(monkeypatch, lambda r: httpx.Response(503, text="unavailable"))
        tool = build_asana_add_project_member_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="retries exhausted"):
            await tool.handler(
                AsanaAddProjectMemberIn(project_gid="p1", grantee="alice@example.com"),
                _ctx(_grant()),
            )

    async def test_success_without_a_gid_is_refused(self, monkeypatch) -> None:
        """Never report a task the caller cannot identify."""
        _patch_http(monkeypatch, lambda r: _ok({"name": "no gid here"}))
        tool = build_asana_create_task_tool(await _oauth_service())
        with pytest.raises(ToolExecutionError, match="no task gid"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())


class TestNotConnected:
    async def test_missing_grant_degrades_to_a_clean_tool_error(self, monkeypatch) -> None:
        """The HubSpot precedent: 'not connected' is a tool error, never a 500."""
        _patch_http(monkeypatch, lambda r: _ok({"gid": "1"}))
        enc = FernetEncryptor(TEST_KEY)
        empty = OAuthCredentialService(
            encryptor=enc, repo=InMemoryOAuthCredentialRepository(),
            audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        )
        tool = build_asana_create_task_tool(empty)
        with pytest.raises(ToolExecutionError, match="Asana is not connected"):
            await tool.handler(AsanaCreateTaskIn(name="t", workspace_gid="w1"), _ctx())
