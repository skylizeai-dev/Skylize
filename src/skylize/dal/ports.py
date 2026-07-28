"""
Repository ports (Protocols) and the row dataclasses they move.

No driver import here — `app/` codes against these. Concrete asyncpg
implementations live in `repositories.py`; in-memory fakes in `memory.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from ..schemas.memory import MemoryWriteOutcome, ProvenanceEntry


# ---------------------------------------------------------------------------
# Row dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TokenRow:
    token_id: UUID
    agent_id: str
    org_id: str
    authority_level: str
    department: str
    scope: list[str]
    max_token_budget: int
    max_execution_time_seconds: int
    issued_at: datetime
    expires_at: datetime
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class KillScope:
    scope_type: str  # agent|department|tenant|platform
    scope_id: str  # agent_id, department, org_id, or 'platform'
    org_id: str


@dataclass(frozen=True, slots=True)
class AgentStateRow:
    agent_id: str
    org_id: str
    state: str  # active|suspended|killed


@dataclass(frozen=True, slots=True)
class AuditRow:
    event_id: UUID
    org_id: str
    correlation_id: UUID
    action_type: str
    result: str  # success|denied|escalated|failed
    occurred_at: datetime
    causation_id: UUID | None = None
    source_agent_id: str | None = None
    authority_level: str | None = None
    governance_token_id: UUID | None = None
    inputs_hash: str | None = None
    outputs_hash: str | None = None
    result_reason: str | None = None


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

class GovernanceRepository(Protocol):
    """Durable governance state. The in-memory snapshot in the Authority is the
    hot path for validation; this is the system of record."""

    async def insert_token(self, row: TokenRow) -> None: ...

    async def revoke_token(self, token_id: UUID, reason: str, when: datetime) -> None: ...

    async def is_token_revoked(self, token_id: UUID) -> bool: ...

    async def set_agent_state(
        self, agent_id: str, org_id: str, state: str, reason: str | None
    ) -> None: ...

    async def get_agent_state(self, agent_id: str, org_id: str) -> str: ...

    async def increment_circuit_breaker(self, agent_id: str, org_id: str) -> int:
        """Increment and return the new trip count for the agent."""
        ...

    async def engage_kill_switch(
        self, scope: KillScope, engaged_by: str, reason: str
    ) -> None: ...

    async def disengage_kill_switch(self, scope: KillScope, disengaged_by: str) -> None: ...

    async def active_kill_scopes(self, org_id: str) -> list[KillScope]: ...

    # -- rehydration reads (snapshot warm-up on Authority startup) ----------

    async def revoked_token_ids(self) -> list[UUID]:
        """All currently-revoked token_ids (revoked_at IS NOT NULL)."""
        ...

    async def non_active_agents(self) -> list[AgentStateRow]:
        """Every (agent, org) whose live state is not 'active' (suspended/killed)."""
        ...

    async def all_active_kill_scopes(self) -> list[KillScope]:
        """Active kill scopes across all tenants (platform-wide rehydration)."""
        ...


class AuditRepository(Protocol):
    async def append(self, row: AuditRow) -> None: ...

    async def list_for_org(
        self, org_id: str, *, limit: int = 50, before: datetime | None = None
    ) -> list[AuditRow]:
        """Newest-first audit rows for one org; `before` pages backwards in time."""
        ...


class ContractRepository(Protocol):
    async def upsert(self, agent_id: str, version: int, contract_json: str) -> None: ...

    async def load_all_active(self) -> list[tuple[str, str]]:
        """All active contracts as (agent_id, contract_json), latest version per agent."""
        ...

    async def get_latest_active(self, agent_id: str) -> str | None:
        """Latest active contract_json for one agent, or None if absent."""
        ...


# ---------------------------------------------------------------------------
# Tenant & Auth (Subsystem 1) — rows
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TenantRow:
    org_id: str
    display_name: str
    oidc_issuer: str
    status: str  # active|suspended|killed
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantUserRow:
    user_id: str
    org_id: str
    role: str  # owner|admin|operator|analyst|viewer
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyRow:
    key_id: UUID
    org_id: str
    prefix: str  # public lookup handle (indexed, unique)
    key_hash: str  # SHA-256 of the secret; the plaintext is never stored
    name: str
    scopes: list[str]
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


# ---------------------------------------------------------------------------
# Tenant & Auth — ports
# ---------------------------------------------------------------------------

class TenantRepository(Protocol):
    """Platform-level tenant + user store (no RLS — auth-layer visibility)."""

    async def create_tenant(self, row: TenantRow) -> None: ...

    async def get_tenant(self, org_id: str) -> TenantRow | None: ...

    async def set_status(self, org_id: str, status: str) -> None: ...

    async def add_user(self, row: TenantUserRow) -> None:
        """Insert or update a user's role (idempotent on (user_id, org_id))."""
        ...

    async def get_user(self, user_id: str, org_id: str) -> TenantUserRow | None: ...

    async def list_users(self, org_id: str) -> list[TenantUserRow]: ...

    async def remove_user(self, user_id: str, org_id: str) -> None: ...


class ApiKeyRepository(Protocol):
    """API-key store. Lookup at auth time is by `prefix` (cross-tenant); all
    management reads/writes are scoped by `org_id` at the call site."""

    async def insert(self, row: ApiKeyRow) -> None: ...

    async def get_by_prefix(self, prefix: str) -> ApiKeyRow | None: ...

    async def list_for_org(self, org_id: str) -> list[ApiKeyRow]: ...

    async def revoke(self, key_id: UUID, org_id: str, reason: str, when: datetime) -> None: ...

    async def touch_last_used(self, key_id: UUID, when: datetime) -> None: ...


# ---------------------------------------------------------------------------
# Users & auth (human RBAC layer — distinct from the agent authority layer)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UserRow:
    user_id: UUID
    org_id: str
    email: str
    password_hash: str
    roles: list[str]
    is_active: bool
    created_at: datetime
    display_name: str | None = None
    last_login_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RefreshTokenRow:
    token_id: UUID
    user_id: UUID
    expires_at: datetime
    revoked_at: datetime | None = None


class UserRepository(Protocol):
    """Human user store + refresh-token lifecycle. Email lookup is cross-tenant
    (login); everything else is scoped by `org_id` at the call site."""

    async def create_user(self, row: UserRow) -> None: ...

    async def get_by_email(self, email: str) -> UserRow | None: ...

    async def get_by_id(self, user_id: UUID) -> UserRow | None: ...

    async def list_by_org(self, org_id: str) -> list[UserRow]: ...

    async def update_last_login(self, user_id: UUID, when: datetime) -> None: ...

    async def store_refresh_token(
        self, token_id: UUID, user_id: UUID, expires_at: datetime
    ) -> None: ...

    async def get_refresh_token(self, token_id: UUID) -> RefreshTokenRow | None: ...

    async def revoke_refresh_token(self, token_id: UUID) -> None: ...


# ---------------------------------------------------------------------------
# Deliverables (agent-produced artifacts, versioned, human-approvable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DeliverableRow:
    id: UUID
    org_id: str
    agent_id: str
    deliverable_type: str
    title: str
    content_markdown: str
    summary: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    governance_token_id: UUID | None = None
    parent_id: UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    approved_at: datetime | None = None
    approved_by: str | None = None


class DeliverableRepository(Protocol):
    """Store for agent-produced deliverables. Every read/write is `org_id`-scoped
    (tenant isolation at IF-DATA); approval mutates status + approver fields."""

    async def create(self, row: DeliverableRow) -> None: ...

    async def get_by_id(self, id: UUID, org_id: str) -> DeliverableRow | None: ...

    async def list_by_org(
        self,
        org_id: str,
        *,
        status: str | None = None,
        deliverable_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DeliverableRow], int]: ...

    async def update_status(self, id: UUID, org_id: str, status: str) -> bool: ...

    async def update_approved(
        self, id: UUID, org_id: str, approved_by: str, approved_at: datetime
    ) -> bool: ...

    async def list_versions(
        self, org_id: str, deliverable_id: UUID
    ) -> list[DeliverableRow]: ...


# ---------------------------------------------------------------------------
# Memory write port (the DAL owns SQL; the memory layer owns hashing/events)
# ---------------------------------------------------------------------------

class MemoryWritePort(Protocol):
    """What `memory/repository.py` needs from the DAL: an idempotent fact
    upsert (ON CONFLICT collapses to provenance-append + reinforcement) and a
    scoped point read. Tenant isolation is enforced by `org_id` in the key."""

    async def upsert_fact(
        self,
        *,
        org_id: str,
        namespace: str,
        tier: str,
        fact_hash: str,
        content_text: str,
        provenance_entry: "ProvenanceEntry",
        created_by_agent: str,
        half_life_seconds: float,
        reinforcement: float,
    ) -> "MemoryWriteOutcome": ...

    async def get_fact(
        self, *, org_id: str, namespace: str, fact_hash: str
    ) -> Any | None: ...


# ---------------------------------------------------------------------------
# Capital allocation (Decision Engine stage 4) + idempotency
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BudgetCeiling:
    org_id: str
    scope: str  # department/capital scope, e.g. "growth"
    ceiling_minor_units: int
    committed_minor_units: int = 0


class CapitalRepository(Protocol):
    """The budget ledger read the evaluator consults at stage 4. Tightest ceiling
    wins; a missing ceiling fails closed (defers to human)."""

    async def get_ceiling(self, org_id: str, scope: str) -> BudgetCeiling | None: ...


class ProcessedEventStore(Protocol):
    """Idempotency guard: the async engine decides each `event_id` at most once.

    `org_id` scopes every read/write to one tenant so the Postgres
    implementation can run inside `tenant_session(org_id)` and stay subject to
    RLS — a bare key would force a cross-tenant table. The engine always has
    the tenant at hand (`event.tenant_id`), so callers pass it through."""

    async def is_processed(self, key: str, *, org_id: str) -> bool: ...

    async def mark_processed(self, key: str, outcome: str, *, org_id: str) -> None: ...


# ---------------------------------------------------------------------------
# HITL escalation write (synchronous request-path decision gate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HitlEscalation:
    """One human-in-the-loop escalation the SYNCHRONOUS decision gate persists.

    Carries BOTH the parent `decisions` row and the `hitl_queue` row fields:
    `hitl_queue.decision_id` is an FK to `decisions`, and the synchronous request
    path (unlike the async OPA publisher) has no separate decisions projection to
    write the parent first. The `hitl_queue` fields mirror the columns the async
    writer (`decision_engine/hitl_writer.py`) populates, so a row from either
    writer is schema-compatible (owner decision K3)."""

    # -- parent `decisions` row --------------------------------------------
    decision_id: UUID
    org_id: str
    correlation_id: UUID
    causation_event_id: UUID | None
    partition_key: str
    proposing_agent: str
    authority_level: str
    action_kind: str
    proposal_json: dict[str, Any]
    outcome: str  # "deferred_to_human"
    outcome_reason: str | None
    policy_version: str | None
    score_json: dict[str, Any] | None
    governance_token_id: UUID | None
    # -- child `hitl_queue` row (the id + escalation reason + lifecycle) ----
    hitl_id: UUID
    trigger_reason: str
    expires_at: datetime
    created_at: datetime
    # Serialized HitlReplayEnvelope (schemas/hitl.py) — what a human approval
    # executes (owner decisions K4/K6). None = no replayable execution (the
    # OPA-side writer never sets it).
    request_json: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class HitlQueueItem:
    """One hitl_queue row as read back for the review/approval path."""

    hitl_id: UUID
    org_id: str
    decision_id: UUID | None
    correlation_id: UUID
    partition_key: str
    trigger_reason: str
    proposal_json: dict[str, Any]
    request_json: dict[str, Any] | None
    status: str  # pending|approved|rejected|modified|expired
    verdict_by: str | None
    verdict_json: dict[str, Any] | None
    verdict_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class HitlQueueRepository(Protocol):
    """Writes a human-in-the-loop escalation (and its parent decision) for the
    SYNCHRONOUS request-path decision gate, and serves the review/approval
    reads + the exactly-once verdict claim.

    The async OPA engine has its OWN writer (`decision_engine/hitl_writer.py`);
    this is the request-path sibling. Nothing on the request path imports the
    `decision_engine` package (owner decision K3)."""

    async def enqueue(self, escalation: HitlEscalation) -> None: ...

    async def list_pending(
        self, org_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[HitlQueueItem], int]:
        """Newest-first pending rows for one org (partial index
        idx_hitl_org_pending) plus the total pending count."""
        ...

    async def get(self, hitl_id: UUID, org_id: str) -> HitlQueueItem | None: ...

    async def claim(
        self,
        hitl_id: UUID,
        org_id: str,
        *,
        status_to: str,  # 'approved' | 'rejected'
        verdict_by: str,
        verdict_json: dict[str, Any],
        verdict_at: datetime,
        require_request: bool,
    ) -> HitlQueueItem | None:
        """The exactly-once guard: a CONDITIONAL status update, never a
        read-then-write. Flips status and records the verdict only when the row
        is still 'pending', not past expires_at, and (when `require_request`)
        carries a replayable request_json. Returns the claimed row, or None if
        the predicate did not match (caller re-reads to type the refusal)."""
        ...

    async def release(self, hitl_id: UUID, org_id: str, *, from_status: str) -> bool:
        """Return a claimed row to 'pending' (verdict fields cleared) after a
        failed execution, so the approved work is never silently lost. Only a
        row currently in `from_status` is released."""
        ...

    async def update_verdict_json(
        self, hitl_id: UUID, org_id: str, verdict_json: dict[str, Any]
    ) -> None:
        """Enrich the recorded verdict (e.g. with the produced deliverable_id)."""
        ...


# ---------------------------------------------------------------------------
# Workflow orchestration (Temporal activities) — run-step audit trail
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WorkflowRunStepRow:
    step_id: UUID
    run_id: UUID
    org_id: str
    step_name: str
    step_order: int
    agent_id: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    judge_verdict: dict[str, Any] | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    completed_at: datetime | None


class WorkflowRepository(Protocol):
    """Durable record of each workflow node's execution, written by the
    Temporal `write_run_step` activity to the `workflow_run_steps` table."""

    async def record_step(self, row: WorkflowRunStepRow) -> None: ...
