"""The Notion connector (integration_inputs.md 2.7).

The centre of gravity is `TestRevocationWiring`. Notion grants never expire, so
the refresh-failure revocation path is unreachable and a live 401 is the ONLY
signal a grant has died. `OAuthCredentialService.mark_revoked_by_provider`
shipped in `8f147d4` with no caller; this connector is that caller, and these
tests assert the FULL loop — 401 observed, state written, next gated call denied
— rather than merely that a function was invoked.

The rest covers the three verbs, the deliberate absence of a
`ToolPermissionProfile`, Notion's own retry policy (429/529 only — 5xx is NOT
retried on these non-idempotent writes), and its third distinct error envelope.
Notion's API is faked with `httpx.MockTransport`; no network, no live workspace.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.notion_provider import NOTION_PROVIDER, NOTION_VERSION
from skylize.app.credentials.oauth import (
    GrantRevoked,
    OAuthCredentialService,
)
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolContext, ToolExecutionError
from skylize.tools.builtin.notion_tools import (
    NotionAppendBlocksIn,
    NotionCreateDatabaseIn,
    NotionCreatePageIn,
    build_notion_append_blocks_tool,
    build_notion_create_database_tool,
    build_notion_create_page_tool,
)

ORG = "org-notion-1"
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _ctx() -> ToolContext:
    return ToolContext(
        org_id=ORG, agent_id="agency_agent", correlation_id=uuid.uuid4()
    )


async def _oauth(repo: InMemoryOAuthCredentialRepository | None = None):
    """OAuth service holding a live, NON-EXPIRING Notion grant."""
    enc = FernetEncryptor(TEST_KEY)
    repo = repo if repo is not None else InMemoryOAuthCredentialRepository()
    now = datetime.now(timezone.utc)
    await repo.insert(OAuthCredentialRow(
        cred_id=uuid.uuid4(), org_id=ORG, provider=NOTION_PROVIDER, label="",
        provider_account_id="workspace-1", key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("notion-access-token"),
        encrypted_refresh_token=enc.encrypt("notion-refresh"),
        # NULL — the whole point: Notion issues no expiry.
        expires_at=None,
        scopes=(), connection_state="valid", state_reason=None,
        created_at=now, updated_at=now, refreshed_at=None,
    ))
    svc = OAuthCredentialService(
        encryptor=enc, repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )
    return svc, repo


def _patch_http(monkeypatch, handler) -> list[httpx.Request]:
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


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int, code: str, message: str = "boom") -> httpx.Response:
    """Notion's error envelope — a THIRD shape, distinct from Drive's and Asana's."""
    return httpx.Response(
        status,
        json={"object": "error", "status": status, "code": code, "message": message},
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Backoff is real seconds; never actually wait in a unit test."""
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("skylize.tools.builtin.notion_tools.asyncio.sleep", fake_sleep)


# ---------------------------------------------------------------------------
# THE REVOCATION WIRING — the reason this connector exists in this shape
# ---------------------------------------------------------------------------

class TestRevocationWiring:
    async def test_401_marks_the_grant_revoked(self, monkeypatch) -> None:
        _patch_http(monkeypatch, lambda r: _err(401, "unauthorized", "API token is invalid."))
        oauth, repo = await _oauth()
        tool = build_notion_create_page_tool(oauth)

        with pytest.raises(ToolExecutionError, match="marked as needing reconnection"):
            await tool.handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )

        stored = await repo.get(ORG, NOTION_PROVIDER, "")
        assert stored is not None
        assert stored.connection_state == "revoked", (
            "a live 401 did not close the nullable-expiry revocation gap"
        )
        assert "401" in (stored.state_reason or "")

    async def test_the_next_gated_call_is_then_denied(self, monkeypatch) -> None:
        """THE FULL LOOP. Asserting the state write alone would not prove the gap
        is closed — what matters is that the NEXT call actually refuses."""
        _patch_http(monkeypatch, lambda r: _err(401, "unauthorized"))
        oauth, _ = await _oauth()
        tool = build_notion_create_page_tool(oauth)

        # Before: a non-expiring grant sails through the credential gate.
        await oauth.ensure_fresh(org_id=ORG, provider=NOTION_PROVIDER)

        with pytest.raises(ToolExecutionError):
            await tool.handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )

        # After: the same gate the ToolProxy OAuth stage uses now denies.
        with pytest.raises(GrantRevoked):
            await oauth.ensure_fresh(org_id=ORG, provider=NOTION_PROVIDER)

    async def test_all_three_verbs_are_wired(self, monkeypatch) -> None:
        """Every Notion call funnels through one place, so no verb can miss it."""
        cases = [
            (build_notion_create_page_tool,
             NotionCreatePageIn(title="T", parent_page_id="p1")),
            (build_notion_create_database_tool,
             NotionCreateDatabaseIn(title="D", parent_page_id="p1")),
            (build_notion_append_blocks_tool,
             NotionAppendBlocksIn(block_id="b1", paragraphs=["x"])),
        ]
        for builder, payload in cases:
            _patch_http(monkeypatch, lambda r: _err(401, "unauthorized"))
            oauth, repo = await _oauth()
            with pytest.raises(ToolExecutionError):
                await builder(oauth).handler(payload, _ctx())
            stored = await repo.get(ORG, NOTION_PROVIDER, "")
            assert stored is not None
            assert stored.connection_state == "revoked", (
                f"{builder.__name__} did not record the revocation"
            )

    async def test_401_with_an_unparseable_body_still_counts(self, monkeypatch) -> None:
        """Notion's only documented 401 code is `unauthorized`; an unparseable 401
        is far more likely a dead token than anything else, and denying is safe."""
        _patch_http(monkeypatch, lambda r: httpx.Response(401, text="<html>nope"))
        oauth, repo = await _oauth()
        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )
        stored = await repo.get(ORG, NOTION_PROVIDER, "")
        assert stored is not None and stored.connection_state == "revoked"

    @pytest.mark.parametrize(
        "status,code",
        [
            (403, "restricted_resource"),
            (404, "object_not_found"),
            (400, "validation_error"),
            (409, "conflict_error"),
            (500, "internal_server_error"),
        ],
    )
    async def test_other_failures_never_mark_revoked(
        self, monkeypatch, status: int, code: str
    ) -> None:
        """THE LOAD-BEARING NEGATIVE. 403 is our own capability misconfiguration
        or a workspace block limit; 404 is an unshared page. Marking the customer
        revoked for either would demand a reconnect that fixes nothing and would
        hide the real fault."""
        _patch_http(monkeypatch, lambda r: _err(status, code))
        oauth, repo = await _oauth()

        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )

        stored = await repo.get(ORG, NOTION_PROVIDER, "")
        assert stored is not None
        assert stored.connection_state == "valid", (
            f"{status} {code} must not be reported as the customer revoking us"
        )

    async def test_a_failing_state_write_does_not_mask_the_auth_error(
        self, monkeypatch
    ) -> None:
        """Bookkeeping is best-effort; the call must still deny."""
        _patch_http(monkeypatch, lambda r: _err(401, "unauthorized"))
        oauth, _ = await _oauth()

        async def boom(**_kw):
            raise RuntimeError("repo down")

        monkeypatch.setattr(oauth, "mark_revoked_by_provider", boom)

        with pytest.raises(ToolExecutionError, match="Notion rejected the credentials"):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )


# ---------------------------------------------------------------------------
# Severity: no elevated action exists (Q2.7b)
# ---------------------------------------------------------------------------

class TestNoPermissionProfile:
    async def test_no_notion_tool_is_permission_gated(self) -> None:
        oauth, _ = await _oauth()
        for builder in (
            build_notion_create_page_tool,
            build_notion_create_database_tool,
            build_notion_append_blocks_tool,
        ):
            tool = builder(oauth)
            assert tool.oauth is not None
            assert tool.permission is None, (
                f"{tool.tool_id} is gated, but Notion exposes no sharing or "
                "permission-granting endpoint for a gate to authorize"
            )


# ---------------------------------------------------------------------------
# Happy paths and wire shape
# ---------------------------------------------------------------------------

class TestWireShape:
    async def test_create_page_posts_expected_payload(self, monkeypatch) -> None:
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"id": "page-1", "url": "https://notion.so/page-1"})

        seen = _patch_http(monkeypatch, handler)
        oauth, _ = await _oauth()
        out = await build_notion_create_page_tool(oauth).handler(
            NotionCreatePageIn(
                title="Brief", parent_page_id="p1", paragraphs=["one", "two"]
            ),
            _ctx(),
        )

        assert str(seen[0].url) == "https://api.notion.com/v1/pages"
        body = bodies[0]
        assert body["parent"] == {"page_id": "p1"}
        assert body["properties"]["title"]["title"][0]["text"]["content"] == "Brief"
        assert len(body["children"]) == 2
        assert out.page_id == "page-1"
        assert out.url == "https://notion.so/page-1"

    async def test_required_headers_are_sent(self, monkeypatch) -> None:
        seen = _patch_http(monkeypatch, lambda r: _ok({"id": "x"}))
        oauth, _ = await _oauth()
        await build_notion_create_page_tool(oauth).handler(
            NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
        )
        assert seen[0].headers["authorization"] == "Bearer notion-access-token"
        assert seen[0].headers["notion-version"] == NOTION_VERSION

    async def test_database_parent_is_used_when_given(self, monkeypatch) -> None:
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"id": "page-2"})

        _patch_http(monkeypatch, handler)
        oauth, _ = await _oauth()
        await build_notion_create_page_tool(oauth).handler(
            NotionCreatePageIn(title="T", parent_database_id="db1"), _ctx()
        )
        assert bodies[0]["parent"] == {"database_id": "db1"}

    async def test_append_uses_patch_on_block_children(self, monkeypatch) -> None:
        seen = _patch_http(monkeypatch, lambda r: _ok({"results": []}))
        oauth, _ = await _oauth()
        out = await build_notion_append_blocks_tool(oauth).handler(
            NotionAppendBlocksIn(block_id="b1", paragraphs=["a", "b", "c"]), _ctx()
        )
        assert seen[0].method == "PATCH"
        assert str(seen[0].url).endswith("/blocks/b1/children")
        assert out.appended == 3

    async def test_create_database_always_has_a_title_property(self, monkeypatch) -> None:
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return _ok({"id": "db-1"})

        _patch_http(monkeypatch, handler)
        oauth, _ = await _oauth()
        await build_notion_create_database_tool(oauth).handler(
            NotionCreateDatabaseIn(
                title="Tracker", parent_page_id="p1", text_properties=["Owner"]
            ),
            _ctx(),
        )
        props = bodies[0]["properties"]
        assert props["Name"] == {"title": {}}, "Notion requires exactly one title column"
        assert props["Owner"] == {"rich_text": {}}


class TestInputValidation:
    def test_page_requires_exactly_one_parent(self) -> None:
        with pytest.raises(Exception, match="exactly one of"):
            NotionCreatePageIn(title="T")
        with pytest.raises(Exception, match="exactly one of"):
            NotionCreatePageIn(title="T", parent_page_id="p", parent_database_id="d")

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            NotionCreatePageIn(title="T", parent_page_id="p", share_with="x")

    async def test_oversized_paragraph_is_refused_before_the_call(
        self, monkeypatch
    ) -> None:
        """Notion caps rich text at 2000 characters; fail clearly, not with a 400."""
        calls = _patch_http(monkeypatch, lambda r: _ok({"id": "x"}))
        oauth, _ = await _oauth()
        with pytest.raises(ToolExecutionError, match="2000-character"):
            await build_notion_append_blocks_tool(oauth).handler(
                NotionAppendBlocksIn(block_id="b1", paragraphs=["x" * 2001]), _ctx()
            )
        assert calls == []


# ---------------------------------------------------------------------------
# Retry policy — Notion's own guidance, which differs from Drive/Asana
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    async def test_429_is_retried(self, monkeypatch) -> None:
        calls = _patch_http(monkeypatch, lambda r: _err(429, "rate_limited"))
        oauth, _ = await _oauth()
        with pytest.raises(ToolExecutionError, match="rate limited"):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )
        assert len(calls) == 4, "bounded retry on 429"

    async def test_529_is_retried(self, monkeypatch) -> None:
        calls = _patch_http(monkeypatch, lambda r: _err(529, "service_overload"))
        oauth, _ = await _oauth()
        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )
        assert len(calls) == 4

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_5xx_is_NOT_retried_on_these_non_idempotent_writes(
        self, monkeypatch, status: int
    ) -> None:
        """Notion's own instruction: retry 5xx only for idempotent requests.

        Every verb here is a POST/PATCH write, so a timeout-then-success retry
        would create a SECOND page. Drive and Asana do retry 5xx; this connector
        deliberately declines to inherit that duplication hazard.
        """
        calls = _patch_http(monkeypatch, lambda r: _err(status, "internal_server_error"))
        oauth, _ = await _oauth()
        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(oauth).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )
        assert len(calls) == 1, f"{status} must not be retried on a write"

    async def test_retry_after_header_is_honoured(self, monkeypatch) -> None:
        """The per-workspace budget is shared with every other integration on the
        customer's workspace, so only Notion knows how long it is really throttled."""
        from skylize.tools.builtin.notion_tools import NotionClient

        client = NotionClient("tok")
        try:
            response = httpx.Response(429, headers={"Retry-After": "7"})
            assert client._sleep_seconds(response, 0) == 7.0
            # And a nonsense header falls back to jittered backoff rather than raising.
            bad = httpx.Response(429, headers={"Retry-After": "soon"})
            assert 0 < client._sleep_seconds(bad, 0) <= 30.0
        finally:
            await client.aclose()


class TestNotConnected:
    async def test_missing_grant_degrades_to_a_clean_tool_error(
        self, monkeypatch
    ) -> None:
        _patch_http(monkeypatch, lambda r: _ok({"id": "x"}))
        empty = OAuthCredentialService(
            encryptor=FernetEncryptor(TEST_KEY),
            repo=InMemoryOAuthCredentialRepository(),
            audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        )
        with pytest.raises(ToolExecutionError, match="Notion is not connected"):
            await build_notion_create_page_tool(empty).handler(
                NotionCreatePageIn(title="T", parent_page_id="p1"), _ctx()
            )
