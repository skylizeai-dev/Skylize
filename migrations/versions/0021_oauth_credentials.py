"""oauth_credentials — provider-agnostic OAuth grant storage (org-level)

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-29

A NEW TABLE ALONGSIDE ``org_credentials`` (migration 0007), NOT an alteration of
it. Design: docs/06_integrations/oauth_provider_infrastructure_design.md §6.

WHY NOT EXTEND org_credentials. That table's contract is "one opaque encrypted
string per (org_id, provider, label)": ``CredentialVault.store``/``rotate`` take a
single ``raw_value: str`` and ``retrieve`` returns a single ``str``
(app/credentials/vault.py:31-73), the HubSpot connector consumes exactly that
(tools/builtin/hubspot_tools.py:129-137), and the JSON-encoded-``str`` row
contract is pinned by tests/integration/test_jsonb_readback_pg.py:229-245.
An OAuth grant is not one string, and the two have different lifecycles: an API
key is written once and rotated by a human, whereas a grant is refreshed
automatically, carries an expiry, and can be revoked upstream by the customer.
Widening 0007 would also require NOT NULL columns that are meaningless for an
API-key row (what is ``expires_at`` for a HubSpot key?).

WHY STRUCTURED COLUMNS, NOT ONE JSONB BLOB. Two reasons, both load-bearing:
  1. ``org_credentials.metadata_json`` is PLAINTEXT JSONB (0007:43); only
     ``encrypted_value`` is ciphertext. A refresh token is secret material and
     must never live in a plaintext column, so it needs its own encrypted column.
  2. Encrypting one combined blob to protect the refresh token would bury
     ``expires_at`` inside ciphertext — and the freshness check reads the expiry
     on EVERY governed tool call. It must be readable (and indexable) without
     decrypting anything.
This also matches the schema's own convention: no JSONB column anywhere in this
database is indexed or queried by content (the only GIN index is a tsvector
expression, 0001:255). Anything that appears in a predicate is a real column —
cf. ``spend_reservation.expires_at`` plus its partial index (0019:169-170).

``key_id`` (owner-approved, included now deliberately). Records WHICH encryption
key produced the ciphertext in this row. There is exactly one platform-wide
Fernet key today (bootstrap.py ``resolve_credential_encryption_key``), so every
row is written with the same value — but adding this column later would require
rewriting every row to backfill it, and rows here are long-lived customer
credentials. It is cheap now and expensive later.

``connection_state`` is a THREE-VALUE DURABLE STATE, and 'expired' is NOT
redundant with ``expires_at``:
  * 'valid'   — the grant is usable. An access token PAST ``expires_at`` is still
                'valid' when a refresh token exists, because refresh repairs it.
  * 'expired' — the access token is past expiry AND cannot be repaired (no
                refresh token was ever issued). Needs a reconnect.
  * 'revoked' — the provider reported the grant dead (e.g. OAuth 2.0
                ``invalid_grant``, RFC 6749 §5.2). Needs a reconnect.
Deriving a stored 'expired' from the timestamp alone would be both redundant and
wrong; the distinction above is exactly "can this be fixed without a human".
Only an unambiguous provider signal may write 'revoked' — see
app/credentials/oauth.py, which refuses to mark a grant dead on a transient
network fault.

RLS is ENABLED + FORCED with a ``tenant_isolation`` policy, modelled exactly on
org_credentials (0007:59-68) and the 0019 tenant tables (0019:246-253), so the
non-superuser ``skylize_app`` role is a genuine policy subject.

DELIBERATELY NOT added to migration 0002's cross-tenant read carve-out list
(0002:33-36). The refresh strategy is on-demand inside ``tenant_session(org_id)``
(design doc §2.3), so nothing needs to read this table across tenants. A
carve-out granted "just in case" on the table holding every customer's
third-party access tokens is the opposite of fail-closed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE oauth_credentials (
            cred_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id                  TEXT NOT NULL REFERENCES tenants(org_id),
            provider                TEXT NOT NULL,
            label                   TEXT NOT NULL DEFAULT '',
            provider_account_id     TEXT NOT NULL DEFAULT '',
            key_id                  TEXT NOT NULL,
            encrypted_access_token  TEXT NOT NULL,
            encrypted_refresh_token TEXT,
            expires_at              TIMESTAMPTZ NOT NULL,
            scopes                  TEXT[] NOT NULL DEFAULT '{}',
            connection_state        TEXT NOT NULL DEFAULT 'valid',
            state_reason            TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            refreshed_at            TIMESTAMPTZ,
            CONSTRAINT oauth_credentials_state_check
                CHECK (connection_state IN ('valid', 'expired', 'revoked'))
        )
    """))

    # Identity cardinality mirrors org_credentials (0007:54-57): one live grant
    # per (org, provider, label). `label` = '' is the default connection.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_oauth_credentials_unique "
        "ON oauth_credentials (org_id, provider, label)"
    ))

    op.execute(sa.text(
        "CREATE INDEX idx_oauth_credentials_org_provider "
        "ON oauth_credentials (org_id, provider)"
    ))

    op.execute(sa.text("COMMENT ON COLUMN oauth_credentials.key_id IS "
                       "'Identifier of the encryption key that produced this row''s "
                       "ciphertext. One platform Fernet key today; present so key "
                       "rotation never requires rewriting existing rows.'"))

    op.execute(sa.text("COMMENT ON COLUMN oauth_credentials.connection_state IS "
                       "'valid = usable (refreshable even if past expires_at); "
                       "expired = past expiry and unrepairable (no refresh token); "
                       "revoked = provider reported the grant dead. Both non-valid "
                       "states require a human reconnect.'"))

    op.execute(sa.text("ALTER TABLE oauth_credentials ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE oauth_credentials FORCE ROW LEVEL SECURITY"))

    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON oauth_credentials
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_credentials TO {_APP_ROLE}"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"REVOKE ALL ON oauth_credentials FROM {_APP_ROLE}"))
    op.execute(sa.text("DROP TABLE IF EXISTS oauth_credentials CASCADE"))
