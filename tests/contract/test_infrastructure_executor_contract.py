"""The infrastructure executor's contract, and that its schema paths resolve.

`AgentContract` names its input/output schemas as DOTTED-PATH STRINGS, so a typo
is invisible until the first execution attempt -- which, for this contract, means
invisible until someone is trying to contain a runaway workload. These tests
resolve them the way the runtime does.

Resolving them here also gives `skylize.schemas.agents.infrastructure` a real
loader, which is what the orphan-module contract (scripts/find_orphan_modules.py)
asks for: a module reached only through a string is indistinguishable from a dead
one until something imports it.
"""

from __future__ import annotations

import importlib

import pytest

from skylize.contracts.base import HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.schemas.agents.infrastructure import ContainInstanceIn, ContainInstanceOut

CONTRACT_ID = "infrastructure_executor"


def _resolve(path: str) -> type:
    module_name, _, attr = path.rpartition(".")
    return getattr(importlib.import_module(module_name), attr)


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_schema_paths_resolve(field: str) -> None:
    contract = MVP_REGISTRY.resolve(CONTRACT_ID)
    model = _resolve(getattr(contract, field))
    assert hasattr(model, "model_fields"), f"{field} is not a pydantic model"


def test_input_names_exactly_one_machine() -> None:
    """A containment approval must be for a NAMED machine.

    A schema that accepted a list, a prefix, or a label selector would let one
    human verdict authorise an unbounded set of stops.
    """
    contract = MVP_REGISTRY.resolve(CONTRACT_ID)
    model = _resolve(contract.input_schema)
    assert set(model.model_fields) == {"project", "zone", "instance", "reason"}
    for name in ("project", "zone", "instance"):
        assert model.model_fields[name].annotation is str


def test_reason_is_required_so_the_audit_trail_can_say_why() -> None:
    model = _resolve(MVP_REGISTRY.resolve(CONTRACT_ID).input_schema)
    assert model.model_fields["reason"].is_required()


def test_output_can_express_a_partial_containment() -> None:
    """"Stopped but the IP is still attached" must be representable.

    An output schema with only a success boolean would force a partial result to
    be reported as either a clean success or a clean failure, and both are lies.
    """
    model = _resolve(MVP_REGISTRY.resolve(CONTRACT_ID).output_schema)
    assert {"fully_succeeded", "stopped", "ip_released", "partial", "summary"} <= set(
        model.model_fields
    )


def test_the_contract_holds_exactly_one_tool_and_no_model_access() -> None:
    """It carries an action; it does not reason.

    Granting `llm.generate` would create a path where prompt content could
    influence WHICH machine is stopped.
    """
    contract = MVP_REGISTRY.resolve(CONTRACT_ID)
    assert contract.invocable_tools == ["integration.gcp_stop_instance"]
    assert [g.tool_id for g in contract.allowed_tools] == [
        "integration.gcp_stop_instance"
    ]
    assert contract.memory_read_access == []
    assert contract.memory_write_access == []


def test_it_always_defers_to_a_human() -> None:
    """FIRST_EXTERNAL_LAUNCH is checked before the trigger-presence opt-out
    (app/decision_engine/evaluator.py:230-245), so it is the one trigger a future
    contract edit cannot suppress. Losing it would both allow an unapproved stop
    and remove the `hitl_id` the action's idempotency key derives from."""
    contract = MVP_REGISTRY.resolve(CONTRACT_ID)
    assert HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH in contract.human_in_loop_triggers
    assert contract.failure_mode.value == "fail_closed"


def test_the_contracts_string_paths_point_at_these_exact_models() -> None:
    """A STATIC import, deliberately, alongside the dynamic resolution above.

    The dynamic `importlib` calls prove the runtime can find the models; this one
    proves the strings name THESE models and not two other classes that merely
    happen to resolve. It also gives the schema module a statically visible
    importer, which is what the orphan-module contract looks for -- runtime
    resolution through a string is invisible to it.
    """
    contract = MVP_REGISTRY.resolve(CONTRACT_ID)
    assert _resolve(contract.input_schema) is ContainInstanceIn
    assert _resolve(contract.output_schema) is ContainInstanceOut


def test_a_partial_containment_round_trips_through_the_output_model() -> None:
    """The shape an operator must be able to see: stopped, but not fully done."""
    out = ContainInstanceOut(
        fully_succeeded=False, stopped=True, ip_released=False, partial=True,
        summary="instances.stop: ok; instances.deleteAccessConfig: FAILED (HTTP 403)",
    )
    assert out.partial and out.stopped and not out.ip_released
    assert not out.fully_succeeded
