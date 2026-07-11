"""Settings-gated CORS on the gateway: absent by default, allow-list when set.

The middleware is installed by create_app() only when SKYLIZE_CORS_ORIGINS is a
non-empty JSON array, so each fixture rebuilds the app after monkeypatching the
env and resetting the cached Settings singleton (same pattern as
tests/edge/test_agent_prompts.py). Memory backend, no infra required.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

ALLOWED_ORIGIN = "https://console.skylize.example"
OTHER_ORIGIN = "https://not-on-the-list.example"


def _fresh_app_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Reset the settings singleton so the patched env is picked up, then build
    # a new app (CORS wiring happens inside create_app, not per-request).
    import skylize.config as _cfg

    monkeypatch.setattr(_cfg, "_settings", None)
    return TestClient(create_app())


@pytest.fixture()
def default_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SKYLIZE_CORS_ORIGINS", raising=False)
    with _fresh_app_client(monkeypatch) as c:
        yield c


@pytest.fixture()
def cors_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SKYLIZE_CORS_ORIGINS", f'["{ALLOWED_ORIGIN}"]')
    with _fresh_app_client(monkeypatch) as c:
        yield c


# ---------------------------------------------------------------------------
# Default: no SKYLIZE_CORS_ORIGINS → no CORS middleware at all
# ---------------------------------------------------------------------------


def test_default_get_has_no_cors_headers(default_client: TestClient) -> None:
    resp = default_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_default_preflight_is_not_handled(default_client: TestClient) -> None:
    resp = default_client.options(
        "/health",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    # No middleware → the route itself answers (405: /health only allows GET),
    # and no CORS headers appear.
    assert resp.status_code == 405
    assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# Configured: listed origin echoed, unlisted origin refused
# ---------------------------------------------------------------------------


def test_configured_origin_is_echoed(cors_client: TestClient) -> None:
    resp = cors_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    # Explicit allow-list means per-origin responses; caches must key on Origin.
    assert "origin" in resp.headers.get("vary", "").lower()


def test_configured_preflight_allows_listed_origin(cors_client: TestClient) -> None:
    resp = cors_client.options(
        "/health",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_configured_unlisted_origin_not_echoed(cors_client: TestClient) -> None:
    resp = cors_client.get("/health", headers={"Origin": OTHER_ORIGIN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_configured_preflight_rejects_unlisted_origin(cors_client: TestClient) -> None:
    resp = cors_client.options(
        "/health",
        headers={"Origin": OTHER_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# Guard rail: wildcard origins are rejected at settings construction
# ---------------------------------------------------------------------------


def test_wildcard_origin_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYLIZE_CORS_ORIGINS", '["*"]')
    import skylize.config as _cfg

    monkeypatch.setattr(_cfg, "_settings", None)
    with pytest.raises(Exception, match="'\\*' is not allowed"):
        _cfg.get_settings()
