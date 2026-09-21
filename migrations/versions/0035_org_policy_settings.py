"""org_policy_settings — org-wide guardrails + retention policy (mutable config, RLS)

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-21

WHAT THIS IS, AND WHY IT IS NOT org_autonomy_mode. The Command Console's Org
Settings screen mocks four things beyond autonomy mode: four guardrail toggles,
a retention window, an org name, and a region. Autonomy mode (migration 0028)
already has its own effective-dated, RLS-scoped table and is explicitly OUT OF
SCOPE here. `orgName` belongs to `tenants` (display_name), not here. This table
holds the three genuinely new org-wide policy concerns: guardrails, retention,
and (see below) region — modelled EXACTLY on 0028's shape because it is the
same authority class: an org-wide policy value with no per-department or
per-agent row, changed rarely, and owner-gated on write.

REGION — VERIFIED, NOT INVENTED. Before adding a region column at all, the infra
was checked rather than assumed: `infra/terraform/staging/terraform.tfvars:6`
(`aws_region = "us-east-1"`) and `.github/workflows/deploy-staging.yml:13`
(`AWS_REGION: us-east-1`) are the only real AWS region settings in this repo,
and both are a single hardcoded per-ENVIRONMENT Terraform variable, not a
per-org value read from any table. `docs/architecture/06_deployment_architecture.md`
describes one deployable substrate per environment (local/staging/production),
never a per-tenant region choice, and no doc or table anywhere ties an org_id to
a region. There is therefore NO real per-org region concept in this codebase
today — not even a single fixed value an org "has", since region is an
infra/environment property, not an org property. This migration deliberately
adds NO region column. Inventing a read-only `region` field reporting
"us-east-1" would misrepresent an environment-wide Terraform variable as
org-scoped policy, which is the same category of false-precision this codebase
explicitly refuses elsewhere (see edge/routes/security.py's refusal to report a
posture score or control inventory nothing backs). If per-org region ever
becomes real (e.g. per-tenant deployment target), it gets its own column then,
sourced from whatever actually assigns it.

RETENTION BOUNDS — PARTIALLY SOURCED. `docs/02_architecture/event_driven_architecture.md`
section 11 documents a REAL compliance floor for `audit`/`governance` event
retention: 7 years (2555 days) cold-archive, and states "Retention is
per-tenant configurable **upward** (never below the compliance floor for
audit/governance)" (line 384-385). That sentence is the one real, sourced
number this migration has: 2555 days is a documented compliance FLOOR for
audit/governance retention, not a document about `org_policy_settings.retention_days`
specifically (this column's exact real-world meaning — which data class it
governs — is undefined by any doc; the console mock does not say either). Given
that ambiguity, this migration uses 2555 days as the MINIMUM bound (matching the
one real number in the docs, read as "must retain at least this long") and
30 days as a floor-of-the-floor sanity minimum is REPLACED by that documented
minimum. The upper bound (10 years / 3650 days) has NO source anywhere in the
docs and is an ENGINEERING DEFAULT pending real compliance input, chosen only
so the CHECK constraint has *some* ceiling rather than an unbounded integer.
Concretely: CHECK (retention_days BETWEEN 2555 AND 3650). This is a DEVIATION
from the task brief's suggested 30-2555 range, made because research turned up
a real, cited compliance floor of 2555 days that a permissive 30-day minimum
would silently violate — shipping 30 as an allowed value here would let an
owner configure a retention window that contradicts the platform's own
documented audit/governance floor.

GUARDRAILS — four booleans, matching the mock's four concepts. Per-guardrail
enforcement status (verified against code, not assumed — see dal/org_policy_settings.py
for the full per-guardrail citation and safety-default reasoning):
  * `spend_cap_alert_enabled` — PREFERENCE ONLY today. `dal/org_spend_ceiling.py`
    enforces a spend ceiling (gate/deny), but nothing in this codebase sends an
    ALERT when a spend cap is approached — no alerting mechanism exists for
    org_spend_ceiling at all (grep for "alert" near spend/ceiling/budget turns
    up nothing but a docstring in tools/base.py:472 that is about a different
    concept). Toggling this column changes no system behavior yet.
  * `email_domain_restriction_enabled` — PREFERENCE ONLY today.
    `org_permission_grants` (migration 0022) gates WHO an agent may share a
    customer's Drive document with, keyed by (org_id, action_class,
    grantee_pattern), but that is a per-action ALLOW-list an owner populates
    explicitly, not a single on/off "restrict sharing to my org's email
    domain(s)" switch, and nothing reads a would-be
    `email_domain_restriction_enabled` flag anywhere in the tool-proxy gate.
  * `pii_redaction_enabled` — PREFERENCE ONLY today. `app/audit/service.py`
    hashes audit inputs/outputs (SHA-256) so the AUDIT TRAIL is PII-safe by
    construction, and `edge/routes/security.py` explicitly documents that PII
    redaction as a general CONTROL has no table and no enforced state anywhere
    in the system ("No table records the state of any of them"). There is no
    redaction step over agent inputs/outputs/tool payloads that this toggle
    could switch on or off.
  * `silent_fallback_suppressed` — PREFERENCE ONLY today, but closest to a real
    hook. `model_routing_rules` (migration 0032, `dal/model_routing.py`) has a
    genuinely related, ADJACENT concept — `fallback_logical_model` is NULLable
    per routing rule, and NULL already means "no fallback, fail closed" at the
    per-class level. But that is a per-class routing CHOICE an owner sets
    explicitly per rule; there is no global "suppress silent fallback" flag
    anywhere the adapter (`adapters/llm/anthropic_adapter.py`) reads, and this
    migration does not wire one — `silent_fallback_suppressed` here is a stored
    preference an owner can set, with no enforcement path consuming it yet.

None of the four guardrails is wired to enforcement as of this migration. The
route (`edge/routes/org_policy_settings.py`) reports this honestly per field so
the console never implies a toggle does something it does not yet do.

SHAPE: PRIMARY KEY (org_id, effective_from), identical to 0028 — an org-wide
policy CHANGE is one atomic decision covering guardrails + retention together,
so all fields written at the same instant are one generation and a partial
generation cannot be read as a merge of two different decisions.

FAIL CLOSED (same philosophy as 0028's ruling 7): a MISSING row does NOT mean
"no policy" — each guardrail resolves in the READ PATH (`dal/org_policy_settings.py`)
to its SAFEST value, and `retention_days` resolves to the documented compliance
floor (2555). None of these defaults is a column DEFAULT; the table is seeded
empty, exactly as 0028 is.

Mutability + isolation: MUTABLE CONFIG, no append-only trigger. RLS is modelled
EXACTLY on org_autonomy_mode (migration 0028) and through it on org_spend_ceiling
(0014) and ai_cost_ledger (0012:178-185): ENABLE + FORCE ROW LEVEL SECURITY, one
`tenant_isolation` FOR ALL policy on `current_setting('skylize.org_id')`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"

#: Documented compliance floor for audit/governance retention
#: (docs/02_architecture/event_driven_architecture.md section 11, line 371:
#: "7 years, immutable (object-lock)"). Used as the MINIMUM, not an
#: engineering guess: retention configured below this would contradict the
#: platform's own documented floor.
_RETENTION_MIN_DAYS = 2555
#: NOT sourced from any document. An engineering default ceiling pending real
#: compliance input, chosen only so the CHECK constraint is bounded.
_RETENTION_MAX_DAYS = 3650


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    # ------------------------------------------------------------------
    # org_policy_settings — one row per (org, effective instant). Org-wide
    # guardrails + retention. No region column: verified against real infra
    # (see module docstring) that there is no per-org region concept today.
    # ------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE org_policy_settings (
            org_id                          TEXT NOT NULL REFERENCES tenants(org_id),
            effective_from                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            spend_cap_alert_enabled         BOOLEAN NOT NULL,
            email_domain_restriction_enabled BOOLEAN NOT NULL,
            pii_redaction_enabled           BOOLEAN NOT NULL,
            silent_fallback_suppressed      BOOLEAN NOT NULL,
            retention_days                  INTEGER NOT NULL
                CHECK (retention_days BETWEEN {_RETENTION_MIN_DAYS} AND {_RETENTION_MAX_DAYS}),
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, effective_from)
        )
    """))

    op.execute(sa.text(
        "COMMENT ON TABLE org_policy_settings IS "
        "'Org-wide guardrails + retention policy, effective-dated like "
        "org_autonomy_mode (migration 0028). A MISSING row does not mean no "
        "policy: each column resolves in the read path (dal/org_policy_settings.py) "
        "to its safest fail-closed default, never a column DEFAULT. No region "
        "column: verified against infra/terraform and deployment docs that "
        "region is an environment-wide Terraform variable (us-east-1), not a "
        "per-org concept.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_policy_settings.spend_cap_alert_enabled IS "
        "'Stored preference only as of migration 0035 -- no alerting mechanism "
        "exists yet for org_spend_ceiling. Toggling this changes no enforcement.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_policy_settings.email_domain_restriction_enabled IS "
        "'Stored preference only as of migration 0035 -- org_permission_grants "
        "(migration 0022) gates sharing per explicit grantee_pattern, not via "
        "this single flag. Toggling this changes no enforcement.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_policy_settings.pii_redaction_enabled IS "
        "'Stored preference only as of migration 0035 -- no PII redaction control "
        "exists over agent inputs/outputs/tool payloads (edge/routes/security.py "
        "documents this control as unimplemented). Toggling this changes no "
        "enforcement.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_policy_settings.silent_fallback_suppressed IS "
        "'Stored preference only as of migration 0035. Adjacent to, but not "
        "wired to, model_routing_rules.fallback_logical_model (migration 0032), "
        "which is a per-routing-class NULL-means-no-fallback choice, not a "
        "global suppression flag the adapter reads.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_policy_settings.retention_days IS "
        "'CHECK bound MIN=2555 is the documented compliance floor for "
        "audit/governance retention (docs/02_architecture/event_driven_architecture.md "
        "section 11). MAX=3650 is an engineering default, not sourced from any "
        "document, pending real compliance input.'"
    ))

    # ------------------------------------------------------------------
    # Row-level security — modelled EXACTLY on org_autonomy_mode (migration
    # 0028) and through it on org_spend_ceiling (0014) / ai_cost_ledger
    # (0012:178-185).
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE org_policy_settings ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_policy_settings FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_policy_settings
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS). Mutable
    # config, so SELECT + INSERT + UPDATE for the read + upsert setter.
    # DELETE revoked for the same reason migration 0028 revokes it: erasing a
    # policy-settings row would erase the history of what guardrails/retention
    # were in force when, which is exactly the kind of fact an audit needs.
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON org_policy_settings TO {_APP_ROLE};")
    op.execute(f"REVOKE DELETE ON org_policy_settings FROM {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY. An org with no row reads as the fail-closed
    # defaults resolved in dal/org_policy_settings.py.
    # ------------------------------------------------------------------
    # (no INSERTs)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON org_policy_settings FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS org_policy_settings CASCADE"))
