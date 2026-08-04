"""Unit tests for AgentExecutionService."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage
from skylize.app.agents.execution import AgentExecutionService, AgentInputError, AgentOutputError
from skylize.contracts.registry import AgentNotRegistered, MVP_REGISTRY


def _make_demo_response(payload: dict) -> LLMGenerateResponse:
    text = json.dumps(payload)
    return LLMGenerateResponse(
        text=text,
        provider="demo",
        concrete_model="demo-v1",
        usage=LLMUsage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
        cost_usd_micros=0,
    )


def _make_llm_mock(payload: dict) -> AsyncMock:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=_make_demo_response(payload))
    return llm


def _make_deliverable_mock(row: Any) -> AsyncMock:
    svc = MagicMock()
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc


def _fake_row(agent_id: str = "hook_generator_agent") -> Any:
    row = MagicMock()
    row.id = uuid4()
    row.agent_id = agent_id
    row.status = "draft"
    row.title = "Test Hooks"
    return row


# ── Happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_hook_generator_returns_deliverable() -> None:
    valid_hooks = {"hooks": ["Hook A", "Hook B", "Hook C"]}
    llm = _make_llm_mock(valid_hooks)
    row = _fake_row()
    deliverables = _make_deliverable_mock(row)

    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=deliverables,
    )
    result = await service.execute(
        org_id="org_a",
        agent_id="hook_generator_agent",
        input_data={
            "brand_name": "TestBrand",
            "product_description": "A revolutionary widget",
            "target_audience": "startup founders",
            "tone": "professional",
        },
        user_id="user_1",
    )
    assert result is row
    deliverables.create_deliverable.assert_called_once()
    call_kwargs = deliverables.create_deliverable.call_args.kwargs
    assert call_kwargs["org_id"] == "org_a"
    assert call_kwargs["agent_id"] == "hook_generator_agent"
    assert call_kwargs["deliverable_type"] == "marketing_copy"
    assert "TestBrand" in call_kwargs["content_markdown"]


@pytest.mark.asyncio
async def test_execute_builds_prompt_with_agent_id() -> None:
    llm = _make_llm_mock({"hooks": ["H1"]})
    deliverables = _make_deliverable_mock(_fake_row())

    service = AgentExecutionService(registry=MVP_REGISTRY, llm=llm, deliverables=deliverables)
    await service.execute(
        org_id="org_a",
        agent_id="hook_generator_agent",
        input_data={
            "brand_name": "Acme",
            "product_description": "Widget",
            "target_audience": "SMBs",
        },
        user_id="u",
    )
    llm_req = llm.generate.call_args.args[0]
    assert "hook_generator_agent" in llm_req.prompt
    assert "Acme" in llm_req.prompt


# ── Invalid input → 422 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_input_raises_agent_input_error() -> None:
    llm = _make_llm_mock({})
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    with pytest.raises(AgentInputError):
        await service.execute(
            org_id="org_a",
            agent_id="hook_generator_agent",
            input_data={"wrong_field": 123},  # missing required brand_name etc.
            user_id="u",
        )


@pytest.mark.asyncio
async def test_extra_fields_in_input_raise_agent_input_error() -> None:
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_make_llm_mock({}),
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    with pytest.raises(AgentInputError):
        await service.execute(
            org_id="org_a",
            agent_id="hook_generator_agent",
            input_data={
                "brand_name": "X",
                "product_description": "Y",
                "target_audience": "Z",
                "injected_extra": "boom",
            },
            user_id="u",
        )


# ── Unknown agent_id → 404 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_agent_raises_agent_not_registered() -> None:
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_make_llm_mock({}),
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    with pytest.raises(AgentNotRegistered):
        await service.execute(
            org_id="org_a",
            agent_id="does_not_exist",
            input_data={},
            user_id="u",
        )


# ── Bad LLM output → AgentOutputError ───────────────────────────────────────

@pytest.mark.asyncio
async def test_non_json_llm_output_raises_agent_output_error() -> None:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=LLMGenerateResponse(
        text="not json at all",
        provider="demo",
        concrete_model="demo-v1",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    ))
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    with pytest.raises(AgentOutputError):
        await service.execute(
            org_id="org_a",
            agent_id="hook_generator_agent",
            input_data={
                "brand_name": "X",
                "product_description": "Y",
                "target_audience": "Z",
            },
            user_id="u",
        )


@pytest.mark.asyncio
async def test_json_with_wrong_schema_raises_agent_output_error() -> None:
    llm = _make_llm_mock({"unexpected_key": [1, 2, 3]})
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    with pytest.raises(AgentOutputError):
        await service.execute(
            org_id="org_a",
            agent_id="hook_generator_agent",
            input_data={
                "brand_name": "X",
                "product_description": "Y",
                "target_audience": "Z",
            },
            user_id="u",
        )


# ── Demo content in deliverable ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_demo_mode_deliverable_contains_demo_hooks() -> None:
    demo_hooks = ["[DEMO] Hook 1", "[DEMO] Hook 2"]
    llm = _make_llm_mock({"hooks": demo_hooks})
    captured: dict = {}

    async def capture_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_row()

    deliverables = MagicMock()
    deliverables.create_deliverable = capture_create

    service = AgentExecutionService(registry=MVP_REGISTRY, llm=llm, deliverables=deliverables)
    await service.execute(
        org_id="org_a",
        agent_id="hook_generator_agent",
        input_data={
            "brand_name": "DemoBrand",
            "product_description": "Demo product",
            "target_audience": "Everyone",
        },
        user_id="u",
    )
    assert "[DEMO]" in captured["content_markdown"]
    assert captured["agent_id"] == "hook_generator_agent"


# ── list_agents ──────────────────────────────────────────────────────────────

def test_list_agents_returns_all_registered() -> None:
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_make_llm_mock({}),
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    agents = service.list_agents()
    agent_ids = {a["agent_id"] for a in agents}
    assert "hook_generator_agent" in agent_ids
    assert len(agents) == 22  # +1: cowork_agent (sandbox)


def test_list_agents_hook_generator_has_input_schema() -> None:
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_make_llm_mock({}),
        deliverables=_make_deliverable_mock(_fake_row()),
    )
    agents = service.list_agents()
    hook = next(a for a in agents if a["agent_id"] == "hook_generator_agent")
    schema = hook["input_schema"]
    assert "properties" in schema
    props = schema["properties"]
    assert "brand_name" in props
    assert "product_description" in props
    assert "target_audience" in props
    assert "tone" in props
