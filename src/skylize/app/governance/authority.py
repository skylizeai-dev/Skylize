"""
The Governance Authority (Application Boundary).

The single component that mints and revokes governance tokens, runs the circuit
breaker, and engages/disengages the kill switch (agent_governance.md §4, §7, §8).
It holds the ECDSA P-384 signing key and the live-state snapshot; it persists to
the DB and mirrors every transition as a governance + audit event.

It does NOT validate tool calls itself — validation is decentralized: the tool
proxy / orchestrator call `token.validate_tool_call` with the public key and the
`live_state_checker(org_id)` this Authority exposes.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

from ...config import Settings
from ...contracts.base import AgentContract, GovernanceToken
from ...contracts.registry import AgentRegistry
from ...contracts.token import LiveStateChecker, TokenSigner
from ...dal.ports import GovernanceRepository, KillScope, TokenRow
from ...events.bus import EventBus
from ...schemas.base import BaseEvent
from ...schemas.events.governance import (
    GovernanceAgentReinstated,
    GovernanceAgentSuspended,
    GovernanceCircuitBreakerTripped,
    GovernanceKillSwitchDisengaged,
    GovernanceKillSwitchEngaged,
    GovernanceTokenIssued,
    GovernanceTokenRevoked,
)
from ..audit.service import AuditService
from .broadcast import (
    GovernanceBroadcast,
    GovernanceInvalidation,
    InMemoryGovernanceBroadcast,
    InvalidationKind,
)
from .keys import load_signing_key
from .snapshot import GovernanceSnapshot

CIRCUIT_BREAKER_THRESHOLD = 3  # scope violations within an agent before suspend

# Convergence breaker: an agent repeating the *same* action back-to-back inside a
# workflow is the "runaway loop" trip condition in agent_governance.md §7. We trip
# as soon as an action_hash recurs immediately (twice consecutively); the ring
# buffer keeps the last few hashes per agent per workflow purely for observability
# (so the trip reason can show the repeated tail).
CONVERGENCE_RING_SIZE = 3
CONVERGENCE_TRIP_REASON = "convergence"  # GovernanceCircuitBreakerTripped.trip_reason


def compute_action_hash(*, agent_id: str, action_type: str, action_args: Any) -> str:
    """SHA-256 hex of ``{agent_id, action_type, action_args}`` (canonical JSON).

    Same canonical-JSON discipline as the audit trail and the tool-exec
    fingerprint: ``sort_keys`` + tight separators + ``default=str`` so two
    semantically-identical actions hash identically regardless of key ordering.
    """
    body = json.dumps(
        {"agent_id": agent_id, "action_type": action_type, "action_args": action_args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ConvergenceTracker:
    """Per-(workflow, agent) ring buffer of recent action hashes.

    Keyed by ``(correlation_id, agent_id)`` — a workflow is one ``correlation_id``
    (event_driven_architecture.md §8), so this is scoped per agent per workflow as
    required. In-process only: convergence is a hot-path heuristic evaluated where
    the action originates, mirroring how ``GovernanceSnapshot`` keeps live state in
    memory rather than reaching for the DAL.
    """

    def __init__(self, ring_size: int = CONVERGENCE_RING_SIZE) -> None:
        self._ring_size = ring_size
        self._rings: dict[tuple[UUID, str], deque[str]] = {}

    def record(self, correlation_id: UUID, agent_id: str, action_hash: str) -> bool:
        """Append an action hash; return True iff it repeats the previous one.

        "Same action_hash twice consecutively" is the trip signal. The hash is
        appended either way so the buffer reflects the true recent tail.
        """
        key = (correlation_id, agent_id)
        ring = self._rings.get(key)
        if ring is None:
            ring = deque(maxlen=self._ring_size)
            self._rings[key] = ring
        is_consecutive_repeat = bool(ring) and ring[-1] == action_hash
        ring.append(action_hash)
        return is_consecutive_repeat

    def recent(self, correlation_id: UUID, agent_id: str) -> list[str]:
        """Snapshot of the current ring tail (oldest→newest), for diagnostics."""
        ring = self._rings.get((correlation_id, agent_id))
        return list(ring) if ring is not None else []

    def reset(self, correlation_id: UUID, agent_id: str) -> None:
        """Drop the buffer for a (workflow, agent) — called once a trip fires so a
        third identical action does not re-trip / re-escalate (idempotency)."""
        self._rings.pop((correlation_id, agent_id), None)


class GovernanceDenied(Exception):
    """Raised when governance state forbids an action (suspended / killed)."""


class _BoundLiveStateChecker:
    """Sync LiveStateChecker bound to one tenant, reading the hot snapshot."""

    def __init__(self, snapshot: GovernanceSnapshot, org_id: str) -> None:
        self._snapshot = snapshot
        self._org_id = org_id

    def revocation_reason(self, token_id: UUID, agent_id: str) -> str | None:
        return self._snapshot.reason_for(token_id, agent_id, self._org_id)


class GovernanceAuthority:
    def __init__(
        self,
        *,
        signer: TokenSigner,
        public_key: EllipticCurvePublicKey,
        repo: GovernanceRepository,
        audit: AuditService,
        bus: EventBus,
        registry: AgentRegistry,
        settings: Settings,
        broadcast: GovernanceBroadcast | None = None,
    ) -> None:
        self._signer = signer
        self._public_key = public_key
        self._repo = repo
        self._audit = audit
        self._bus = bus
        self._registry = registry
        self._settings = settings
        self._snapshot = GovernanceSnapshot()
        self._convergence = ConvergenceTracker()
        # Cross-process propagation. Defaults to in-process fan-out so a single
        # Authority (tests / memory backend) still works with no broadcast wired.
        self._broadcast: GovernanceBroadcast = broadcast or InMemoryGovernanceBroadcast()

    # -- factory ------------------------------------------------------------
    @classmethod
    def build(
        cls,
        *,
        repo: GovernanceRepository,
        audit: AuditService,
        bus: EventBus,
        registry: AgentRegistry,
        settings: Settings,
        broadcast: GovernanceBroadcast | None = None,
    ) -> "GovernanceAuthority":
        """Build with the configured signing key (Task 3 validates it is present)."""
        pair = load_signing_key(settings)
        return cls(
            signer=TokenSigner(pair.private_key),
            public_key=pair.public_key,
            repo=repo, audit=audit, bus=bus, registry=registry, settings=settings,
            broadcast=broadcast,
        )

    # -- lifecycle: rehydrate + subscribe ----------------------------------
    async def rehydrate(self) -> None:
        """Warm the in-memory snapshot from the DB system of record.

        Closes the "restart forgets the kill switch" hole: a freshly built
        Authority loads active kills, revoked tokens, and suspended agents
        before it serves a single request.
        """
        for scope in await self._repo.all_active_kill_scopes():
            self._apply_kill(scope.scope_type, scope.scope_id, scope.org_id, engaged=True)
        for token_id in await self._repo.revoked_token_ids():
            self._snapshot.revoke(token_id)
        for row in await self._repo.non_active_agents():
            self._snapshot.set_agent_state(row.agent_id, row.org_id, row.state)

    async def start_subscriber(self) -> None:
        """Subscribe to cross-instance invalidations (run as a background task)."""
        await self._broadcast.subscribe(self._apply_invalidation)

    async def _apply_invalidation(self, msg: GovernanceInvalidation) -> None:
        """Apply an invalidation from another instance to the local snapshot."""
        if msg.kind is InvalidationKind.REVOKE and msg.token_id is not None:
            self._snapshot.revoke(msg.token_id)
        elif msg.kind is InvalidationKind.AGENT_STATE and msg.agent_id and msg.org_id:
            self._snapshot.set_agent_state(msg.agent_id, msg.org_id, msg.state or "active")
        elif msg.kind is InvalidationKind.KILL_TENANT and msg.org_id is not None:
            (self._snapshot.kill_tenant if msg.engaged else self._snapshot.unkill_tenant)(
                msg.org_id
            )
        elif msg.kind is InvalidationKind.KILL_PLATFORM:
            (self._snapshot.kill_platform if msg.engaged else self._snapshot.unkill_platform)()

    # -- accessors ----------------------------------------------------------
    @property
    def public_key(self) -> EllipticCurvePublicKey:
        return self._public_key

    def live_state_checker(self, org_id: str) -> LiveStateChecker:
        return _BoundLiveStateChecker(self._snapshot, org_id)

    # -- gate ---------------------------------------------------------------
    async def assert_active(self, agent_id: str, org_id: str) -> None:
        """Raise GovernanceDenied if the agent is suspended or kill-switched."""
        reason = self._snapshot.reason_for(None, agent_id, org_id)
        if reason is not None:
            raise GovernanceDenied(reason)

    # -- minting ------------------------------------------------------------
    async def mint(
        self,
        contract: AgentContract,
        *,
        org_id: str,
        correlation_id: UUID,
        scope: list[str] | None = None,
        delegation_chain: list[str] | None = None,
    ) -> GovernanceToken:
        await self.assert_active(contract.agent_id, org_id)
        now = datetime.now(timezone.utc)
        token = self._signer.sign(
            token_id=uuid4(),
            agent_id=contract.agent_id,
            authority_level=contract.authority_level,
            department=contract.department,
            delegation_chain=delegation_chain or [contract.agent_id],
            scope=scope if scope is not None else [t.tool_id for t in contract.allowed_tools],
            max_token_budget=contract.max_token_budget,
            max_execution_time_seconds=contract.max_execution_time_seconds,
            issued_at=now,
            expires_at=now + timedelta(minutes=self._settings.token_ttl_minutes),
            nonce=uuid4().hex,
        )
        await self._repo.insert_token(
            TokenRow(
                token_id=token.token_id, agent_id=token.agent_id, org_id=org_id,
                authority_level=token.authority_level, department=token.department,
                scope=token.scope, max_token_budget=token.max_token_budget,
                max_execution_time_seconds=token.max_execution_time_seconds,
                issued_at=token.issued_at, expires_at=token.expires_at,
                correlation_id=correlation_id,
            )
        )
        await self._emit(
            GovernanceTokenIssued(
                tenant_id=org_id, partition_key=f"agent:{token.agent_id}",
                department=token.department, source_agent_id=token.agent_id,
                authority_level=token.authority_level, governance_token_id=token.token_id,
                correlation_id=correlation_id,
                payload=GovernanceTokenIssued.Payload(
                    token_id=token.token_id, agent_id=token.agent_id,
                    authority_level=token.authority_level, scope=token.scope,
                    expires_at=token.expires_at.isoformat(),
                ),
            )
        )
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="governance.token_issued", result="success",
            source_agent_id=token.agent_id, authority_level=token.authority_level,
            governance_token_id=token.token_id,
        )
        return token

    # -- revocation ---------------------------------------------------------
    async def revoke(
        self, *, token_id: UUID, agent_id: str, org_id: str, reason: str, correlation_id: UUID
    ) -> None:
        self._snapshot.revoke(token_id)  # hot path: effective immediately, locally
        await self._repo.revoke_token(token_id, reason, datetime.now(timezone.utc))
        await self._broadcast.publish(  # fan out to every other instance
            GovernanceInvalidation(kind=InvalidationKind.REVOKE, token_id=token_id)
        )
        await self._emit(
            GovernanceTokenRevoked(
                tenant_id=org_id, partition_key=f"agent:{agent_id}", department="governance",
                source_agent_id=agent_id, governance_token_id=token_id,
                correlation_id=correlation_id,
                payload=GovernanceTokenRevoked.Payload(
                    token_id=token_id, agent_id=agent_id, reason=reason
                ),
            )
        )
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="governance.token_revoked", result="success",
            source_agent_id=agent_id, governance_token_id=token_id, result_reason=reason,
        )

    # -- circuit breaker ----------------------------------------------------
    async def record_violation(
        self, *, agent_id: str, org_id: str, reason: str, correlation_id: UUID
    ) -> bool:
        """Count a scope violation; suspend the agent if the threshold is crossed.

        Returns True if this violation tripped the breaker.
        """
        trips = await self._repo.increment_circuit_breaker(agent_id, org_id)
        if trips < CIRCUIT_BREAKER_THRESHOLD:
            return False
        await self._suspend_and_emit(
            agent_id=agent_id, org_id=org_id, correlation_id=correlation_id,
            trip_reason=reason, trip_count=trips, suspend_reason=reason,
            audit_action_type="governance.circuit_breaker_tripped",
        )
        return True

    async def record_action(
        self,
        *,
        agent_id: str,
        org_id: str,
        correlation_id: UUID,
        action_type: str,
        action_args: Any,
    ) -> bool:
        """Track one agent action within a workflow; trip on convergence.

        Convergence = the agent emitting the *same* ``action_hash`` twice
        consecutively within the workflow (`correlation_id`), i.e. a runaway loop
        (agent_governance.md §7). On trip the agent is suspended (same machinery as
        the scope-violation breaker, so suspension state stays single-source), a
        ``governance.circuit_breaker_tripped`` event is emitted with
        ``trip_reason="convergence"``, and the failure is escalated along the
        agent's ``escalation_path`` — recorded as a ``governance.convergence_failure``
        audit action.

        Returns True iff this action tripped the convergence breaker. Tripping is
        idempotent per (workflow, agent): the ring buffer is cleared on trip and a
        suspended agent is not re-tripped, so a third identical action neither
        re-suspends nor re-escalates.
        """
        # Already stopped (suspended/killed/tenant-or-platform kill)? Do not
        # re-trip or re-escalate — keeps "escalation emitted exactly once".
        if self._snapshot.reason_for(None, agent_id, org_id) is not None:
            return False

        action_hash = compute_action_hash(
            agent_id=agent_id, action_type=action_type, action_args=action_args
        )
        if not self._convergence.record(correlation_id, agent_id, action_hash):
            return False

        # Trip. Capture the repeated tail for the trip reason, then reset so the
        # next identical action cannot re-trip this (workflow, agent).
        recent = self._convergence.recent(correlation_id, agent_id)
        self._convergence.reset(correlation_id, agent_id)
        trip_reason = f"{CONVERGENCE_TRIP_REASON}: repeated action {action_hash} in {action_type}"
        escalation_path = self._escalation_path_for(agent_id)
        await self._suspend_and_emit(
            agent_id=agent_id, org_id=org_id, correlation_id=correlation_id,
            trip_reason=trip_reason, trip_count=len(recent), suspend_reason=trip_reason,
            audit_action_type="governance.convergence_failure",
            audit_result_reason=f"escalation_path={escalation_path}",
        )
        return True

    def _escalation_path_for(self, agent_id: str) -> list[str]:
        """The agent's ordered escalation chain, or an empty list if unregistered.

        Escalation today routes via the contract's ``escalation_path`` recorded in
        the audit trail; the dedicated ``governance.human_escalation_raised`` event
        is the Decision Engine's to emit in its sprint (not yet in the registry).
        """
        try:
            return list(self._registry.resolve(agent_id).escalation_path)
        except Exception:
            return []

    async def _suspend_and_emit(
        self,
        *,
        agent_id: str,
        org_id: str,
        correlation_id: UUID,
        trip_reason: str,
        trip_count: int,
        suspend_reason: str,
        audit_action_type: str,
        audit_result_reason: str | None = None,
    ) -> None:
        """Suspend an agent and emit the breaker/suspension events + audit.

        Shared by the scope-violation breaker and the convergence breaker so both
        mutate the snapshot, persist, broadcast, and emit identically — one
        suspension code path, one source of truth.
        """
        self._snapshot.set_agent_state(agent_id, org_id, "suspended")
        await self._repo.set_agent_state(agent_id, org_id, "suspended", suspend_reason)
        await self._broadcast.publish(
            GovernanceInvalidation(
                kind=InvalidationKind.AGENT_STATE,
                agent_id=agent_id, org_id=org_id, state="suspended",
            )
        )
        await self._emit(
            GovernanceCircuitBreakerTripped(
                tenant_id=org_id, partition_key=f"agent:{agent_id}", department="governance",
                source_agent_id=agent_id, correlation_id=correlation_id,
                payload=GovernanceCircuitBreakerTripped.Payload(
                    agent_id=agent_id, trip_reason=trip_reason, trip_count=trip_count
                ),
            )
        )
        await self._emit(
            GovernanceAgentSuspended(
                tenant_id=org_id, partition_key=f"agent:{agent_id}", department="governance",
                source_agent_id=agent_id, correlation_id=correlation_id,
                payload=GovernanceAgentSuspended.Payload(
                    agent_id=agent_id, reason=suspend_reason
                ),
            )
        )
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type=audit_action_type, result="escalated",
            source_agent_id=agent_id,
            result_reason=audit_result_reason if audit_result_reason is not None else suspend_reason,
        )

    async def reinstate(
        self, *, agent_id: str, org_id: str, reinstated_by: str, correlation_id: UUID
    ) -> None:
        self._snapshot.set_agent_state(agent_id, org_id, "active")
        await self._repo.set_agent_state(agent_id, org_id, "active", None)
        await self._broadcast.publish(
            GovernanceInvalidation(
                kind=InvalidationKind.AGENT_STATE,
                agent_id=agent_id, org_id=org_id, state="active",
            )
        )
        await self._emit(
            GovernanceAgentReinstated(
                tenant_id=org_id, partition_key=f"agent:{agent_id}", department="governance",
                source_agent_id=agent_id, correlation_id=correlation_id,
                payload=GovernanceAgentReinstated.Payload(
                    agent_id=agent_id, reinstated_by=reinstated_by
                ),
            )
        )

    # -- kill switch --------------------------------------------------------
    async def engage_kill_switch(
        self,
        *,
        scope_type: str,  # agent|department|tenant|platform
        scope_id: str,
        org_id: str,
        engaged_by: str,
        reason: str,
        correlation_id: UUID,
    ) -> None:
        self._apply_kill(scope_type, scope_id, org_id, engaged=True)
        await self._repo.engage_kill_switch(
            KillScope(scope_type=scope_type, scope_id=scope_id, org_id=org_id),
            engaged_by, reason,
        )
        await self._broadcast_kill(scope_type, scope_id, org_id, engaged=True)
        await self._emit(
            GovernanceKillSwitchEngaged(
                tenant_id=org_id, partition_key=f"kill:{scope_type}:{scope_id}",
                department="governance", correlation_id=correlation_id,
                payload=GovernanceKillSwitchEngaged.Payload(
                    scope_type=scope_type, scope_id=scope_id,
                    engaged_by=engaged_by, reason=reason,
                ),
            )
        )
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="governance.kill_switch_engaged", result="success",
            result_reason=f"{scope_type}:{scope_id} by {engaged_by}: {reason}",
        )

    async def disengage_kill_switch(
        self, *, scope_type: str, scope_id: str, org_id: str, disengaged_by: str,
        correlation_id: UUID,
    ) -> None:
        self._apply_kill(scope_type, scope_id, org_id, engaged=False)
        await self._repo.disengage_kill_switch(
            KillScope(scope_type=scope_type, scope_id=scope_id, org_id=org_id), disengaged_by
        )
        await self._broadcast_kill(scope_type, scope_id, org_id, engaged=False)
        await self._emit(
            GovernanceKillSwitchDisengaged(
                tenant_id=org_id, partition_key=f"kill:{scope_type}:{scope_id}",
                department="governance", correlation_id=correlation_id,
                payload=GovernanceKillSwitchDisengaged.Payload(
                    scope_type=scope_type, scope_id=scope_id, disengaged_by=disengaged_by
                ),
            )
        )
        await self._audit.record(
            org_id=org_id, correlation_id=correlation_id,
            action_type="governance.kill_switch_disengaged", result="success",
        )

    # -- internals ----------------------------------------------------------
    def _apply_kill(self, scope_type: str, scope_id: str, org_id: str, *, engaged: bool) -> None:
        if scope_type == "platform":
            self._snapshot.kill_platform() if engaged else self._snapshot.unkill_platform()
        elif scope_type == "tenant":
            self._snapshot.kill_tenant(scope_id) if engaged else self._snapshot.unkill_tenant(scope_id)
        elif scope_type == "agent":
            self._snapshot.set_agent_state(scope_id, org_id, "killed" if engaged else "active")
        elif scope_type == "department":
            # Expand to every registered agent in that department.
            for contract in self._registry.all():
                if contract.department == scope_id:
                    self._snapshot.set_agent_state(
                        contract.agent_id, org_id, "killed" if engaged else "active"
                    )
        else:
            raise ValueError(f"unknown kill scope_type: {scope_type}")

    async def _broadcast_kill(
        self, scope_type: str, scope_id: str, org_id: str, *, engaged: bool
    ) -> None:
        """Fan a kill engage/disengage out to every instance's snapshot.

        Mirrors `_apply_kill`'s per-scope mutation so remote snapshots reach the
        same state. Agent/department scopes expand to per-agent AGENT_STATE
        messages; tenant/platform use their dedicated invalidation kinds.
        """
        state = "killed" if engaged else "active"
        if scope_type == "platform":
            await self._broadcast.publish(
                GovernanceInvalidation(kind=InvalidationKind.KILL_PLATFORM, engaged=engaged)
            )
        elif scope_type == "tenant":
            await self._broadcast.publish(
                GovernanceInvalidation(
                    kind=InvalidationKind.KILL_TENANT, org_id=scope_id, engaged=engaged
                )
            )
        elif scope_type == "agent":
            await self._broadcast.publish(
                GovernanceInvalidation(
                    kind=InvalidationKind.AGENT_STATE,
                    agent_id=scope_id, org_id=org_id, state=state,
                )
            )
        elif scope_type == "department":
            for contract in self._registry.all():
                if contract.department == scope_id:
                    await self._broadcast.publish(
                        GovernanceInvalidation(
                            kind=InvalidationKind.AGENT_STATE,
                            agent_id=contract.agent_id, org_id=org_id, state=state,
                        )
                    )

    async def _emit(self, event: BaseEvent) -> None:
        await self._bus.publish(event)
