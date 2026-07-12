"""
HTTP behaviour of SkylizeIngestClient: 202/503/403 handling, and
end-to-end 503-still-advances-state via a mocked run_sync call.
All HTTP is mocked with respx — no live dependencies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib

import httpx
import pytest
import respx

from anytype_sync.config import Settings
from anytype_sync.ingest_client import IngestResult, SkylizeIngestClient
from anytype_sync.sync import run_sync
from anytype_sync.sync_state import load_state

_BASE = "http://skylize.test"
_SECRET = "test-secret"
_INGEST_URL = f"{_BASE}/api/v1/knowledge/ingest"

_ANYTYPE_BASE = "http://anytype.test"
_SPACE = "space-abc"
_LIST_URL = f"{_ANYTYPE_BASE}/v1/spaces/{_SPACE}/objects"
_OBJECT_URL = f"{_ANYTYPE_BASE}/v1/spaces/{_SPACE}/objects/obj1"


# ── helpers ────────────────────────────────────────────────────────────────────

def _verify_sig(body: bytes, sig: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# ── 202 accepted ───────────────────────────────────────────────────────────────

async def test_202_returns_ok() -> None:
    with respx.mock:
        respx.post(_INGEST_URL).mock(return_value=httpx.Response(202, json={"status": "accepted"}))
        async with SkylizeIngestClient(_BASE, _SECRET) as client:
            result = await client.ingest("doc1", "content", "path/1")
    assert result == IngestResult.OK


async def test_202_sends_valid_hmac() -> None:
    captured: list[httpx.Request] = []

    with respx.mock:
        respx.post(_INGEST_URL).mock(
            side_effect=lambda req: (captured.append(req), httpx.Response(202))[1]
        )
        async with SkylizeIngestClient(_BASE, _SECRET) as client:
            await client.ingest("doc1", "hello", "p/1")

    assert len(captured) == 1
    req = captured[0]
    sig = req.headers.get("x-hub-signature-256", "")
    body = req.content
    assert _verify_sig(body, sig, _SECRET)

    # Body must be valid JSON matching the schema
    payload = json.loads(body)
    assert payload == {"doc_id": "doc1", "content": "hello", "source_path": "p/1"}


async def test_no_secret_sends_no_signature_header() -> None:
    captured: list[httpx.Request] = []

    with respx.mock:
        respx.post(_INGEST_URL).mock(
            side_effect=lambda req: (captured.append(req), httpx.Response(202))[1]
        )
        async with SkylizeIngestClient(_BASE, "") as client:
            await client.ingest("doc1", "content", "p/1")

    assert "x-hub-signature-256" not in captured[0].headers


# ── 503 unconfigured ───────────────────────────────────────────────────────────

async def test_503_does_not_raise() -> None:
    with respx.mock:
        respx.post(_INGEST_URL).mock(
            return_value=httpx.Response(
                503, json={"detail": "knowledge ingestion not configured"}
            )
        )
        async with SkylizeIngestClient(_BASE, _SECRET) as client:
            result = await client.ingest("doc1", "content", "p/1")

    assert result == IngestResult.UNCONFIGURED


# ── 403 auth error ─────────────────────────────────────────────────────────────

async def test_403_raises_http_status_error() -> None:
    with respx.mock:
        respx.post(_INGEST_URL).mock(
            return_value=httpx.Response(403, json={"detail": "invalid HMAC signature"})
        )
        async with SkylizeIngestClient(_BASE, _SECRET) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.ingest("doc1", "content", "p/1")

    assert exc_info.value.response.status_code == 403


# ── end-to-end: 503 still advances state ─────────────────────────────────────

def _make_settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        anytype_api_key="test-key",
        anytype_base_url=_ANYTYPE_BASE,
        anytype_space_id=_SPACE,
        skylize_api_base_url=_BASE,
        skylize_webhook_secret=_SECRET,
        sync_state_path=str(tmp_path / "state.json"),
    )


def _search_response() -> dict:  # type: ignore[type-arg]
    return {
        "data": [
            {
                "object": "object",
                "id": "obj1",
                "name": "My Page",
                "type": {"key": "page", "id": "t1", "name": "Page"},
                "snippet": "",
                "properties": [
                    {"key": "last_modified_date", "format": "date", "date": "2024-06-01"},
                ],
            }
        ],
        "pagination": {"total": 1, "offset": 0, "limit": 100, "has_more": False},
    }


def _detail_response() -> dict:  # type: ignore[type-arg]
    return {
        "object": {
            "object": "object",
            "id": "obj1",
            "name": "My Page",
            "type": {"key": "page", "id": "t1", "name": "Page"},
            "markdown": r"# My Page\|table\|",
            "properties": [],
        }
    }


async def test_503_still_advances_state(tmp_path: pathlib.Path) -> None:
    settings = _make_settings(tmp_path)

    with respx.mock:
        respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json=_search_response()))
        respx.get(_OBJECT_URL).mock(return_value=httpx.Response(200, json=_detail_response()))
        respx.post(_INGEST_URL).mock(
            return_value=httpx.Response(503, json={"detail": "not configured"})
        )
        await run_sync(settings)

    state = load_state(settings.sync_state_path)
    assert _SPACE in state, "State must be written even when Skylize responds 503"
    assert state[_SPACE]  # non-empty timestamp


async def test_403_does_not_advance_state(tmp_path: pathlib.Path) -> None:
    settings = _make_settings(tmp_path)

    with respx.mock:
        respx.get(_LIST_URL).mock(return_value=httpx.Response(200, json=_search_response()))
        respx.get(_OBJECT_URL).mock(return_value=httpx.Response(200, json=_detail_response()))
        respx.post(_INGEST_URL).mock(
            return_value=httpx.Response(403, json={"detail": "invalid HMAC signature"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await run_sync(settings)

    state = load_state(settings.sync_state_path)
    assert _SPACE not in state, "State must NOT be written when Skylize rejects with 403"


# ── URL path assertions ────────────────────────────────────────────────────────

async def test_list_objects_uses_correct_path() -> None:
    """list_objects must call GET /v1/spaces/{id}/objects (no /api prefix, no POST /search)."""
    from anytype_sync.anytype_client import AnytypeClient

    captured: list[httpx.Request] = []

    with respx.mock:
        respx.get(_LIST_URL).mock(
            side_effect=lambda req: (captured.append(req), httpx.Response(200, json={"data": []}))[1]
        )
        async with AnytypeClient("key", _ANYTYPE_BASE) as client:
            await client.list_objects(_SPACE)

    assert len(captured) == 1
    assert captured[0].url.path == f"/v1/spaces/{_SPACE}/objects"
    assert captured[0].method == "GET"


async def test_get_object_uses_correct_path() -> None:
    """get_object must call GET /v1/spaces/{id}/objects/{obj_id} (no /api prefix)."""
    from anytype_sync.anytype_client import AnytypeClient

    captured: list[httpx.Request] = []

    with respx.mock:
        respx.get(_OBJECT_URL).mock(
            side_effect=lambda req: (captured.append(req), httpx.Response(200, json=_detail_response()))[1]
        )
        async with AnytypeClient("key", _ANYTYPE_BASE) as client:
            await client.get_object(_SPACE, "obj1")

    assert len(captured) == 1
    assert captured[0].url.path == f"/v1/spaces/{_SPACE}/objects/obj1"
    assert captured[0].method == "GET"
