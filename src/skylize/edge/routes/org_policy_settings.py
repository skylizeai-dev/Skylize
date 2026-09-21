"""Org policy settings route — read and set guardrails + retention (0035).

ORG-WIDE, same authority class as autonomy mode (edge/routes/autonomy.py):
one GET and one PUT, no department or agent path segment, because there is no
narrower row to address. Autonomy mode itself lives in its own route and is
out of scope here. Region is deliberately ABSENT from this API: verified
against real infra (infra/terraform/staging, .github/workflows/deploy-staging.yml)
that region is a per-environment Terraform variable (``us-east-1``), not a
per-org concept — see migrations/versions/0035_org_policy_settings.py and
dal/org_policy_settings.py for the full citation trail. Adding a fake
`region` field here would misrepresent an environment-wide setting as
org-scoped policy.

RBAC is split exactly like autonomy: GET is owner-or-admin, PUT is OWNER ONLY
(edge/routes/autonomy.py:84-97) — this is org-wide guardrail + retention
policy, the same authority class as the org-wide autonomy posture, so the PUT
gate must not be weaker than autonomy's.

FAIL CLOSED: the GET reports each guardrail's SAFEST value and the retention
floor whenever no row resolves. The `configured` flag tells the console
whether that came from a choice or from the absence of one, mirroring the
autonomy route's own `configured` field.

HONESTY ABOUT ENFORCEMENT: none of the four guardrails is wired to a real
enforcement path as of migration 0035 (see dal/org_policy_settings.py's module
docstring for the per-guardrail citation). The response says so explicitly per
field via `enforced=False` on every guardrail today, so the console can render
"stored preference, not yet enforced" rather than implying a toggle changes
system behavior it does not yet change.

WIRED TO THE CONSOLE UI through a proxy, never directly, exactly like autonomy:
Console-Black calls the server-side BFF at
`website/src/app/api/console/org-policy-settings/route.ts`, which attaches a
service API key and calls these two verbs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...dal.org_policy_settings import (
    RETENTION_MAX_DAYS,
    RETENTION_MIN_DAYS,
    OrgPolicySettings,
    OrgPolicySettingsDAL,
)
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user, require_role

router = APIRouter(prefix="/api/v1/org-policy-settings", tags=["org-policy-settings"])

#: None of the four guardrails is wired to a real enforcement path today. This
#: is a literal, not derived per-request, because "enforced" is a fact about
#: the codebase, not about a request; a future PR that actually wires one of
#: these to a gate flips its entry here alongside the code that wires it, and
#: should update the docstrings in dal/org_policy_settings.py in the same
#: change so the two never drift.
_ENFORCEMENT_STATUS: dict[str, bool] = {
    "spend_cap_alert_enabled": False,
    "email_domain_restriction_enabled": False,
    "pii_redaction_enabled": False,
    "silent_fallback_suppressed": False,
}


class GuardrailField(BaseModel):
    value: bool = Field(description="The stored guardrail value.")
    enforced: bool = Field(
        description="Whether a real enforcement point in the system consumes "
        "this value today. False means this is currently a stored preference "
        "only — toggling it changes no system behavior yet."
    )


class OrgPolicySettingsResponse(BaseModel):
    spend_cap_alert_enabled: GuardrailField
    email_domain_restriction_enabled: GuardrailField
    pii_redaction_enabled: GuardrailField
    silent_fallback_suppressed: GuardrailField
    retention_days: int = Field(
        description="Retention window in days. Fail-closed default (unset "
        f"org) is {RETENTION_MIN_DAYS}, the documented compliance floor for "
        "audit/governance retention."
    )
    configured: bool = Field(
        description="False when no row resolves for this org, i.e. the values "
        "above are the fail-closed defaults rather than a choice. Display "
        "only; enforcement (where it exists) acts on the values either way."
    )


class SetOrgPolicySettingsRequest(BaseModel):
    spend_cap_alert_enabled: bool
    email_domain_restriction_enabled: bool
    pii_redaction_enabled: bool
    silent_fallback_suppressed: bool
    retention_days: int = Field(
        ge=RETENTION_MIN_DAYS,
        le=RETENTION_MAX_DAYS,
        description=f"Must be between {RETENTION_MIN_DAYS} and "
        f"{RETENTION_MAX_DAYS} inclusive.",
    )


def _require_dal(container: Container) -> "OrgPolicySettingsDAL":
    if container.policy_settings_dal is None:
        raise HTTPException(
            status_code=503,
            detail="org policy settings require the postgres backend",
        )
    return container.policy_settings_dal


def _to_response(
    settings: "OrgPolicySettings", configured: bool
) -> OrgPolicySettingsResponse:
    return OrgPolicySettingsResponse(
        spend_cap_alert_enabled=GuardrailField(
            value=settings.spend_cap_alert_enabled,
            enforced=_ENFORCEMENT_STATUS["spend_cap_alert_enabled"],
        ),
        email_domain_restriction_enabled=GuardrailField(
            value=settings.email_domain_restriction_enabled,
            enforced=_ENFORCEMENT_STATUS["email_domain_restriction_enabled"],
        ),
        pii_redaction_enabled=GuardrailField(
            value=settings.pii_redaction_enabled,
            enforced=_ENFORCEMENT_STATUS["pii_redaction_enabled"],
        ),
        silent_fallback_suppressed=GuardrailField(
            value=settings.silent_fallback_suppressed,
            enforced=_ENFORCEMENT_STATUS["silent_fallback_suppressed"],
        ),
        retention_days=settings.retention_days,
        configured=configured,
    )


@router.get("", response_model=OrgPolicySettingsResponse)
async def get_org_policy_settings(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> OrgPolicySettingsResponse:
    dal = _require_dal(container)
    configured = await dal.read_configured_settings(ctx.org_id)
    if configured is None:
        # Not a second source of truth for the defaults: read_settings owns
        # them, and this branch exists only to report WHY the answer is the
        # fail-closed set.
        return _to_response(await dal.read_settings(ctx.org_id), configured=False)
    return _to_response(configured, configured=True)


@router.put("", response_model=OrgPolicySettingsResponse)
async def set_org_policy_settings(
    body: SetOrgPolicySettingsRequest,
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> OrgPolicySettingsResponse:
    dal = _require_dal(container)
    settings = await dal.set_settings(
        org_id=ctx.org_id,
        spend_cap_alert_enabled=body.spend_cap_alert_enabled,
        email_domain_restriction_enabled=body.email_domain_restriction_enabled,
        pii_redaction_enabled=body.pii_redaction_enabled,
        silent_fallback_suppressed=body.silent_fallback_suppressed,
        retention_days=body.retention_days,
        audit=container.audit,
        correlation_id=ctx.correlation_id,
    )
    return _to_response(settings, configured=True)
