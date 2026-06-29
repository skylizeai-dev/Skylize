"""Compression boundary schemas — the wire shapes of the context-compression proxy.

The Model Context Engine sits between agent context assembly and the LLM Gateway
egress, shrinking tool payloads and memory recall before they enter the model
context window. This module carries the two boundary types that cross the
compression module lines (coding_standards.md §3, §8):

  - `CompressionContext`  — the per-call input policy + correlation context.
  - `CompressionResult`   — the audited outcome, including the metadata that the
                            caller folds into an `audit.action_recorded` event
                            (event_driven_architecture.md §5).

`schemas/` is a leaf package: nothing here imports another skylize package, and
no driver/vendor SDK is reachable from this file.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CallClass(str, Enum):
    """The class of payload being compressed.

    The budget policy keys per-class thresholds off this. The taxonomy is closed
    — new payload classes are added deliberately, never as free-form strings, so
    policy stays exhaustive (canonical-vocabulary rule, README §3).
    """

    TOOL_RESULT = "tool_result"  # raw tool/integration payloads (webhooks, API bodies)
    MEMORY_RECALL = "memory_recall"  # retrieved memory chunks before context entry
    PROMPT_CONTEXT = "prompt_context"  # assembled prompt context blocks
    GENERIC = "generic"  # default when the caller does not classify


class CompressionStage(str, Enum):
    """A compression stage that actually ran on a payload.

    `stages_applied` lists these in execution order, so the audit trail shows
    exactly which transforms touched a payload and which were skipped by policy
    or degradation.
    """

    L1_DETERMINISTIC = "l1_deterministic"
    L2_SEMANTIC = "l2_semantic"
    L2_DEGRADED = "l2_degraded"  # L2 was selected by policy but fell back to L1-only


class CompressionContext(BaseModel):
    """Per-call input to `compress` — policy selectors + correlation context.

    Immutable: the context is decided by the caller (the agent runtime) and read,
    never mutated, by the pipeline. `correlation_id` is threaded through to the
    emitted audit event so a whole workflow remains traceable (coding_standards §8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: UUID
    org_id: str
    call_class: CallClass = CallClass.GENERIC

    # L2 routing inputs. `query` is the intent string chunks are scored against;
    # when absent, L2 cannot route and the pipeline runs L1-only by policy.
    query: str | None = None
    top_k: int = Field(default=5, gt=0)

    # L1 tuning. Overrides the deterministic-stage default when set.
    max_string_chars: int | None = Field(default=None, gt=0)

    # Escape hatch: force L1-only regardless of token threshold (e.g. a caller
    # that already knows the payload is small or latency-critical).
    force_l1_only: bool = False


class CompressionAudit(BaseModel):
    """The audit-facing projection of a compression decision.

    These fields are lifted verbatim into the `AuditActionRecorded` payload the
    caller emits. Holding them in a typed sub-model (rather than a raw dict)
    keeps the boundary Pydantic-validated end to end (coding_standards §3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: UUID
    org_id: str
    action_type: str  # "compression.applied" | "compression.l2_degraded"
    call_class: CallClass
    stages_applied: list[CompressionStage]
    tokens_in: int
    tokens_out: int
    ratio: float  # tokens_out / tokens_in; 1.0 when input is empty
    duration_ms: float
    degraded_reason: str | None = None  # populated only on the L2-degraded path


class CompressionResult(BaseModel):
    """The audited outcome of a `compress` call.

    `compress` is total: it always returns a `CompressionResult`, even on the
    degraded path (a failed L2 yields the L1-only text plus a degraded audit
    record). It never raises for a recoverable compression failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    compressed_text: str
    tokens_in: int
    tokens_out: int
    ratio: float
    stages_applied: list[CompressionStage]
    duration_ms: float
    audit: CompressionAudit
