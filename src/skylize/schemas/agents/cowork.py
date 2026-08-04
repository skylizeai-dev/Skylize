"""I/O schemas for the co-work agent — one interactive turn.

The co-work agent is the human-present shape: an employee talks to it during a
session, and it acts under THAT employee's authority (never wider). One request
is one turn; the conversation lives in the work journal and the principal's
memory namespace, not in this payload.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CoworkTurnIn(BaseModel):
    """One message from the human to their co-work agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)


class CoworkTurnOut(BaseModel):
    """The agent's reply to one turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: str
