"""Principal authority, spend ledger, and work journal — the per-employee kernel.

Compiles a human principal's authority into an effective scope set, provides
attenuation-only delegation to sub-agents, enforces a spend ceiling via atomic
reservations, and gives the co-work agent a single append-only journal to read
from.

NOT YET WIRED: nothing here is called by `GovernanceAuthority.mint()`, the tool
proxy, or any edge route. See the module docstrings for the specific integration
points and what each one still requires.
"""

from __future__ import annotations

from .authority import (
    assert_snapshot_current,
    attenuate_for_subagent,
    compile_authority,
    fingerprint_scopes,
    resolve_effective_scope,
)
from .errors import (
    AuthorityExceeded,
    BudgetError,
    CeilingExceeded,
    EnvelopeNotFound,
    ExpiryExtensionDenied,
    PrincipalError,
    PrincipalSuspended,
    ReservationConflict,
    StaleAuthority,
)
from .journal import BriefBlock, JournalRepository, WorkJournal, assemble_brief
from .models import (
    ActorKind,
    AuthorityLevel,
    AuthoritySnapshot,
    Grant,
    GrantSource,
    JournalCursor,
    JournalEntry,
    OnBehalfOf,
    Principal,
    Reservation,
    ScopeId,
    SpendEnvelope,
)
from .spend import DEFAULT_HOLD_TTL, PostgresSpendRepository, SpendLedger, SpendRepository

__all__ = [
    "DEFAULT_HOLD_TTL",
    "ActorKind",
    "AuthorityExceeded",
    "AuthorityLevel",
    "AuthoritySnapshot",
    "BriefBlock",
    "BudgetError",
    "CeilingExceeded",
    "EnvelopeNotFound",
    "ExpiryExtensionDenied",
    "Grant",
    "GrantSource",
    "JournalCursor",
    "JournalEntry",
    "JournalRepository",
    "OnBehalfOf",
    "PostgresSpendRepository",
    "Principal",
    "PrincipalError",
    "PrincipalSuspended",
    "Reservation",
    "ReservationConflict",
    "ScopeId",
    "SpendEnvelope",
    "SpendLedger",
    "SpendRepository",
    "StaleAuthority",
    "WorkJournal",
    "assemble_brief",
    "assert_snapshot_current",
    "attenuate_for_subagent",
    "compile_authority",
    "fingerprint_scopes",
    "resolve_effective_scope",
]
