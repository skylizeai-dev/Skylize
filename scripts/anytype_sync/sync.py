from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from .anytype_client import AnytypeClient, AnytypeObject, AnytypeProperty
from .config import Settings
from .ingest_client import IngestResult, SkylizeIngestClient
from .markdown_utils import unescape_anytype_markdown
from .sync_state import get_last_sync, load_state, now_iso, save_state, set_last_sync

log = structlog.get_logger(__name__)


async def resolve_anytype_api_key(settings: Settings) -> str:
    """Fetch the Anytype API key from the Skylize credential vault.

    Falls back to settings.anytype_api_key if vault fetch is not configured.
    Raises RuntimeError if vault is configured but the request fails.
    """
    if not (settings.skylize_auth_token and settings.org_id):
        if not settings.anytype_api_key:
            raise RuntimeError(
                "anytype_api_key must be set when skylize_auth_token/org_id are not configured"
            )
        return settings.anytype_api_key

    url = settings.resolve_credential_url or (
        settings.skylize_api_base_url.rstrip("/") + "/api/v1/credentials/resolve"
    )
    log.info("credential.resolving", url=url, org_id=settings.org_id)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            url,
            params={"provider": "anytype"},
            headers={"Authorization": f"ApiKey {settings.skylize_auth_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    key: str = data["value"]
    log.info("credential.resolved", provider="anytype")
    return key

_PAGE_TYPE_KEY = "page"


def _get_property(properties: list[AnytypeProperty], key: str) -> AnytypeProperty | None:
    for prop in properties:
        if prop.key == key:
            return prop
    return None


def _parse_anytype_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def filter_modified_since(
    objects: list[AnytypeObject],
    since: str | None,
) -> list[AnytypeObject]:
    """Client-side guard: drop objects not modified after *since* (ISO string)."""
    if since is None:
        return list(objects)

    threshold = _parse_anytype_date(since)
    kept: list[AnytypeObject] = []
    for obj in objects:
        prop = _get_property(obj.properties, "last_modified_date")
        if prop is None or prop.date is None:
            log.warning("sync.missing_date", object_id=obj.id)
            continue
        try:
            modified = _parse_anytype_date(prop.date)
        except ValueError:
            log.warning("sync.bad_date", object_id=obj.id, date=prop.date)
            continue
        if modified >= threshold:
            kept.append(obj)
    return kept


async def run_sync(settings: Settings) -> None:
    anytype_api_key = await resolve_anytype_api_key(settings)

    state = load_state(settings.sync_state_path)
    last_sync = get_last_sync(state, settings.anytype_space_id)
    sync_ts = now_iso()

    log.info("sync.start", space_id=settings.anytype_space_id, last_sync=last_sync)

    async with AnytypeClient(anytype_api_key, settings.anytype_base_url) as anytype:
        objects = await anytype.list_objects(settings.anytype_space_id, modified_since=last_sync)

        # Client-side filter as extra safety (server filter may be approximate).
        objects = filter_modified_since(objects, last_sync)
        # Only process page objects; skip Anytype system types.
        objects = [o for o in objects if o.type.key == _PAGE_TYPE_KEY]

        log.info("sync.objects_to_process", count=len(objects))

        async with SkylizeIngestClient(
            settings.skylize_api_base_url, settings.skylize_webhook_secret
        ) as ingest:
            for obj in objects:
                detail = await anytype.get_object(settings.anytype_space_id, obj.id)
                content = unescape_anytype_markdown(detail.markdown)

                log.info("sync.processing", object_id=obj.id, name=obj.name)

                result = await ingest.ingest(
                    doc_id=obj.id,
                    content=content,
                    source_path=f"anytype://{settings.anytype_space_id}/{obj.id}",
                )

                if result == IngestResult.UNCONFIGURED:
                    # 503 counts as delivered (unembedded but acknowledged); continue.
                    pass

                # 403 raises httpx.HTTPStatusError before reaching here →
                # save_state is never called → state does not advance. ✓

    set_last_sync(state, settings.anytype_space_id, sync_ts)
    save_state(settings.sync_state_path, state)
    log.info("sync.complete", space_id=settings.anytype_space_id, synced_at=sync_ts)
