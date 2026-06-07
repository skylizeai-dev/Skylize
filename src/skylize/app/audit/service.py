"""
Audit Logging service.

Every governed action records an immutable `AuditEvent` (agent_governance.md §10).
The service does two writes that together make the system reconstructable:
  1. publishes an `audit.action_recorded` event to the bus (replay/observability);
  2. appends an `AuditRow` to the object-lock-equivalent `audit_log` table
     (append-only; the DB trigger blocks UPDATE/DELETE).

Inputs/outputs are recorded as SHA-256 hashes only — PII never enters the audit
trail in clear (observability.md §5).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ...dal.ports import AuditRepository, AuditRow
from ...events.bus import EventBus
from ...schemas.base import AuthorityLevelLiteral
from ...schemas.events.audit import AuditActionRecorded


def hash_payload(value: Any) -> str | None:
    """Stable SHA-256 hex of any JSON-serializable value (PII-safe). None -> None."""
    if value is None:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AuditService:
    def __init__(self, bus: EventBus, repo: AuditRepository) -> None:
        self._bus = bus
        self._repo = repo

    async def record(
        self,
        *,
        org_id: str,
        correlation_id: UUID,
        action_type: str,
        result: str,  # success|denied|escalated|failed
        source_agent_id: str | None = None,
        authority_level: AuthorityLevelLiteral | None = None,
        governance_token_id: UUID | None = None,
        causation_id: UUID | None = None,
        inputs: Any = None,
        outputs: Any = None,
        result_reason: str | None = None,
        partition_key: str | None = None,
    ) -> UUID:
        """Record one audited action; returns the audit event_id."""
        now = datetime.now(timezone.utc)
        inputs_hash = hash_payload(inputs)
        outputs_hash = hash_payload(outputs)
        event = AuditActionRecorded(
            tenant_id=org_id,
            partition_key=partition_key or str(correlation_id),
            department="audit",
            source_agent_id=source_agent_id,
            authority_level=authority_level,
            governance_token_id=governance_token_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            payload=AuditActionRecorded.Payload(
                action_type=action_type,
                inputs_hash=inputs_hash,
                outputs_hash=outputs_hash,
                result=result,
                result_reason=result_reason,
            ),
        )
        await self._bus.publish(event)
        await self._repo.append(
            AuditRow(
                event_id=event.event_id,
                org_id=org_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                source_agent_id=source_agent_id,
                authority_level=authority_level,
                governance_token_id=governance_token_id,
                action_type=action_type,
                inputs_hash=inputs_hash,
                outputs_hash=outputs_hash,
                result=result,
                result_reason=result_reason,
                occurred_at=now,
            )
        )
        return event.event_id
