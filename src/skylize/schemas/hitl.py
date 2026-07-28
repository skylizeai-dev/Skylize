"""The HITL replay envelope — ``hitl_queue.request_json``, typed at both ends.

Owner decision K6: what a human approval executes is never a loose dict. The
synchronous decision gate serializes THIS model into the dedicated
``request_json`` column at enqueue time (owner decision K4), and the approval
path re-parses it with this same model before re-validating the payload against
the agent's CURRENT input schema (owner decision K7).

Field inventory (K6 "anything else execute() needs that is not derivable at
approval time"):
  * ``agent_id``       — which contract to run.
  * ``input``          — the ALREADY-VALIDATED customer input, dumped in JSON
                         mode. Re-validated against the current schema on
                         approval; never trusted as-is.
  * ``user_id``        — the requesting principal, for the deliverable metadata
                         and audit parity with the original call.
  * ``correlation_id`` — the ORIGINAL request correlation, recorded as
                         ``causation_id`` on the replay's audit records so the
                         defer -> approve -> execute chain is traceable (K8).
``org_id`` is deliberately ABSENT: it is derived from the authenticated
principal and the RLS-scoped row, never from a stored payload that could
disagree with them.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HitlReplayEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    user_id: str
    correlation_id: UUID
