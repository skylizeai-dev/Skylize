"""memory_records: fact_hash + provenance for dedup-on-write

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-29

Memory writes must collapse on a canonical content hash (feat/memory-existence-
check). This migration evolves the EXISTING `memory_records` table (migration
0001) — it does NOT create a new table.

Added columns:
  - fact_hash         CHAR(64)  — SHA-256 of canonical {namespace, content}.
  - provenance        JSONB     — append-only list of who wrote/reinforced it.
  - importance_score  DOUBLE PRECISION — time-decayed reinforcement weight.
  - first_seen / last_seen TIMESTAMPTZ — reinforcement window bounds.

A UNIQUE index on (org_id, namespace, fact_hash) makes the write path's
ON CONFLICT collapse concurrent identical writes to one row.

Backfill: existing rows get a computed fact_hash and a single provenance entry
synthesized from their origin (created_by_agent + created_at). The SQL
canonicalization mirrors memory/dedup.py (lower → collapse-whitespace → trim);
Unicode NFC is applied by the Python write path but omitted here (no clean
pure-Postgres NFC) — acceptable for legacy rows, which are ASCII in practice.

NOTE ON CHAIN: renumbered 0009→0005 in the launch-chain consolidation (the
0005–0008 revisions it expected never landed on this line; 0004 is the head).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # 0. digest() (used below to compute fact_hash) lives in pgcrypto, not core.
    #    Idempotent — a no-op on a database that already has it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 1. Add the columns (nullable fact_hash first so existing rows survive).
    op.execute(
        """
    ALTER TABLE memory_records
        ADD COLUMN IF NOT EXISTS fact_hash CHAR(64),
        ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS importance_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        ADD COLUMN IF NOT EXISTS first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        ADD COLUMN IF NOT EXISTS last_seen  TIMESTAMPTZ NOT NULL DEFAULT now();
    """
    )

    # 2. Backfill fact_hash + provenance for any pre-existing rows. The canonical
    #    JSON is built to byte-match Python's
    #    json.dumps({"content_canonical":..,"namespace":..},
    #               sort_keys=True, separators=(",",":"), ensure_ascii=False).
    #    (Keys already alphabetical: content_canonical < namespace.)
    op.execute(
        r"""
    WITH canon AS (
        SELECT
            record_id,
            btrim(regexp_replace(lower(content_text), '\s+', ' ', 'g')) AS c_content,
            btrim(regexp_replace(lower(namespace),    '\s+', ' ', 'g')) AS c_ns
        FROM memory_records
        WHERE fact_hash IS NULL
    )
    UPDATE memory_records m
    SET fact_hash = encode(
            digest(
                '{"content_canonical":' || to_jsonb(canon.c_content)::text ||
                ',"namespace":'         || to_jsonb(canon.c_ns)::text || '}',
                'sha256'
            ),
            'hex'
        ),
        provenance = jsonb_build_array(
            jsonb_build_object(
                'event_id',              gen_random_uuid()::text,
                'agent_id',              m.created_by_agent,
                'ts',                    to_char(m.created_at AT TIME ZONE 'UTC',
                                                 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
                'source_correlation_id', gen_random_uuid()::text
            )
        ),
        first_seen = m.created_at,
        last_seen  = m.created_at
    FROM canon
    WHERE m.record_id = canon.record_id;
    """
    )

    # 3. Now that every row has a value, enforce NOT NULL on fact_hash.
    op.execute("ALTER TABLE memory_records ALTER COLUMN fact_hash SET NOT NULL;")

    # 4. Dedup identity: unique within a tenant + namespace. Enables ON CONFLICT.
    op.execute(
        """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_records_fact
        ON memory_records (org_id, namespace, fact_hash);
    """
    )

    # 5. The migration runs as the owner; the runtime app role inherits table
    #    grants from 0003's default privileges, but the new columns need no extra
    #    grant (column privileges follow the table grant). No-op kept explicit for
    #    self-description: the app role already has DML on memory_records.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON memory_records TO {_APP_ROLE};"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_memory_records_fact;")
    op.execute(
        """
    ALTER TABLE memory_records
        DROP COLUMN IF EXISTS last_seen,
        DROP COLUMN IF EXISTS first_seen,
        DROP COLUMN IF EXISTS importance_score,
        DROP COLUMN IF EXISTS provenance,
        DROP COLUMN IF EXISTS fact_hash;
    """
    )
