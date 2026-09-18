"""OrgRefundLimitsDAL - org_refund_authority_limits + org_refund_review_thresholds.

Design: docs/06_integrations/stripe_connector_design.md 7.5.5. Modelled on
`OrgSpendCeilingDAL` (dal/org_spend_ceiling.py) for the audited-setter pattern
and on `PgPermissionGrantRepository` (dal/permission_grants.py:6) for the
explicit-org-predicate discipline: every read and write runs inside
``Database.tenant_session(org_id)`` AND carries an explicit ``org_id``
predicate, so the RLS policy and the query agree rather than the query
relying on RLS alone.

BOTH TABLES ARE MINOR UNITS (cents), NOT MICRO-UNITS - the opposite of
`org_spend_ceiling.ceiling_micros`. See migration 0031's docstring and design
7.5.0's UNIT WARNING.

`None` is never fabricated into a default (design 7.5.5 rule 4), exactly as
`OrgSpendCeilingDAL.read_ceiling_micros` documents (org_spend_ceiling.py:58-62):
a `None` read is the honest "not configured" signal the caller acts on. For
`org_refund_authority_limits` the caller denies; for
`org_refund_review_thresholds` the caller routes to a human (design 7.5.3).

NO EFFECTIVE-DATING (design 7.5.5 rule 5). `org_spend_ceiling` is
effective-dated because it is keyed by billing period
(dal/org_spend_ceiling.py:19-22, 26-34). These tables have no period
dimension: a refund limit is current config, and the audit trail is what
reconstructs history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ..app.audit.service import AuditService
    from .connection import Database


class NonMonotonicAuthorityLadder(ValueError):
    """Refused: this write would leave a lower authority level with a higher
    refund cap than a higher one (design 7.5.5 rule 3).

    Not expressed as a SQL CHECK (design 7.5.2) because the constraint is
    cross-row, which Postgres cannot check cheaply at the row level. Enforced
    here instead, before the write, so a misconfiguration is refused rather
    than silently written.
    """


class OrgRefundLimitsDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_authority_limit_minor(
        self, org_id: str, currency: str, authority_level: str
    ) -> int | None:
        """The refund cap for (org, currency, authority_level), or None.

        A `None` read is the honest "not configured" signal - the caller
        DENIES (design 7.5.2's fail-closed-empty rule), never falls back to
        another currency or authority level.
        """
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                "SELECT max_refund_minor FROM org_refund_authority_limits "
                "WHERE org_id=$1 AND currency=$2 AND authority_level=$3",
                org_id, currency, authority_level,
            )
        return int(value) if value is not None else None

    async def read_review_threshold_minor(
        self, org_id: str, currency: str
    ) -> int | None:
        """The review-trigger threshold for (org, currency), or None.

        A `None` read means "review everything" (design 7.5.3's fail-closed-
        means-review-not-deny rule) - the caller must not read it as "never
        review."
        """
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                "SELECT review_above_minor FROM org_refund_review_thresholds "
                "WHERE org_id=$1 AND currency=$2",
                org_id, currency,
            )
        return int(value) if value is not None else None

    async def set_authority_limit(
        self,
        *,
        org_id: str,
        currency: str,
        authority_level: str,
        max_refund_minor: int,
        audit: "AuditService",
        correlation_id: UUID,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> None:
        """Set (upsert) the refund cap for (org, currency, authority_level).

        Validates before writing (design 7.5.5 rule 2): rejects a negative
        amount, an unknown authority_level, a currency that is not three
        characters, and a value that would break monotonicity across this
        org's other authority levels for the same currency (rule 3).
        """
        if max_refund_minor < 0:
            raise ValueError(
                f"max_refund_minor must be >= 0 (minor units); got {max_refund_minor}"
            )
        if len(currency) != 3:
            raise ValueError(f"currency must be a 3-character ISO-4217 code; got {currency!r}")
        if authority_level not in _AUTHORITY_ORDER:
            raise ValueError(f"unknown authority_level: {authority_level!r}")

        async with self._db.tenant_session(org_id) as conn:
            existing = await conn.fetch(
                "SELECT authority_level, max_refund_minor "
                "FROM org_refund_authority_limits WHERE org_id=$1 AND currency=$2",
                org_id, currency,
            )
            _check_monotonic(
                existing,
                changed_level=authority_level,
                changed_value=max_refund_minor,
            )
            previous = await conn.fetchval(
                "SELECT max_refund_minor FROM org_refund_authority_limits "
                "WHERE org_id=$1 AND currency=$2 AND authority_level=$3",
                org_id, currency, authority_level,
            )
            await conn.execute(
                """
                INSERT INTO org_refund_authority_limits
                    (org_id, currency, authority_level, max_refund_minor)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (org_id, currency, authority_level)
                DO UPDATE SET max_refund_minor = EXCLUDED.max_refund_minor,
                              updated_at = now()
                """,
                org_id, currency, authority_level, max_refund_minor,
            )
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.refund_authority_limit_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"currency={currency} authority_level={authority_level} "
                f"max_refund_minor {previous} -> {max_refund_minor}"
            ),
            inputs={
                "currency": currency,
                "authority_level": authority_level,
                "max_refund_minor": max_refund_minor,
            },
        )

    async def set_review_threshold(
        self,
        *,
        org_id: str,
        currency: str,
        review_above_minor: int,
        audit: "AuditService",
        correlation_id: UUID,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> None:
        """Set (upsert) the review-trigger threshold for (org, currency)."""
        if review_above_minor < 0:
            raise ValueError(
                f"review_above_minor must be >= 0 (minor units); got {review_above_minor}"
            )
        if len(currency) != 3:
            raise ValueError(f"currency must be a 3-character ISO-4217 code; got {currency!r}")

        async with self._db.tenant_session(org_id) as conn:
            previous = await conn.fetchval(
                "SELECT review_above_minor FROM org_refund_review_thresholds "
                "WHERE org_id=$1 AND currency=$2",
                org_id, currency,
            )
            await conn.execute(
                """
                INSERT INTO org_refund_review_thresholds
                    (org_id, currency, review_above_minor)
                VALUES ($1, $2, $3)
                ON CONFLICT (org_id, currency)
                DO UPDATE SET review_above_minor = EXCLUDED.review_above_minor,
                              updated_at = now()
                """,
                org_id, currency, review_above_minor,
            )
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.refund_review_threshold_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"currency={currency} review_above_minor {previous} -> {review_above_minor}"
            ),
            inputs={"currency": currency, "review_above_minor": review_above_minor},
        )


#: The canonical ladder, duplicated here ONLY as an ordered tuple for the
#: monotonicity check below - the values themselves are not redefined; they
#: are validated against contracts.base.AUTHORITY_RANK's own key set at
#: registration time (see tests/unit/test_refund_limits.py).
_AUTHORITY_ORDER: tuple[str, ...] = ("worker", "manager", "director", "vp", "executive")


def _check_monotonic(
    existing_rows: list[object], *, changed_level: str, changed_value: int
) -> None:
    """Refuse a write that leaves a lower authority level with a higher cap
    than a higher one, across this org+currency's OTHER rows plus the one
    being written (design 7.5.5 rule 3)."""
    caps: dict[str, int] = {
        row["authority_level"]: row["max_refund_minor"]  # type: ignore[index]
        for row in existing_rows
    }
    caps[changed_level] = changed_value

    ordered = [caps[level] for level in _AUTHORITY_ORDER if level in caps]
    for lower, higher in zip(ordered, ordered[1:]):
        if lower > higher:
            raise NonMonotonicAuthorityLadder(
                f"writing authority_level={changed_level!r}={changed_value} would "
                f"leave a lower authority level with a higher refund cap than a "
                f"higher one: {dict(zip(_AUTHORITY_ORDER, ordered))!r}"
            )
