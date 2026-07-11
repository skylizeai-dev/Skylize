# anytype_sync

Incremental sync from Anytype Desktop (local API at `localhost:31009`) to the
Skylize knowledge-ingestion endpoint. Runs as a one-shot script; schedule via
cron / Windows Task Scheduler.

## Prerequisites

```
pip install -r scripts/anytype_sync/requirements.txt
```

Anytype Desktop must be running with the Local HTTP API enabled.

## Configuration

Copy `.env.example` to `.env` in the project root (or the directory you run
from) and fill in real values, **or** export the vars directly:

| Env var | Required | Description |
|---|---|---|
| `ANYTYPE_API_KEY` | ✓ | Bearer token from Anytype → Settings → API |
| `ANYTYPE_SPACE_ID` | ✓ | The Anytype space to sync (from Settings → Space) |
| `ANYTYPE_BASE_URL` | — | Default `http://localhost:31009` |
| `SKYLIZE_API_BASE_URL` | ✓ | Base URL of your Skylize instance |
| `SKYLIZE_WEBHOOK_SECRET` | — | HMAC secret (`SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET` on the server); leave empty to skip signing |
| `SYNC_STATE_PATH` | — | Path to the JSON state file; default is `.state/sync_state.json` inside this package |

## Running

```bash
# From the project root
python -m anytype_sync
```

Or with an explicit pythonpath:

```bash
PYTHONPATH=scripts python -m anytype_sync
```

## What it does

1. Loads `sync_state.json` to find the last sync timestamp for your space.
2. Calls `POST /api/v1/spaces/{spaceId}/objects/search` with a `GreaterOrEqual`
   filter on `last_modified_date`.
3. Keeps only objects where `type.key == "page"` (skips system objects).
4. For each page, fetches the full object and its `markdown` field.
5. Unescapes Anytype's backslash-escaped Markdown special characters.
6. POSTs to `{SKYLIZE_API_BASE_URL}/api/v1/knowledge/ingest` with HMAC signing.
7. Updates `sync_state.json` with the current timestamp.

A **503** from Skylize (ingestion not configured — OpenAI key pending) is logged
as a warning and still counted as delivered; the state advances so the object
is not re-sent once ingestion is enabled.

A **403** (bad HMAC) halts the sync and does **not** advance the state.

## Adapting to your Anytype version

The Anytype Local API response shape can vary between versions. If objects are
not being picked up, inspect the raw JSON from
`POST /api/v1/spaces/{spaceId}/objects/search` and adjust the Pydantic models
in `anytype_client.py` (particularly `AnytypeObject`, `AnytypeObjectType`,
`AnytypeProperty`) and the `type.key` constant in `sync.py`.

## Running tests

```bash
pip install respx pytest pytest-asyncio
pytest tests/anytype_sync/ -v
```
