"""Infrastructure-executor agent I/O models (GCP kill switch).

Deliberately minimal. This agent exists to carry ONE governed action through the
decision gate; its input is the action's parameters and its output is an honest
report of what actually changed on the customer's infrastructure.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainInstanceIn(_Base):
    """Which machine to contain, and why.

    `reason` is required and unbounded-ish on purpose: this action stops a
    customer's production VM, and an audit trail that cannot say why is not an
    audit trail.
    """

    project: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    instance: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


class ContainInstanceOut(_Base):
    """What actually happened, per step.

    `fully_succeeded` is NOT a summary of intent - it is False whenever any step
    did not complete, including the partial case where the VM stopped but its
    external IP is still attached. `summary` always names the per-step outcome so
    a partial result can never read as a clean success.
    """

    fully_succeeded: bool
    stopped: bool
    ip_released: bool
    partial: bool
    summary: str
