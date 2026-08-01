"""
Principal bounded context — types.

WHY THIS EXISTS
---------------
`GovernanceToken` (agent_governance.md §4) proves an *agent* is acting within its
*contract*. It carries `agent_id`, `authority_level`, `delegation_chain`, `scope`,
`max_token_budget`. What it does NOT carry is a **human principal**: there is no
claim answering "whose authority is this, and is it a subset of what that human
may do?".

That is fine for the autonomous shape (authority originates at `human_owner` and
flows down the agent org tree). It is insufficient for the per-employee shape,
where the security property we must be able to PROVE is:

    an employee's agent can never do anything the employee could not do himself.

This module adds the missing axis. It does not replace the governance token; it
supplies the second input that `GovernanceAuthority.mint()` must intersect against.

INTEGRATION POINTS (verify against HEAD before merge — Anti-M5):
  - `skylize.contracts.token.GovernanceToken`        -> extended with `on_behalf_of`
  - `skylize.app.governance.authority.GovernanceAuthority.mint()` -> calls
    `principal.authority.resolve_effective_scope()` before signing
  - `skylize.dal.ports`                              -> new repository protocols
  - `skylize.app.audit.service.AuditService.record()` -> unchanged, called on deny

NOT YET WIRED: none of the above integration points are touched by this module.
Adding `on_behalf_of` to `GovernanceToken` also requires extending
`skylize.contracts.token.canonical_signing_bytes` (and `token_signing_bytes` /
`TokenSigner.sign`) in the same change — the payload there is an explicit field
list, not a model dump, so a claim added to the model alone would be unsigned
and therefore forgeable. Deferred to the wiring pass.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# `OnBehalfOf` lives in `contracts.base`, because it is part of the SIGNED token
# wire format and `contracts` is an inner layer that must not import from `app`.
# It is re-exported here so the principal kernel keeps one vocabulary and every
# existing `from ...models import OnBehalfOf` still resolves.
#
# The redundant `as OnBehalfOf` marks this an intentional re-export: this module
# has no `__all__` and is not an `__init__.py`, so a bare import would read as an
# unused import and `ruff check src tests` — a CI gate — would fail on F401.
from ...contracts.base import OnBehalfOf as OnBehalfOf

# The scope vocabulary is deliberately the SAME string space as
# `ToolGrant.tool_id` (e.g. "llm.generate", "memory.search", "stripe.refund").
# One vocabulary, or the intersection below is meaningless.
ScopeId = str

AuthorityLevel = Literal["executive", "vp", "director", "manager", "worker"]


class ActorKind(str, Enum):
    """Who performed the journal entry. The co-work agent must be able to say
    'you did this' vs 'your agent did this while you were offline'."""

    HUMAN = "human"
    AGENT_AUTONOMOUS = "agent_autonomous"  # ran without the human present
    AGENT_COWORK = "agent_cowork"  # ran in an interactive session


class GrantSource(str, Enum):
    """Provenance of a grant. Kept so an auditor can answer 'why does Devon have
    stripe.refund?' without re-deriving the org chart."""

    POSITION = "position"  # derived from the org chart position
    GROUP = "group"  # from an RBAC group membership
    EXPLICIT_GRANT = "explicit_grant"  # named exception, requires justification
    EXPLICIT_DENY = "explicit_deny"  # named exception, wins over everything


class Grant(BaseModel):
    """One scope, granted or denied to one principal, with provenance.

    Effective-dated. `valid_to=None` means open-ended. Explicit denies always win
    (see `PrincipalAuthority.compile`), which is what makes segregation-of-duties
    expressible without rewriting the org chart.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ScopeId
    source: GrantSource
    valid_from: datetime
    valid_to: datetime | None = None
    justification: str | None = None

    @model_validator(mode="after")
    def _exceptions_need_justification(self) -> Grant:
        if self.source in (GrantSource.EXPLICIT_GRANT, GrantSource.EXPLICIT_DENY):
            if not (self.justification or "").strip():
                raise ValueError(
                    f"grant source={self.source.value} requires a justification "
                    f"(scope={self.scope!r})"
                )
        return self

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Grant:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be strictly after valid_from")
        return self

    def is_active_at(self, at: datetime) -> bool:
        if at < self.valid_from:
            return False
        return self.valid_to is None or at < self.valid_to


class Principal(BaseModel):
    """A human employee. The root of authority for the per-employee shape.

    `position_id` is the org-chart node. It GENERATES grants; it is not itself the
    permission. Coupling permissions directly to the org chart means the first HR
    reorg silently re-permissions the company.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=128)
    display_name: str
    position_id: str | None = None
    authority_level: AuthorityLevel
    manager_principal_id: str | None = None
    suspended_at: datetime | None = None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None


class AuthoritySnapshot(BaseModel):
    """The compiled, effective authority of one principal at one instant.

    `fingerprint` is embedded in every minted token. When a principal's authority
    changes the fingerprint changes, so live tokens minted under the old authority
    are detectable at verification time WITHOUT a database lookup per call. This
    is how revocation works without making the hot path chatty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    org_id: str
    scopes: frozenset[ScopeId]
    computed_at: datetime
    fingerprint: str  # sha256 over (org_id, principal_id, sorted(scopes))

    @field_validator("scopes", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> frozenset[str]:
        if isinstance(v, frozenset):
            return v
        if isinstance(v, (list, set, tuple)):
            return frozenset(str(x) for x in v)
        raise TypeError("scopes must be an iterable of strings")


# (`OnBehalfOf` is re-exported from `contracts.base` at the top of this module.)


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #


class SpendEnvelope(BaseModel):
    """A currency budget with a period, owned by one principal.

    Amounts are integer MINOR units (cents). Never float. Never Decimal across a
    process boundary.

    NOTE: this is the read model. The ceiling is NOT enforced by reading this and
    comparing — see `spend.SpendLedger`. A read-then-check is not a ceiling under
    concurrency; it is a race.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_id: UUID
    org_id: str
    principal_id: str
    currency: str = Field(min_length=3, max_length=3)
    ceiling_minor: int = Field(ge=0)
    reserved_minor: int = Field(ge=0)
    spent_minor: int = Field(ge=0)
    period_start: datetime
    period_end: datetime
    over_ceiling_behavior: Literal["hard_deny", "defer_to_human"]
    revoked_at: datetime | None = None

    @property
    def available_minor(self) -> int:
        return max(0, self.ceiling_minor - self.spent_minor - self.reserved_minor)


class Reservation(BaseModel):
    """A hold placed on an envelope before a spending action executes.

    Lifecycle: RESERVE -> (COMMIT with actual | RELEASE). A reservation that is
    neither committed nor released is swept at `expires_at` and released, so a
    crashed worker cannot permanently consume budget.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: UUID
    envelope_id: UUID
    org_id: str
    idempotency_key: str = Field(min_length=8, max_length=200)
    amount_minor: int = Field(gt=0)
    correlation_id: UUID
    governance_token_id: UUID | None = None
    state: Literal["held", "committed", "released", "expired"]
    created_at: datetime
    expires_at: datetime
    committed_minor: int | None = None


# --------------------------------------------------------------------------- #
# Work journal
# --------------------------------------------------------------------------- #


class JournalEntry(BaseModel):
    """One human-legible thing that happened on a principal's behalf.

    DELIBERATELY SEPARATE FROM `audit_log`. The audit trail stores inputs/outputs
    as SHA-256 hashes and is PII-free by design (audit/service.py). You cannot
    build a morning brief out of hashes. This is the readable projection; it is
    tenant-scoped, principal-scoped, and subject to the same RLS.

    `seq` is a monotonic bigserial. The co-work agent's freshness contract is
    'read everything with seq > my cursor', not 'poll a nightly digest'.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    org_id: str
    principal_id: str
    actor_kind: ActorKind
    actor_id: str  # agent_id, or principal_id when actor_kind is HUMAN
    correlation_id: UUID
    governance_token_id: UUID | None = None
    kind: str  # e.g. "invoice.reconciled", "decision.deferred_to_human"
    headline: str = Field(min_length=1, max_length=280)
    detail: dict[str, object] = Field(default_factory=dict)
    cost_minor: int = Field(default=0, ge=0)
    requires_attention: bool = False
    occurred_at: datetime


class JournalCursor(BaseModel):
    """Per-principal read position. Advanced only when the human has actually been
    shown the entries — not when the query runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: str
    principal_id: str
    last_seen_seq: int = Field(ge=0)
    last_seen_at: datetime
