"""
Work journal — the shared substrate.

THE CORRECTION THIS MODULE ENCODES
----------------------------------
"The co-work agent must stay in sync with the autonomous agent" is not a sync
problem. It becomes one only if you build two memories and then reconcile them.
Don't. Build ONE append-only, principal-scoped log that both shapes WRITE to, and
give the co-work agent a cursor to READ from.

Two stores + reconciliation = a permanent class of bug ("my agent told me the
invoice was paid, it wasn't"). One store + a monotonic cursor = a `WHERE seq > $1`.

WHY NOT REUSE `audit_log`?
    `AuditService` hashes inputs and outputs (SHA-256, PII-safe by design) and is
    object-locked with a 7-year floor. You cannot render a morning brief from
    hashes, and you must not put readable business content into an immutable
    compliance store with that retention. Different retention, different PII
    posture, different reader. Two tables, written in the same transaction.

WHY NOT A NIGHTLY DIGEST JOB?
    Because "fresh" is the requirement. A cron digest is stale the moment the
    autonomous fleet acts at 09:05. Read-time projection from `seq > cursor` is
    always current; the cron is only a cache pre-warm, never the source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence, runtime_checkable
from uuid import UUID

from .models import ActorKind, JournalCursor, JournalEntry


@runtime_checkable
class JournalRepository(Protocol):
    async def append(self, entry: JournalEntry) -> int:
        """Insert and return the assigned monotonic `seq`."""

    async def since(
        self,
        *,
        org_id: str,
        principal_id: str,
        after_seq: int,
        limit: int = 200,
    ) -> Sequence[JournalEntry]: ...

    async def get_cursor(
        self, *, org_id: str, principal_id: str
    ) -> JournalCursor | None: ...

    async def advance_cursor(
        self, *, org_id: str, principal_id: str, to_seq: int, at: datetime
    ) -> None: ...

    async def head_seq(self, *, org_id: str, principal_id: str) -> int: ...


class WorkJournal:
    """Write path for both deployment shapes; read path for the co-work agent."""

    def __init__(self, repo: JournalRepository) -> None:
        self._repo = repo

    async def record(
        self,
        *,
        org_id: str,
        principal_id: str,
        actor_kind: ActorKind,
        actor_id: str,
        correlation_id: UUID,
        kind: str,
        headline: str,
        detail: dict[str, object] | None = None,
        cost_minor: int = 0,
        requires_attention: bool = False,
        governance_token_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> int:
        """Append one entry.

        CALL THIS IN THE SAME TRANSACTION AS THE BUSINESS WRITE. A journal entry
        that can be lost independently of the action it describes is worse than no
        journal — it makes the co-work agent confidently wrong.
        """
        entry = JournalEntry(
            seq=0,  # assigned by the DB
            org_id=org_id,
            principal_id=principal_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            correlation_id=correlation_id,
            governance_token_id=governance_token_id,
            kind=kind,
            headline=headline.strip(),
            detail=detail or {},
            cost_minor=cost_minor,
            requires_attention=requires_attention,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        return await self._repo.append(entry)

    async def unseen(
        self, *, org_id: str, principal_id: str, limit: int = 200
    ) -> tuple[Sequence[JournalEntry], int]:
        """Everything that happened since this human last actually looked.

        Returns `(entries, head_seq)`. The caller advances the cursor only AFTER
        rendering — advancing on read means a failed render silently eats a day of
        work from the brief.
        """
        cursor = await self._repo.get_cursor(org_id=org_id, principal_id=principal_id)
        after = cursor.last_seen_seq if cursor else 0
        entries = await self._repo.since(
            org_id=org_id, principal_id=principal_id, after_seq=after, limit=limit
        )
        head = entries[-1].seq if entries else after
        return entries, head

    async def mark_seen(
        self, *, org_id: str, principal_id: str, to_seq: int
    ) -> None:
        await self._repo.advance_cursor(
            org_id=org_id,
            principal_id=principal_id,
            to_seq=to_seq,
            at=datetime.now(timezone.utc),
        )


# --------------------------------------------------------------------------- #
# Brief assembly
# --------------------------------------------------------------------------- #


class BriefBlock(dict[str, object]):
    """Deterministic, pre-LLM structure. The model summarises this; it does not
    decide what is in it. Ordering and attention-flagging are code, so the same
    day always produces the same brief skeleton — which is what makes the brief
    auditable and testable."""


def assemble_brief(entries: Sequence[JournalEntry]) -> dict[str, object]:
    """Deterministic reduction of raw journal entries into brief sections.

    NOTE: this returns STRUCTURE, not prose. Feed the result to the LLM as the
    grounding payload. Never let the model read the raw journal and decide what
    matters — that is how "the agent forgot to mention the failed payment" happens.
    """
    needs_attention = [e for e in entries if e.requires_attention]
    by_actor: dict[str, list[JournalEntry]] = {}
    for e in entries:
        by_actor.setdefault(e.actor_kind.value, []).append(e)

    return {
        "entry_count": len(entries),
        "window_start": entries[0].occurred_at.isoformat() if entries else None,
        "window_end": entries[-1].occurred_at.isoformat() if entries else None,
        "total_cost_minor": sum(e.cost_minor for e in entries),
        "needs_attention": [
            {
                "seq": e.seq,
                "kind": e.kind,
                "headline": e.headline,
                "correlation_id": str(e.correlation_id),
            }
            for e in needs_attention
        ],
        "done_while_away": [
            {"seq": e.seq, "kind": e.kind, "headline": e.headline}
            for e in by_actor.get(ActorKind.AGENT_AUTONOMOUS.value, [])
        ],
        "your_own_actions": [
            {"seq": e.seq, "kind": e.kind, "headline": e.headline}
            for e in by_actor.get(ActorKind.HUMAN.value, [])
        ],
        "head_seq": entries[-1].seq if entries else 0,
    }
