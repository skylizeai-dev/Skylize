"""Security activity read route — what governance actually did, over a window.

Read-only, and deliberately NARROW. Everything this route returns is a row or a
count that already exists in the append-only `audit_log` (migration 0001,
0001_initial_schema.py:298-316). Conventions follow the audit read route
(edge/routes/audit.py): a typed Pydantic response, RBAC via
`require_any_role_or_user("owner", "admin")`, and `org_id` taken strictly from
the authenticated `RequestContext` — never from a query parameter or a body.

WHAT THIS ROUTE DOES NOT RETURN, AND WHY THAT IS THE POINT
----------------------------------------------------------
The console's security screen was mocked with three things that have NO backend
counterpart anywhere in this repository, and this route deliberately ships
none of them:

  * A POSTURE SCORE (the mock's hardcoded `secScore = 94`). There is no scoring
    methodology in this system — not in a table, not in an ADR, not in a
    decision anyone has made. A number in this field would be invented here, in
    the request path, and then rendered under the word "security", where a
    reader would take it as an assurance that somebody had measured something.
    `dal/activity_signals.py` states the rule this route inherits: a number this
    layer invents is a number no human reviewed. Scoring is an open product
    question, not a missing implementation.
  * A CONTROL INVENTORY (the mock's eight rows — SSO, SCIM, encryption at rest,
    data residency, HITL, PII redaction, sandbox isolation, pen test). No table
    records the state of any of them. Reporting a control as enabled because a
    response model has a field for it is how a control gets believed in without
    ever being implemented.
  * COMPLIANCE BADGES (the mock's SOC 2, ISO 27001, GDPR, HIPAA-READY). A
    compliance claim is an assertion about an audit that an auditor performs.
    This system cannot produce one, and a badge emitted from an API is a false
    claim about a third party, not a display bug.

Their ABSENCE from this response is the honest signal, and the console should
render that absence rather than fill it. If any of the three is ever wanted, it
needs its own source of truth first, and this route should grow a field only
once that source exists.

WHAT IT DOES RETURN is the same window twice, from two angles: the counts of
every audited outcome across the whole window, and the actual most recent rows
for the non-success part of it. Both come from one DAL over one table under one
`tenant_session`, so the feed and the counts cannot disagree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...dal.activity_signals import AUDIT_RESULTS
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/security", tags=["security"])

#: Default window. Matches nothing enforced — it is a display default the caller
#: can override, and the response always states the window it actually used so
#: no reader has to assume this value.
DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 24 * 90


class GovernanceEventResponse(BaseModel):
    """One audited action, projected from `audit_log` with nothing added.

    NO ACTOR FIELD AND NO SIGNATURE FIELD, for the reasons the audit route
    states at edge/routes/audit.py:1-7 and the console BFF repeats:
    `source_agent_id` is an AGENT id or null and never a person, and the content
    hashes that would be the nearest thing to a signature are not signatures, so
    they are not carried here at all.
    """

    event_id: UUID
    correlation_id: UUID
    action_type: str
    result: str = Field(description="One of denied|escalated|failed for rows in this feed.")
    occurred_at: datetime
    source_agent_id: str | None = Field(
        description="The AGENT that took the action, or null. Never a human."
    )
    authority_level: str | None
    governance_token_id: UUID | None = Field(
        description="The governance token the action was taken under, or null "
        "when the action was recorded outside a minted token."
    )
    result_reason: str | None


class SecurityActivityResponse(BaseModel):
    window_start: datetime = Field(
        description="Inclusive start of the counted window (UTC)."
    )
    window_end: datetime = Field(
        description="Exclusive end of the counted window (UTC) — the moment the "
        "request was served."
    )
    total_actions: int = Field(
        description="Every audited action in the window, all outcomes."
    )
    by_result: dict[str, int] = Field(
        description="Counts keyed by audit result: success, denied, escalated, "
        "failed. Every key is always present, zero included — a missing key and "
        "a zero would otherwise be indistinguishable."
    )
    distinct_action_types: int
    distinct_agents: int
    recent_events: list[GovernanceEventResponse] = Field(
        description="The most recent non-success actions in the SAME window, "
        "newest first. Bounded by `limit`, so this is a sample of the counts "
        "above and not a second total; `recent_events_truncated` says which."
    )
    recent_events_truncated: bool = Field(
        description="True when the window holds more non-success actions than "
        "`limit` returned. The counts above are never truncated."
    )


@router.get("/activity", response_model=SecurityActivityResponse)
async def get_security_activity(
    window_hours: int = Query(
        default=DEFAULT_WINDOW_HOURS, ge=1, le=MAX_WINDOW_HOURS,
        description="How far back to count, in hours.",
    ),
    limit: int = Query(
        default=20, ge=1, le=200,
        description="Maximum rows in `recent_events`. Does not affect the counts.",
    ),
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> SecurityActivityResponse:
    dal = container.security_activity_dal
    if dal is None:
        # Same posture as the spend position route (edge/routes/spend.py:61-65):
        # the memory backend has no `audit_log`, and a zeroed response would be
        # a claim that nothing happened rather than that nothing was recorded.
        raise HTTPException(
            status_code=503,
            detail="security activity requires the postgres backend",
        )

    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=window_hours)

    counts = await dal.read_window(org_id=ctx.org_id, since=since, until=until)
    # One extra row is fetched purely to learn whether the feed is a sample; it
    # is dropped before serialization. Counting the non-success rows separately
    # would be a second query against a table that is being appended to while
    # this one runs, so the two answers could disagree by a row.
    fetched = await dal.read_recent_governance_events(
        org_id=ctx.org_id, since=since, until=until, limit=limit + 1
    )
    truncated = len(fetched) > limit

    return SecurityActivityResponse(
        window_start=since,
        window_end=until,
        total_actions=counts.total,
        # Zero-filled across the full result vocabulary, not just the results
        # that happened to occur: `AUDIT_RESULTS` is the DAL's own list, so a
        # new outcome value shows up here as a key rather than silently missing.
        by_result={r: counts.by_result.get(r, 0) for r in AUDIT_RESULTS},
        distinct_action_types=counts.distinct_action_types,
        distinct_agents=counts.distinct_agents,
        recent_events=[
            GovernanceEventResponse(
                event_id=e.event_id,
                correlation_id=e.correlation_id,
                action_type=e.action_type,
                result=e.result,
                occurred_at=e.occurred_at,
                source_agent_id=e.source_agent_id,
                authority_level=e.authority_level,
                governance_token_id=e.governance_token_id,
                result_reason=e.result_reason,
            )
            for e in fetched[:limit]
        ],
        recent_events_truncated=truncated,
    )
