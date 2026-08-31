"""org_permission_grants - org-level pre-authorization for elevated tool actions

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-31

Backs the THIRD opt-in ToolProxy stage (`ToolPermissionProfile`), decided in
`docs/06_integrations/integration_inputs.md` section 2.5, Q2.5d. The first two
stages gate a tool on a RESOURCE it consumes - a live OAuth grant
(`ToolOAuthProfile`) and a spend ceiling (`ToolSpendProfile`). This one gates a
tool on the SHAPE OF THE ACTION it is about to take: specifically, who an agent
may hand a customer's data to.

WHY A PRE-AUTHORIZATION TABLE AND NOT THE HITL QUEUE. Q2.5d's decision names a
per-call ToolProxy stage rather than the coarse per-request gate at
`app/agents/execution.py:283-294`. Routing an individual tool call to
`hitl_queue` instead was considered and REJECTED on evidence: the HITL approve
path replays the ENTIRE agent execution (`app/hitl/service.py:187` calls
`AgentExecutionService.execute` with the frozen `HitlReplayEnvelope`), so a share
deferred mid-run would, on approval, re-run the agent and create the deliverable
file a SECOND time in the customer's Drive before re-attempting the share. That
is the non-idempotent-replay hazard already recorded for Stripe refunds
(section 2.1, Q2.1d). A synchronous pre-authorization check has no replay and no
duplicate side effect.

This also matches the precedent the spend gate already set for a high-risk
per-call decision: `_reserve_spend` DENIES with a typed error carrying a
`defer_to_human` flag (`tools/base.py`, `ToolSpendDeferredToHuman`) and lets the
CALLER route to HITL at the request boundary, where replay is safe. The gate
itself never enqueues mid-call. No fourth mechanism is invented here.

DENY BY DEFAULT IS THE DATA MODEL, not a policy layered on top. An org with no
rows in this table can share with nobody. There is deliberately NO seed, no
wildcard default, and no "allow all" row inserted by this migration - an implicit
global grant on the table that decides who receives a customer's documents would
be exactly the unauditable default this platform exists to avoid. Compare
migration 0014's identical stance on a missing spend ceiling: absence refuses.

`grantee_pattern` matches either an exact address (`alice@example.com`) or a bare
domain (`example.com`, matching any address at that domain). It is deliberately
NOT a regex or glob: a pattern language on the column that decides who receives
customer data is an injection surface and an operator footgun. Matching is exact
string comparison in the gate, never SQL `LIKE`.

`allow_link_sharing` is separate from `grantee_pattern` and defaults FALSE
because "anyone with the link" names no grantee at all - it is a different risk
class, not a broader pattern, and must be enabled deliberately rather than
falling out of a permissive address rule.

`max_role` is ordered reader < commenter < writer; the gate refuses a request
exceeding it. There is no `owner` tier: transferring ownership of a customer's
file is out of scope for section 2.5 and must not be expressible here.

RLS is ENABLED + FORCED with a `tenant_isolation` policy, modelled exactly on
oauth_credentials (0021) and org_credentials (0007:59-68), so the non-superuser
`skylize_app` role is a genuine policy subject.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE org_permission_grants (
            grant_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id             TEXT NOT NULL REFERENCES tenants(org_id),
            action_class       TEXT NOT NULL,
            grantee_pattern    TEXT NOT NULL,
            max_role           TEXT NOT NULL,
            allow_link_sharing BOOLEAN NOT NULL DEFAULT false,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT org_permission_grants_role_check
                CHECK (max_role IN ('reader', 'commenter', 'writer'))
        )
    """))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_org_permission_grants_unique "
        "ON org_permission_grants (org_id, action_class, grantee_pattern)"
    ))

    op.execute(sa.text(
        "CREATE INDEX idx_org_permission_grants_lookup "
        "ON org_permission_grants (org_id, action_class)"
    ))

    op.execute(sa.text(
        "COMMENT ON TABLE org_permission_grants IS "
        "'Org-level pre-authorization for elevated tool actions (ToolPermissionProfile). "
        "An org with no rows here can perform no elevated action: absence is denial, "
        "never an implicit allow.'"
    ))

    op.execute(sa.text(
        "COMMENT ON COLUMN org_permission_grants.grantee_pattern IS "
        "'Exact address (alice@example.com) or bare domain (example.com). NOT a regex "
        "or glob - matched by exact string comparison in the gate, never SQL LIKE.'"
    ))

    op.execute(sa.text("ALTER TABLE org_permission_grants ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_permission_grants FORCE ROW LEVEL SECURITY"))

    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_permission_grants
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON org_permission_grants TO {_APP_ROLE}"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"REVOKE ALL ON org_permission_grants FROM {_APP_ROLE}"))
    op.execute(sa.text("DROP TABLE IF EXISTS org_permission_grants CASCADE"))
