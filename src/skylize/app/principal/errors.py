"""
Principal-context errors.

Mirrors the existing `ToolPermissionDenied(failed_stage=...)` shape in
`skylize.tools.base` so these route through the same denied-call audit path
rather than inventing a second denial taxonomy.

INTEGRATION (verified against HEAD, PROMPT 0 audit): `ValidationStage` in
`skylize.contracts.token` is a `str, Enum` with exactly SIGNATURE, EXPIRY,
REVOCATION, SCOPE, BUDGET, DELEGATION (contracts/token.py:185-193); the ordered
pipeline is asserted in tests/contract/test_governance_token.py. `failed_stage`
on `ToolPermissionDenied` is a plain `str | None`, not tied to that enum — the
proxy-side `ToolConvergenceDenied`/`ToolCallLimitExceeded` subclasses already
use non-enum stage strings ("convergence", "call_limit") for exactly this
reason. The stage values below (revocation/scope/budget/delegation, plus the
new "principal" default) map onto that existing vocabulary without requiring
any change to `ValidationStage` itself.
"""

from __future__ import annotations

from typing import Sequence


class PrincipalError(Exception):
    """Base. Every subclass carries `failed_stage` for the audit record."""

    failed_stage: str = "principal"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PrincipalSuspended(PrincipalError):
    """The human is deactivated/offboarded. Fail closed, immediately."""

    failed_stage = "revocation"


class StaleAuthority(PrincipalError):
    """Token was minted under an authority set that has since changed."""

    failed_stage = "revocation"


class ExpiryExtensionDenied(PrincipalError):
    """A delegated token tried to outlive its parent."""

    failed_stage = "delegation"


class AuthorityExceeded(PrincipalError):
    """Requested scope is not a subset of contract ∩ principal ∩ parent.

    Carries the exact excess so the denial is actionable in the console without
    re-running the derivation.
    """

    failed_stage = "scope"

    def __init__(
        self,
        *,
        requested: Sequence[str],
        ceiling: Sequence[str],
        excess: Sequence[str],
        principal_id: str,
    ) -> None:
        self.requested = list(requested)
        self.ceiling = list(ceiling)
        self.excess = list(excess)
        self.principal_id = principal_id
        super().__init__(
            f"principal {principal_id!r} may not delegate {excess!r}; "
            f"effective ceiling is {list(ceiling)!r}"
        )


class BudgetError(PrincipalError):
    failed_stage = "budget"


class EnvelopeNotFound(BudgetError):
    """No active envelope for (org, principal) at this instant. FAIL CLOSED —
    a missing budget is not an unlimited budget."""


class CeilingExceeded(BudgetError):
    """The reservation would push committed+held spend past the ceiling."""

    def __init__(self, reason: str, *, defer_to_human: bool) -> None:
        super().__init__(reason)
        self.defer_to_human = defer_to_human


class ReservationConflict(BudgetError):
    """Same idempotency key, different amount. Never silently reuse."""
