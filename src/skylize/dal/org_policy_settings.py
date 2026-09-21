"""Org Policy Settings DAL — org-wide guardrails + retention (migration 0035).

Read/write layer for ``org_policy_settings``: four guardrail booleans and a
retention window, effective-dated exactly like ``OrgAutonomyModeDAL`` (which
this module is modeled closely on). Autonomy mode itself is a SEPARATE table
and out of scope here; so is region, which migration 0035 deliberately does
not add a column for — verified against ``infra/terraform/staging`` and
``docs/architecture/06_deployment_architecture.md`` that region is a
per-ENVIRONMENT Terraform variable (``us-east-1``), not a per-org concept.

Both queries run inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy applies — one org can never read or write another
org's policy settings.

The read is EFFECTIVE-DATED: the row in force is the one with the greatest
``effective_from`` at or before the requested instant, exactly the
``org_autonomy_mode`` / ``org_spend_ceiling`` resolution rule.

FAIL CLOSED, per field, to the SAFEST value — never a column DEFAULT, so
"nobody has configured this org" and "somebody explicitly chose these values"
stay distinguishable in the table while both resolve to a value callers can
act on safely:

  * ``spend_cap_alert_enabled`` defaults True. Enabling an alert can only ever
    surface more information to the owner; it has no side effect that could
    itself be a governance risk, so the safe default is "on."
    ENFORCEMENT STATUS: PREFERENCE ONLY. No alerting mechanism exists yet for
    ``org_spend_ceiling`` (``dal/org_spend_ceiling.py`` enforces the ceiling as
    a hard gate/deny, but nothing sends a notification as spend approaches
    it). This column changes no system behavior today.
  * ``email_domain_restriction_enabled`` defaults True. Restricting sharing is
    the fail-closed posture for the same reason ``org_permission_grants``
    (migration 0022) is deny-by-default: an org with no explicit grant can
    share with nobody, so defaulting the *restriction* toggle ON is consistent
    with that table's own stance, even though this flag does not yet feed it.
    ENFORCEMENT STATUS: PREFERENCE ONLY. ``org_permission_grants`` gates
    sharing per explicit ``(action_class, grantee_pattern)`` row; nothing
    reads this single on/off flag in the tool-proxy gate.
  * ``pii_redaction_enabled`` defaults True. Redacting PII by default is
    strictly safer than not redacting it; there is no operational cost to
    defaulting on.
    ENFORCEMENT STATUS: PREFERENCE ONLY. No redaction step exists over agent
    inputs/outputs/tool payloads; ``edge/routes/security.py`` documents PII
    redaction as a control with no table and no enforced state anywhere in
    this system. (The audit trail's SHA-256 hashing in
    ``app/audit/service.py`` is unrelated infrastructure, not something this
    flag controls.)
  * ``silent_fallback_suppressed`` defaults True. Suppressing a silent model
    fallback is safer than allowing one: a silent fallback can spend on a
    model, and at a price, the org never chose, exactly the failure
    ``model_routing_rules.fallback_logical_model`` already treats as unsafe by
    letting NULL mean "no fallback, fail closed" at the per-class level
    (migration 0032). Defaulting the org-wide toggle to "suppress" mirrors
    that per-class fail-closed reasoning.
    ENFORCEMENT STATUS: PREFERENCE ONLY. No code reads a global suppression
    flag; the adapter (``adapters/llm/anthropic_adapter.py``) and
    ``model_routing_rules`` only implement the per-class NULL-fallback
    behavior, which this column is not wired to.
  * ``retention_days`` defaults to 2555 (the CHECK-constrained minimum), which
    is the documented compliance FLOOR for audit/governance retention
    (``docs/02_architecture/event_driven_architecture.md`` section 11). The
    safest unset-org default is the floor, not the ceiling: it can only be
    read as "retain at least as long as compliance already requires,"
    never as an owner having chosen a specific longer window they did not.

``read_configured_settings`` exists alongside ``read_settings`` for the one
caller that must tell "unset" from "explicitly configured" — the console
showing the owner whether they have ever set this policy. Enforcement (if any
guardrail is ever wired to a real gate) must use ``read_settings``, which
never returns None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from skylize.app.audit.service import AuditService
    from skylize.dal.connection import Database

#: Mirrors the CHECK constraint in migration 0035. MIN is the documented
#: compliance floor (event_driven_architecture.md section 11: 7 years / 2555
#: days for audit/governance retention); MAX is an engineering default, not
#: sourced from any document, pending real compliance input.
RETENTION_MIN_DAYS = 2555
RETENTION_MAX_DAYS = 3650

#: Fail-closed defaults for an org that has never configured policy settings.
#: Every guardrail defaults to its SAFEST value (see module docstring for the
#: per-field safety reasoning); retention defaults to the documented floor.
DEFAULT_SPEND_CAP_ALERT_ENABLED = True
DEFAULT_EMAIL_DOMAIN_RESTRICTION_ENABLED = True
DEFAULT_PII_REDACTION_ENABLED = True
DEFAULT_SILENT_FALLBACK_SUPPRESSED = True
DEFAULT_RETENTION_DAYS = RETENTION_MIN_DAYS


@dataclass(frozen=True, slots=True)
class OrgPolicySettings:
    """One resolved (or fail-closed default) org policy settings row.

    Field-for-field, this is what ``read_settings`` always returns and what
    ``read_configured_settings`` returns only when a row exists.
    """

    spend_cap_alert_enabled: bool
    email_domain_restriction_enabled: bool
    pii_redaction_enabled: bool
    silent_fallback_suppressed: bool
    retention_days: int


#: The fail-closed instance ``read_settings`` returns when no row resolves.
DEFAULT_POLICY_SETTINGS = OrgPolicySettings(
    spend_cap_alert_enabled=DEFAULT_SPEND_CAP_ALERT_ENABLED,
    email_domain_restriction_enabled=DEFAULT_EMAIL_DOMAIN_RESTRICTION_ENABLED,
    pii_redaction_enabled=DEFAULT_PII_REDACTION_ENABLED,
    silent_fallback_suppressed=DEFAULT_SILENT_FALLBACK_SUPPRESSED,
    retention_days=DEFAULT_RETENTION_DAYS,
)


class OrgPolicySettingsDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_settings(
        self, org_id: str, at: datetime | None = None
    ) -> OrgPolicySettings:
        """The org's policy settings IN FORCE, fail-closed per field.

        Resolution semantics mirror ``OrgAutonomyModeDAL.read_mode``:
          * no row at or before ``at`` -> ``DEFAULT_POLICY_SETTINGS``;
          * a row exists only earlier -> that row is in force;
          * a newer row supersedes an older one from its own instant onward;
          * a row effective later than ``at`` is NOT used.
        """
        configured = await self.read_configured_settings(org_id, at)
        return configured if configured is not None else DEFAULT_POLICY_SETTINGS

    async def read_configured_settings(
        self, org_id: str, at: datetime | None = None
    ) -> OrgPolicySettings | None:
        """The settings in force, or None when the org has never set any.

        For DISPLAY only — enforcement must call ``read_settings``, which
        cannot return None. Same effective-dated resolution, same RLS scoping.
        """
        moment = at if at is not None else datetime.now(timezone.utc)
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT spend_cap_alert_enabled, email_domain_restriction_enabled,
                       pii_redaction_enabled, silent_fallback_suppressed,
                       retention_days
                FROM org_policy_settings
                WHERE org_id = $1 AND effective_from <= $2
                ORDER BY effective_from DESC
                LIMIT 1
                """,
                org_id,
                moment,
            )
        if row is None:
            return None
        return OrgPolicySettings(
            spend_cap_alert_enabled=row["spend_cap_alert_enabled"],
            email_domain_restriction_enabled=row["email_domain_restriction_enabled"],
            pii_redaction_enabled=row["pii_redaction_enabled"],
            silent_fallback_suppressed=row["silent_fallback_suppressed"],
            retention_days=row["retention_days"],
        )

    async def set_settings(
        self,
        *,
        org_id: str,
        spend_cap_alert_enabled: bool,
        email_domain_restriction_enabled: bool,
        pii_redaction_enabled: bool,
        silent_fallback_suppressed: bool,
        retention_days: int,
        audit: "AuditService",
        correlation_id: UUID,
        effective_from: datetime | None = None,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> OrgPolicySettings:
        """Set the org's policy settings, effective at ``effective_from``.

        Mutable config: an idempotent upsert on the ``(org_id, effective_from)``
        primary key, refreshing ``updated_at``. Tenant-scoped via RLS, so a
        caller can only write its own org's row.

        A policy change is a GOVERNANCE EVENT, not silent config — same rule
        ``OrgAutonomyModeDAL.set_mode`` follows (org_autonomy_mode.py:152-167).
        After the write commits this records a
        ``governance.org_policy_settings_set`` audit action carrying the
        before/after values, so the append-only trail always shows who changed
        an org's guardrails or retention window and to what.

        ``retention_days`` is validated here as well as by the DB CHECK, so a
        bad value fails with a legible error instead of a constraint
        violation. Returns the settings now in force at ``effective_from``.
        """
        if not (RETENTION_MIN_DAYS <= retention_days <= RETENTION_MAX_DAYS):
            raise ValueError(
                f"retention_days must be between {RETENTION_MIN_DAYS} and "
                f"{RETENTION_MAX_DAYS} inclusive; got {retention_days!r}"
            )
        moment = (
            effective_from if effective_from is not None else datetime.now(timezone.utc)
        )
        previous = await self.read_configured_settings(org_id, moment)
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                INSERT INTO org_policy_settings (
                    org_id, effective_from,
                    spend_cap_alert_enabled, email_domain_restriction_enabled,
                    pii_redaction_enabled, silent_fallback_suppressed,
                    retention_days
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (org_id, effective_from)
                DO UPDATE SET
                    spend_cap_alert_enabled = EXCLUDED.spend_cap_alert_enabled,
                    email_domain_restriction_enabled =
                        EXCLUDED.email_domain_restriction_enabled,
                    pii_redaction_enabled = EXCLUDED.pii_redaction_enabled,
                    silent_fallback_suppressed = EXCLUDED.silent_fallback_suppressed,
                    retention_days = EXCLUDED.retention_days,
                    updated_at = now()
                """,
                org_id,
                moment,
                spend_cap_alert_enabled,
                email_domain_restriction_enabled,
                pii_redaction_enabled,
                silent_fallback_suppressed,
                retention_days,
            )
        new_settings = OrgPolicySettings(
            spend_cap_alert_enabled=spend_cap_alert_enabled,
            email_domain_restriction_enabled=email_domain_restriction_enabled,
            pii_redaction_enabled=pii_redaction_enabled,
            silent_fallback_suppressed=silent_fallback_suppressed,
            retention_days=retention_days,
        )
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.org_policy_settings_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"effective_from={moment.isoformat()} "
                f"settings {previous} -> {new_settings}"
            ),
            inputs={
                "effective_from": moment.isoformat(),
                "spend_cap_alert_enabled": spend_cap_alert_enabled,
                "email_domain_restriction_enabled": email_domain_restriction_enabled,
                "pii_redaction_enabled": pii_redaction_enabled,
                "silent_fallback_suppressed": silent_fallback_suppressed,
                "retention_days": retention_days,
            },
        )
        return new_settings
