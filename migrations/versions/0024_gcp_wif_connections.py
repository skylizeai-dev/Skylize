"""gcp_wif_connections + gcp_wif_targets — Workload Identity Federation trust state

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-04

Design: docs/06_integrations/gcp_wif_killswitch_design.md §3 (GA-4), owner-approved
at 6265d5d. This migration creates the FOUNDATION only: no trigger path, no
HITL wiring, no VM-stop verb. Nothing in this schema causes an action.

WHY A NEW TABLE AND NOT `oauth_credentials` (migration 0021). The two model
different things and the difference is not cosmetic:

  * `oauth_credentials.encrypted_access_token` is TEXT NOT NULL (0021:91). Under
    Workload Identity Federation NOTHING is stored — a short-lived token is
    minted per call at Google's Security Token Service from a trust
    relationship, then discarded. There is no honest value for that column.
  * There is no refresh-token concept at all, so `encrypted_refresh_token` is
    permanently NULL and the entire refresh machinery
    (`app/credentials/oauth.py` `evaluate_grant` / `ensure_fresh`) — which runs
    on EVERY governed tool call — would be answering a question that does not
    apply.
  * `expires_at` is the wrong shape in the wrong direction. 0023 relaxed it to
    NULL for grants that never expire; a WIF token expires in minutes and is
    never persisted. NULL here would assert "does not expire by time", the
    opposite of true.

The precedent for this reasoning is already in the tree: 0021:10-20 refused to
widen `org_credentials` for OAuth on the same grounds, in nearly the same words.

WHAT IS REUSED FROM 0021, DELIBERATELY AND VERBATIM: the `(org_id, ..., label)`
identity cardinality (0021:107-110), the `tenant_isolation` RLS policy with
ENABLE + FORCE (0021:128-136), the `skylize_app` grant, and the deliberate
EXCLUSION from migration 0002's cross-tenant read carve-out. What is NOT reused:
every credential-payload column.

NO ENCRYPTED COLUMN, AND NO `key_id`. Nothing stored here is secret. Every field
is either a value the customer types into their own Google Cloud IAM
configuration (project, pool, provider, audience, service account) or a value
Skylize publishes on a public endpoint (`issuer_slug`, in the OIDC issuer URL).
Encrypting non-secret configuration would route it through a path built for
secrets and would bury values the dispatch path reads on every call inside
ciphertext — the exact mistake 0021:24-33 argues against for `expires_at`.
0021's `key_id` records which Fernet key produced a row's ciphertext; with no
ciphertext there is nothing to record, and unlike 0021:40-47's "cheap now,
expensive later" reasoning this column can never become necessary.

`issuer_slug` IS NOT A SECRET, and a future reader must not "fix" that by
encrypting it. It is a high-entropy path segment in a PUBLIC URL. Possession of
the URL grants nothing — only the signing key mints tokens. Its entropy is an
anti-enumeration measure so the public discovery endpoint cannot be used as a
tenant-existence oracle, not a credential.

`connection_state` — FOUR values, not 0021's three, because the remedies differ:
  * 'unverified'    — configured but never successfully probed. Onboarding is
                      multi-step and a customer routinely saves the connection
                      before finishing the IAM bindings. Collapsing this into
                      'valid' or 'revoked' would make a half-onboarded
                      connection either look healthy or demand a remedy that is
                      not the right one. REMEDY: finish onboarding.
  * 'valid'         — a probe or live call proved BOTH the trust relationship
                      and the IAM binding. REMEDY: none.
  * 'revoked'       — the TRUST RELATIONSHIP is gone: the workload identity pool
                      or provider was deleted or disabled, so the STS exchange
                      itself fails. REMEDY: the customer re-creates the
                      federation.
  * 'misconfigured' — the trust relationship works but this path cannot act:
                      the IAM role binding was removed (STS succeeds, Compute
                      returns 403), or the audience / attribute condition no
                      longer matches. REMEDY: the customer edits ONE setting.
                      Telling such a customer to "reconnect" would be actively
                      wrong advice, which is the whole reason this value is
                      distinct from 'revoked'.

`last_probe_result` records WHICH LAYER failed, which `connection_state` alone
cannot express. It is what makes "trust broken" and "trust fine, binding
insufficient" separable in a query rather than only in prose: 'sts_failed'
is a trust-layer failure, 'compute_denied' is an authorization-layer failure on
a working trust. This distinction is the single most important operational fact
about a federated connection and it is deliberately a column, not a log line.

`signing_key_id` and `expected_jwks_key_id` — ROTATION ANCHORS ONLY. This pass
does NOT implement rotation. Google's workload identity pool providers may take
the key set either by fetching Skylize's published JWKS ('served') or by having
the customer upload it inline with `--jwk-json-path` ('uploaded', max 8 keys,
replace-not-merge, verified live 2026-09-04). An 'uploaded' connection turns any
future Skylize key rotation into a CUSTOMER action, so a rotation pass must be
able to find those connections and their expected key id before it moves a key.
Recording them now is cheap; discovering them mid-rotation is not.

RLS is ENABLED + FORCED with a `tenant_isolation` policy on BOTH tables,
modelled exactly on oauth_credentials (0021:128-136) and org_credentials
(0007:59-68), so the non-superuser `skylize_app` role is a genuine policy
subject.

`gcp_wif_targets.org_id` IS DENORMALISED ON PURPOSE. RLS policies are per-table;
a policy that had to join to the parent to find `org_id` would be both slower
and easier to get wrong, and a wrong tenancy predicate on the table naming which
of a customer's machines may be stopped is not a defect anyone should risk to
save a column. This mirrors the 0019 tenant tables, which each carry their own
`org_id`.

DELIBERATELY NOT added to migration 0002's cross-tenant read carve-out. The
public OIDC discovery and JWKS endpoints are unauthenticated and carry no org
context, which looks like it needs one — it does not. Both documents are
derivable from the issuer slug plus platform configuration alone, so those
endpoints perform ZERO database access (design doc §2.4). A carve-out granted
"just in case" on the table naming every customer's stoppable infrastructure is
the opposite of fail-closed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE gcp_wif_connections (
            conn_id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id                         TEXT NOT NULL REFERENCES tenants(org_id),
            label                          TEXT NOT NULL DEFAULT '',
            issuer_slug                    TEXT NOT NULL,
            gcp_project_id                 TEXT NOT NULL,
            gcp_project_number             TEXT NOT NULL,
            workload_identity_pool_id      TEXT NOT NULL,
            workload_identity_provider_id  TEXT NOT NULL,
            audience                       TEXT NOT NULL,
            access_mode                    TEXT NOT NULL DEFAULT 'direct',
            service_account_email          TEXT,
            signing_key_id                 TEXT NOT NULL,
            jwks_delivery                  TEXT NOT NULL DEFAULT 'served',
            expected_jwks_key_id           TEXT,
            connection_state               TEXT NOT NULL DEFAULT 'unverified',
            state_reason                   TEXT,
            last_probe_at                  TIMESTAMPTZ,
            last_probe_result              TEXT,
            last_success_at                TIMESTAMPTZ,
            created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT gcp_wif_connections_state_check
                CHECK (connection_state IN
                       ('unverified', 'valid', 'revoked', 'misconfigured')),
            CONSTRAINT gcp_wif_connections_access_mode_check
                CHECK (access_mode IN ('direct', 'impersonation')),
            -- An impersonation connection without a service account, or a direct
            -- connection carrying one, is a half-configured trust. Refuse both.
            CONSTRAINT gcp_wif_connections_sa_matches_mode_check
                CHECK ((access_mode = 'direct') = (service_account_email IS NULL)),
            CONSTRAINT gcp_wif_connections_jwks_delivery_check
                CHECK (jwks_delivery IN ('served', 'uploaded')),
            CONSTRAINT gcp_wif_connections_probe_result_check
                CHECK (last_probe_result IS NULL OR last_probe_result IN
                       ('ok', 'sts_failed', 'compute_denied',
                        'compute_unavailable', 'unreachable', 'no_targets'))
        )
    """))

    # Identity cardinality mirrors oauth_credentials (0021:107-110) and
    # org_credentials (0007:54-57): one live connection per (org, label).
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_gcp_wif_connections_unique "
        "ON gcp_wif_connections (org_id, label)"
    ))

    # GLOBALLY unique, not per-org: the slug is the public issuer path segment,
    # so a collision across two orgs would let one org's provider config accept
    # the other's tokens. This index is a tenancy control, not a convenience.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_gcp_wif_connections_issuer_slug "
        "ON gcp_wif_connections (issuer_slug)"
    ))

    op.execute(sa.text("""
        CREATE TABLE gcp_wif_targets (
            target_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conn_id         UUID NOT NULL
                             REFERENCES gcp_wif_connections(conn_id) ON DELETE CASCADE,
            org_id          TEXT NOT NULL REFERENCES tenants(org_id),
            gcp_project_id  TEXT NOT NULL,
            zone            TEXT NOT NULL,
            instance_name   TEXT NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_gcp_wif_targets_unique "
        "ON gcp_wif_targets (conn_id, gcp_project_id, zone, instance_name)"
    ))
    op.execute(sa.text(
        "CREATE INDEX idx_gcp_wif_targets_org ON gcp_wif_targets (org_id)"
    ))

    op.execute(sa.text(
        "COMMENT ON COLUMN gcp_wif_connections.issuer_slug IS "
        "'High-entropy path segment of this org''s PUBLIC OIDC issuer URL. NOT a "
        "secret and must never be encrypted: possession grants nothing, only the "
        "signing key mints tokens. Its entropy stops the public discovery "
        "endpoint being used as a tenant-existence oracle.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN gcp_wif_connections.connection_state IS "
        "'unverified = configured, never successfully probed (finish onboarding); "
        "valid = trust AND IAM binding both proven; revoked = trust relationship "
        "gone, pool/provider deleted or disabled (re-create the federation); "
        "misconfigured = trust works but this path cannot act, e.g. the IAM role "
        "binding was removed (edit one setting, do NOT reconnect).'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN gcp_wif_connections.last_probe_result IS "
        "'WHICH LAYER the last probe failed at, which connection_state alone "
        "cannot express. sts_failed = trust layer; compute_denied = authorization "
        "layer on a working trust. Removing an IAM binding leaves STS healthy and "
        "only Compute failing, so a probe that stops at STS reports a connection "
        "healthy when it can no longer act.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN gcp_wif_connections.expected_jwks_key_id IS "
        "'For jwks_delivery=uploaded: which signing key id the customer last "
        "uploaded to their provider with --jwk-json-path. Such a connection makes "
        "any Skylize key rotation a CUSTOMER action. Recorded now so a future "
        "rotation pass can find these before moving a key; rotation itself is not "
        "implemented.'"
    ))

    for table in ("gcp_wif_connections", "gcp_wif_targets"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"""
            CREATE POLICY tenant_isolation ON {table}
            FOR ALL
            USING (org_id = current_setting('skylize.org_id', true))
            WITH CHECK (org_id = current_setting('skylize.org_id', true))
        """))
        op.execute(sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}"
        ))


def downgrade() -> None:
    for table in ("gcp_wif_targets", "gcp_wif_connections"):
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {_APP_ROLE}"))
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
