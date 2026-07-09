"""utility.current_datetime — trivial tool; good for exercising the tool loop."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from ..base import ToolContext, ToolDefinition


class CurrentDatetimeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CurrentDatetimeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_datetime_utc: str


async def _handle(_: CurrentDatetimeIn, __: ToolContext) -> CurrentDatetimeOut:
    return CurrentDatetimeOut(current_datetime_utc=datetime.now(timezone.utc).isoformat())


CURRENT_DATETIME_TOOL = ToolDefinition(
    tool_id="utility.current_datetime",
    name="Current Datetime",
    description=(
        "Returns the current UTC date and time in ISO-8601 format. Call this "
        "whenever you need to know today's date or timestamp something — you "
        "have no other way to know the current date."
    ),
    input_schema=CurrentDatetimeIn,
    output_schema=CurrentDatetimeOut,
    category="compute",
    handler=_handle,
)
