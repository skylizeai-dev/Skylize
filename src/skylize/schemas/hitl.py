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
  * ``on_behalf_of_principal`` — the HUMAN whose authority the deferred action
                         was requested under, for the per-employee shape. See
                         below; it is an ID ONLY, deliberately.
``org_id`` is deliberately ABSENT: it is derived from the authenticated
principal and the RLS-scoped row, never from a stored payload that could
disagree with them.

WHY THE PRINCIPAL BINDING IS AN ID AND NOTHING ELSE
---------------------------------------------------
An approved co-work action replays hours later. What must NOT be stored is the
authority that human held at defer time — not their compiled scope set, not the
``authority_fingerprint``, not a snapshot. Storing any of those would let an
approval execute against authority that no longer exists, which is precisely the
failure the whole principal kernel exists to prevent.

Storing only the id forces the replay to RECOMPILE: ``execute()`` passes it to
``GovernanceAuthority.mint``, which calls ``_gate_principal_scope`` ->
``AuthorityProvider.snapshot_for`` -> ``compile_authority`` against the grants as
they are AT APPROVAL TIME. So a principal offboarded, suspended, or descoped
between defer and approve makes the approval REFUSE, and there is no
representation in this envelope capable of expressing "but they used to be
allowed".

Optional with a default so every row written before this field existed still
parses under ``extra="forbid"``: absent means the autonomous shape, which is what
those rows were.

THE SECOND MODEL IN THIS MODULE: ``HitlResumptionPoint``
--------------------------------------------------------
``HitlReplayEnvelope`` answers "what REQUEST would be re-run". ``HitlResumptionPoint``
answers the harder question the platform's governance claim actually needs: "what
ACTION did the human review, and how does exactly that action execute rather than
a freshly sampled one". It lives in its OWN column
(``hitl_queue.resumption_json``, migration 0027), never inside ``request_json``,
because the two have different lifecycles -- see that migration's docstring and
docs/architecture/hitl_approval_resumption_design.md section 3.2.1.

Both models obey the SAME authority rule stated above: neither stores a
governance token, a compiled scope set, or an ``authority_fingerprint``. The
resumed run re-mints and recompiles authority as it stands at approval time. The
action is frozen; the authority is not.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HitlReplayEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    user_id: str
    correlation_id: UUID
    on_behalf_of_principal: str | None = None


class HitlResumptionPoint(BaseModel):
    """The exact model turn a human reviewed, frozen for VERBATIM replay.

    Written once by the mid-loop suspension gate (app/agents/execution.py) into
    ``hitl_queue.resumption_json``; read once by ``HitlQueueService.approve``,
    which hands it to ``execute()`` on ``HitlApprovalContext.resumption``. Its
    presence is the discriminator between the two ticket shapes: absent means
    "approval re-runs the agent from ``input``" (the stage-2.5 gate, and every
    row written before migration 0027); present means "approval resumes THIS
    turn".

    WHY ``messages`` IS ``list[dict]`` AND NOT ``list[LLMMessage]``. The column
    is persisted JSON, and typing the stored shape as the adapter model would
    couple durable rows to a class that may gain fields. Validation into
    ``LLMMessage`` happens at read time, where a failure is a PERMANENT
    disposition the approval path already handles the same way it handles an
    invalid ``request_json``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The conversation prefix the reviewed turn was sampled from, INCLUDING the
    #: assistant message carrying the reviewed ``tool_use`` block(s). Serialized
    #: ``LLMMessage`` list (adapters/llm/gateway.py). The trailing assistant
    #: message is what the resumed run reads instead of sampling, and is what the
    #: eventual ``tool_result`` blocks correlate against -- the correlation is
    #: positional in the message array, so the prefix cannot be trimmed.
    messages: list[dict[str, Any]] = Field(min_length=1)

    #: Every ``tool_use_id`` in that final assistant message, in dispatch order.
    #: Redundant with ``messages[-1]`` BY DESIGN: on resume the hydrated tail is
    #: checked against this list, so a truncated or tampered snapshot is refused
    #: instead of silently dispatching a different set of calls than the human
    #: approved. Per-TURN atomicity (owner decision D4): all of these resume
    #: together, or none does.
    pending_tool_use_ids: list[str] = Field(min_length=1)

    #: Which loop iteration this turn was, so the resumed run re-enters with the
    #: REMAINING iteration budget rather than a fresh one.
    iteration: int = Field(ge=0)

    #: Running token total at suspension, so the ordered token pipeline's BUDGET
    #: stage resumes against the real ledger rather than zero.
    tokens_used_so_far: int = Field(ge=0)

    @model_validator(mode="after")
    def _tail_matches_pending_ids(self) -> "HitlResumptionPoint":
        """The stored tail MUST be the reviewed assistant turn, exactly.

        This is where a truncated, reordered, or tampered snapshot is caught, and
        catching it HERE is deliberate: the approval path already validates this
        model before it reaches ``execute()``, and a validation failure there is a
        PERMANENT disposition that terminates the row -- the same treatment an
        invalid ``request_json`` gets, and for the same reason (it will fail
        identically on every retry). Left to the execution path instead, the same
        corruption would surface as a transient failure and loop the row back to
        'pending' forever.

        It reads the serialized ``LLMMessage`` shape by key rather than importing
        the adapter model, which keeps this module free of that dependency. The
        coupling is to the stored JSON shape, which is exactly what this model is
        the schema for.
        """
        tail = self.messages[-1]
        if tail.get("role") != "assistant":
            raise ValueError("resumption tail must be the reviewed assistant message")
        tail_ids = [
            block.get("tool_use_id")
            for block in tail.get("content") or []
            if isinstance(block, dict) and block.get("kind") == "tool_use"
        ]
        if tail_ids != list(self.pending_tool_use_ids):
            raise ValueError(
                "pending_tool_use_ids does not match the tool_use blocks in the "
                f"stored assistant turn: {self.pending_tool_use_ids!r} != {tail_ids!r}"
            )
        return self
