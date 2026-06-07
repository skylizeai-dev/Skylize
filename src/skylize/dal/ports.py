"""
Repository ports (Protocols) and the row dataclasses they move.

No driver import here — `app/` codes against these. Concrete asyncpg
implementations live in `repositories.py`; in-memory fakes in `memory.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


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


class ContractRepository(Protocol):
    async def upsert(self, agent_id: str, version: int, contract_json: str) -> None: ...


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
