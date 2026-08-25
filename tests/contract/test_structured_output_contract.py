"""
Contract gate — structured output round-trips real AgentContract output schemas.

The gateway's structured-output enforcement must faithfully carry the *actual*
agent I/O models resolved from the registry through all three provider
translations and back into a validated instance. If a real output schema cannot
round-trip (e.g. a nested model that fails to inline for Gemini), structured
generation for that agent is broken — so this is a build-failing contract check,
in the same spirit as test_agent_contracts.py.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from skylize.adapters.llm import (
    LLMGenerateResponse,
    LLMUsage,
    StructuredCapability,
    StructuredRequest,
    build_provider_payload,
    generate_structured,
)
from skylize.contracts.registry import MVP_REGISTRY, resolve_model

# Sample real AgentContract output schemas spanning flat + list-bearing shapes.
SAMPLE_OUTPUT_SCHEMAS = [
    "hook_generator_agent",  # -> HookGeneratorExecuteOut (hooks: list[str])
    "copy_director",  # -> CopyPackageOut (brief_id, hooks, body_copy, ctas)
    "script_writer_agent",  # -> ScriptOut (brief_id, script, beats)
]


def _output_model(agent_id: str) -> type:
    contract = MVP_REGISTRY.resolve(agent_id)
    return resolve_model(contract.output_schema)


def _sample_instance(model: type) -> Any:
    """Build a minimal valid instance of a creative output model."""
    brief = uuid4()
    name = model.__name__
    if name == "HookGeneratorExecuteOut":
        return model(hooks=["h1", "h2"])
    if name == "HooksOut":
        return model(brief_id=brief, hooks=["h1", "h2"])
    if name == "CopyPackageOut":
        return model(brief_id=brief, hooks=["h"], body_copy=["b"], ctas=["c"])
    if name == "ScriptOut":
        return model(brief_id=brief, script="s", beats=["b1", "b2"])
    raise AssertionError(f"no sample builder for {name}")


@pytest.mark.parametrize("agent_id", SAMPLE_OUTPUT_SCHEMAS)
@pytest.mark.parametrize(
    "capability",
    [
        StructuredCapability.JSON_SCHEMA,
        StructuredCapability.TOOL_USE,
        StructuredCapability.RESPONSE_SCHEMA,
    ],
)
def test_real_output_schema_translates_for_every_provider(
    agent_id: str, capability: StructuredCapability
) -> None:
    """Every sampled AgentContract output schema produces a non-empty native
    payload for each provider without raising."""
    model = _output_model(agent_id)
    payload = build_provider_payload(model, capability)
    assert payload, f"{model.__name__} produced empty payload for {capability}"


class _ReplayGateway:
    """Returns the JSON of a known-valid instance; satisfies LLMGateway."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def generate(self, request: Any) -> LLMGenerateResponse:
        self.calls += 1
        return LLMGenerateResponse(
            text=self._text,
            provider="contract-fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    def generate_sync(self, request: Any) -> LLMGenerateResponse:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.parametrize("agent_id", SAMPLE_OUTPUT_SCHEMAS)
async def test_real_output_schema_round_trips_through_generate_structured(
    agent_id: str,
) -> None:
    """A valid instance serialized to JSON is regenerated and re-validated to an
    equal instance — the end-to-end structured-output contract for a real agent."""
    model = _output_model(agent_id)
    original = _sample_instance(model)
    gateway = _ReplayGateway(original.model_dump_json())

    request = StructuredRequest(
        prompt=f"produce a {model.__name__}",
        requested_max_tokens=2000,
        governance_token_id=uuid4(),
        org_id="org_contract",
        correlation_id=uuid4(),
        agent_id=agent_id,
        capability=StructuredCapability.TOOL_USE,
    )

    result = await generate_structured(gateway, request, model)

    assert isinstance(result, model)
    assert result == original
    assert gateway.calls == 1  # clean round-trip, no fallback


def test_gemini_response_schema_is_json_serializable_for_real_schema() -> None:
    """Gemini's responseSchema (post inline + strip) must be plain JSON — a
    lingering $ref would make it both invalid for Gemini and unserializable as a
    clean schema. Asserts on the real CopyPackageOut shape."""
    model = _output_model("copy_director")
    rs = build_provider_payload(model, StructuredCapability.RESPONSE_SCHEMA)
    encoded = json.dumps(rs)  # raises if any non-serializable content
    assert "$ref" not in encoded
    assert "$defs" not in encoded
