"""github_app_installations + github_app_repos — GitHub App installation state

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-05

Design: docs/06_integrations/integration_inputs.md §2.4, `[APPROVED]` 2026-09-05
(`bdcd4e9`), Q2.4d. Predecessor audit: docs/audits/audit_github_readiness.md.
This migration creates the FOUNDATION only: no webhook ingress, no PR-merge verb,
and — per the owner's verb-surface-minimalism decision (Q2.4b.2) — no
branch-deletion capability anywhere. Nothing in this schema causes an action.

THE THIRD CREDENTIAL SHAPE. This is deliberately NOT `oauth_credentials` (0021)
and NOT a copy of `gcp_wif_connections` (0024). It is the third pattern, and the
reason is one specific fact about GitHub Apps:

    The App private key is APP-level — ONE key, shared across every tenant's
    installation — not per-tenant. `[LIVE-VERIFIED]` 2026-09-05,
    docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/
    managing-private-keys-for-github-apps: keys are managed at the App level, an
    App may hold up to 25 at once, rotation is generate-new/switch/delete-old.

So the secret does not live here AT ALL. It is a platform-level secret resolved
once at composition time (`app/github/keys.py`, the shape of
`bootstrap.py::resolve_credential_encryption_key` and
`::resolve_slack_notifier_config`). What is per-tenant is only the non-secret
question "WHICH installation is this org's". Hence:

WHY NO ENCRYPTED COLUMN AND NO `key_id` (contrast 0021, which has both).
`gh_installation_id` is not a secret. Possession of it grants nothing: only the
platform's App private key can mint a JWT, and only that JWT can be exchanged for
an installation access token. This is exactly the argument 0024:48-53 makes for
`issuer_slug` ("possession of the URL grants nothing — only the signing key mints
tokens"), and it lands the same way. 0021's `key_id` records which Fernet key
produced a row's ciphertext; with no ciphertext there is nothing to record, and
unlike 0021:40-47's "cheap now, expensive later" reasoning this column can never
become necessary — a future App key rotation changes a platform secret, not any
value in this table.

WHY NOT `oauth_credentials` (0021), restated because it will be asked again.
An installation access token expires in 1 hour and is re-mintable at will from the
platform key, so:
  * `encrypted_access_token TEXT NOT NULL` (0021:91) — there is no honest value.
    Persisting a 1-hour token buys nothing and widens the blast radius.
  * `encrypted_refresh_token` — GitHub Apps have NO refresh-token concept at all,
    so the column is permanently NULL and the entire refresh machinery
    (`app/credentials/oauth.py` `evaluate_grant`/`ensure_fresh`), which runs on
    EVERY governed tool call, would be answering a question that does not apply.
  * `expires_at` — nothing is persisted, so there is no row-level expiry.
0024:14-32 made this identical argument for WIF, in nearly the same words, and
0021:10-20 made it for `org_credentials` before that. This is the third time; the
reasoning has held each time.

WHAT IS REUSED FROM 0024, DELIBERATELY AND VERBATIM: the `(org_id, label)`
identity cardinality, the `tenant_isolation` RLS policy with ENABLE + FORCE, the
`skylize_app` grant, the denormalised `org_id` on the child table, and the
deliberate EXCLUSION from migration 0002's cross-tenant read carve-out.

`gh_installation_id` IS GLOBALLY UNIQUE, NOT UNIQUE PER ORG. This is a tenancy
control, not a convenience, and it is the most important index here. One GitHub
App installation belongs to exactly one customer account. If two orgs could each
claim installation 12345, then org A's governed tool call would mint a token
against org B's repositories — a cross-tenant escape that RLS cannot catch,
because both rows are individually well-formed and each org only ever reads its
own. The unique index is what makes the claim exclusive. Same reasoning as
0024's globally-unique `issuer_slug` index.

`connection_state` — FIVE values, owner-approved (§2.4 Q2.4c). They are NOT the
same five as 0024's four, because the question they answer is different: 0024 asks
"can this federation act", this asks "is the customer's own branch protection
actually protecting them". Tier 2 of the §2.4 architecture is CONDITIONAL on
customer configuration, and that condition is what this column records:
  * 'unverified'     — installed, never successfully probed. REMEDY: finish
                       onboarding / run the probe.
  * 'protected'      — a probe confirmed an ACTIVE ruleset covers the governed
                       branch AND this App cannot bypass it. REMEDY: none. This is
                       the only state in which the §2.4 Tier 2 guarantee holds.
  * 'unprotected'    — the installation is healthy but no active ruleset restricts
                       pushes to the governed branch. `contents: write` alone then
                       permits a direct push to that branch. NOT an error and NOT
                       a failure of Skylize: the connector works, it just carries
                       Tier 1 protection only. REMEDY: the customer adds branch
                       protection.
  * 'bypass_granted' — a covering ruleset EXISTS but this App can bypass it. This
                       is strictly worse than 'unprotected' and must never be
                       collapsed into it: the customer believes they are protected
                       and they are not. REMEDY: remove the Skylize App from the
                       ruleset's bypass actors.
  * 'revoked'        — the installation is gone or suspended. REMEDY: reinstall.

WHY 'unprotected' AND 'bypass_granted' ARE SEPARATE VALUES, empirically. It would
be easy to collapse them — both mean "Tier 2 is not protecting you". They are
kept apart because the REMEDY differs and because one of them is a silent
security regression while the other is a known gap. Distinguishing them was
verified to be possible under the Tier 1 permission set before this schema was
written; see `app/github/probe.py` for the API-level evidence.

`last_probe_result` records WHICH LAYER answered, which `connection_state` alone
cannot express — the same rationale 0024:78-86 gives for its own column, and for
the same reason it is a column and not a log line. In particular 'classic_only'
exists because GitHub's CLASSIC branch protection can be detected under the Tier 1
permission set but its bypass configuration CANNOT be (that needs
`administration: read`, which §2.4 Tier 1 forbids requesting). Such a repo is
recorded as 'unprotected' + 'classic_only' rather than 'protected', because
asserting protection that was never verified is the one outcome this whole
subsystem exists to prevent.

DELIBERATELY NOT added to migration 0002's cross-tenant read carve-out. The probe
runs inside `tenant_session(org_id)` for one org at a time, so nothing needs to
read this table across tenants. A carve-out on the table naming every customer's
governed repositories is the opposite of fail-closed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE github_app_installations (
            install_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id               TEXT NOT NULL REFERENCES tenants(org_id),
            label                TEXT NOT NULL DEFAULT '',
            gh_installation_id   BIGINT NOT NULL,
            gh_account_login     TEXT NOT NULL,
            gh_account_type      TEXT NOT NULL,
            repository_selection TEXT NOT NULL DEFAULT 'selected',
            connection_state     TEXT NOT NULL DEFAULT 'unverified',
            state_reason         TEXT,
            last_probe_at        TIMESTAMPTZ,
            last_probe_result    TEXT,
            last_success_at      TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT github_app_installations_state_check
                CHECK (connection_state IN
                       ('unverified', 'protected', 'unprotected',
                        'bypass_granted', 'revoked')),
            CONSTRAINT github_app_installations_account_type_check
                CHECK (gh_account_type IN ('Organization', 'User')),
            CONSTRAINT github_app_installations_repo_selection_check
                CHECK (repository_selection IN ('all', 'selected')),
            CONSTRAINT github_app_installations_probe_result_check
                CHECK (last_probe_result IS NULL OR last_probe_result IN
                       ('ok', 'no_ruleset', 'bypass_granted', 'classic_only',
                        'auth_failed', 'installation_gone',
                        'rulesets_unreadable', 'unreachable', 'no_repos')),
            -- A positive installation id is the only shape GitHub issues; a zero
            -- or negative value is a construction bug, not a customer state.
            CONSTRAINT github_app_installations_gh_id_positive
                CHECK (gh_installation_id > 0)
        )
    """))

    # Identity cardinality mirrors gcp_wif_connections (0024) and
    # oauth_credentials (0021:107-110): one live installation per (org, label).
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_github_app_installations_unique "
        "ON github_app_installations (org_id, label)"
    ))

    # GLOBALLY unique, not per-org. See the module docstring: this index is what
    # stops two orgs claiming the same installation and thereby minting tokens
    # against each other's repositories. RLS cannot catch that; this can.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_github_app_installations_gh_installation "
        "ON github_app_installations (gh_installation_id)"
    ))

    op.execute(sa.text("""
        CREATE TABLE github_app_repos (
            repo_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            install_id       UUID NOT NULL
                              REFERENCES github_app_installations(install_id)
                              ON DELETE CASCADE,
            org_id           TEXT NOT NULL REFERENCES tenants(org_id),
            owner            TEXT NOT NULL,
            name             TEXT NOT NULL,
            -- The branch whose protection the probe checks and whose integrity
            -- the Tier 2 guarantee is about. Defaults to 'main' because that is
            -- what it is in the overwhelming majority of repositories, but it is
            -- a real column because a customer whose trunk is 'master' or
            -- 'develop' would otherwise have their ACTUAL trunk unprobed while
            -- the dashboard reported on a branch that does not exist.
            protected_branch TEXT NOT NULL DEFAULT 'main',
            enabled          BOOLEAN NOT NULL DEFAULT true,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_github_app_repos_unique "
        "ON github_app_repos (install_id, owner, name)"
    ))
    op.execute(sa.text(
        "CREATE INDEX idx_github_app_repos_org ON github_app_repos (org_id)"
    ))

    op.execute(sa.text(
        "COMMENT ON COLUMN github_app_installations.gh_installation_id IS "
        "'GitHub''s numeric installation id. NOT a secret and must never be "
        "encrypted: possession grants nothing, only the platform-level App "
        "private key can mint a JWT and exchange it for an installation token. "
        "GLOBALLY unique — one installation belongs to exactly one org, and "
        "without that uniqueness one org could mint tokens against another''s "
        "repositories, which RLS cannot prevent.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN github_app_installations.connection_state IS "
        "'unverified = never probed (run the probe); protected = an ACTIVE "
        "ruleset covers the governed branch AND this App cannot bypass it (the "
        "only state where the 2.4 Tier 2 guarantee holds); unprotected = healthy "
        "but nothing restricts pushes to that branch (Tier 1 only); "
        "bypass_granted = a covering ruleset exists but this App can bypass it "
        "(STRICTLY WORSE than unprotected — the customer believes they are "
        "protected and is not); revoked = installation gone or suspended.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN github_app_installations.last_probe_result IS "
        "'WHICH LAYER the probe answered at, which connection_state alone cannot "
        "express. classic_only = CLASSIC branch protection was detected but its "
        "bypass configuration is unreadable under the Tier 1 permission set "
        "(that needs administration:read, which 2.4 Tier 1 forbids), so the row "
        "is recorded unprotected rather than asserting unverified protection.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN github_app_repos.protected_branch IS "
        "'The branch whose protection the probe checks. A real column, not a "
        "constant, so a customer whose trunk is not ''main'' does not get a "
        "dashboard reporting on a branch that does not exist.'"
    ))

    for table in ("github_app_installations", "github_app_repos"):
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
    for table in ("github_app_repos", "github_app_installations"):
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {_APP_ROLE}"))
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
