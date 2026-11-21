"""ToolOutcomeUnknownError survives ToolProxy.invoke (owner ruling OD4 follow-up).

HubSpot's create_contact (and later other non-idempotent writes) signals "the
side effect MAY have happened" with a typed `ToolOutcomeUnknownError`. The proxy
normalizes every other handler exception into a fresh `ToolExecutionError`; these
tests pin that it keeps the OUTCOME-UNKNOWN subclass while everything else on
the failure path - spend-hold release, the "failed" audit row, the message - is
unchanged. Whether an outcome-unknown should KEEP the hold instead is an open
owner decision; these tests pin today's behaviour (released).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import BaseModel

from skylize.app.principal.spend import SpendLedger
from skylize.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolOutcomeUnknownError,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

from .test_tool_proxy_spend import (
    ORG,
    SPEND_TOOL,
    FakeSpendRepo,
    _authority,
    _contract,
    _SpendIn,
    _SpendOut,
    _v11_token,
)

_MSG = "create OUTCOME UNKNOWN: vendor returned 503. Do NOT re-run this tool."


def _registry(exc: Exception) -> ToolRegistry:
    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        raise exc

    return ToolRegistry([
        ToolDefinition(
            tool_id=SPEND_TOOL, name="Spend", description="spends money",
            input_schema=_SpendIn, output_schema=_SpendOut, category="integration",
            handler=handler,
            spend={"currency": "USD", "amount_field": "amount_minor"},
        ),
    ])


async def _invoke(exc: Exception) -> tuple[pytest.ExceptionInfo[Exception], FakeSpendRepo, list]:
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=10_000)
    proxy = ToolProxy(
        registry=_registry(exc), audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=SpendLedger(repo),
    )
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)
    with pytest.raises(ToolExecutionError) as exc_info:
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 4_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    audits = [
        e.payload for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked"
    ]
    return exc_info, repo, audits


def test_outcome_unknown_is_a_tool_execution_error() -> None:
    assert issubclass(ToolOutcomeUnknownError, ToolExecutionError)


async def test_outcome_unknown_type_survives_tool_proxy_invoke() -> None:
    original = ToolOutcomeUnknownError(_MSG)
    exc_info, _, _ = await _invoke(original)

    assert type(exc_info.value) is ToolOutcomeUnknownError
    assert str(exc_info.value) == _MSG, "message text must be unchanged"
    assert exc_info.value.__cause__ is original, "must be chained from the handler error"


async def test_outcome_unknown_still_caught_by_except_tool_execution_error() -> None:
    caught = False
    try:
        exc_info, _, _ = await _invoke(ToolOutcomeUnknownError(_MSG))
        raise exc_info.value
    except ToolExecutionError:
        caught = True
    assert caught


async def test_outcome_unknown_releases_hold_and_audits_failed_like_any_error() -> None:
    """Spend-hold and audit behaviour is deliberately IDENTICAL to a generic error."""
    _, unknown_repo, unknown_audits = await _invoke(ToolOutcomeUnknownError(_MSG))
    _, generic_repo, generic_audits = await _invoke(RuntimeError(_MSG))

    for repo in (unknown_repo, generic_repo):
        assert len(repo.releases) == 1
        assert repo.commits == []
        assert repo.reserved_minor == 0 and repo.spent_minor == 0

    assert len(unknown_audits) == len(generic_audits) == 1
    assert unknown_audits[0].result == generic_audits[0].result == "failed"
    assert unknown_audits[0].result_reason == generic_audits[0].result_reason
    assert unknown_audits[0].result_reason == f"handler error: {_MSG}"


async def test_generic_handler_error_is_still_flattened_to_base_type() -> None:
    exc_info, _, _ = await _invoke(RuntimeError("vendor API exploded"))
    assert type(exc_info.value) is ToolExecutionError
