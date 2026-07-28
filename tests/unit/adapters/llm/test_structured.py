"""
Unit tests for grammar-constrained structured output (T-A).

Covers, against the provider-neutral LLM Gateway port:
  - the three native schema translations (OpenAI json_schema · Anthropic tool_use
    · Gemini responseSchema) across five schema shapes
    (flat, nested, enum, literal, optional);
  - the validate-then-retry fallback: success, single-retry recovery, and
    raise-after-second-failure, with the fallback audit hop;
  - token-budget enforcement is preserved on the structured path.

No vendor SDK is imported: translation is pure schema reshaping and generation
runs through a fake gateway that satisfies the `LLMGateway` Protocol.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from skylize.adapters.llm import (
    STRUCTURED_FALLBACK_ACTION,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMUsage,
    StructuredCapability,
    StructuredRequest,
    StructuredValidationError,
    build_provider_payload,
    generate_structured,
    translate_anthropic,
    translate_gemini,
    translate_openai,
)


# ── Five schema shapes ───────────────────────────────────────────────────────
class FlatSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    score: int


class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    weight: float


class NestedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    inner: Inner


class Color(str, Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class EnumSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: Color


class LiteralSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["static", "video", "carousel"]


class OptionalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_field: str
    maybe: str | None = None


ALL_SHAPES = [FlatSchema, NestedSchema, EnumSchema, LiteralSchema, OptionalSchema]

# A valid JSON payload for each shape, used to drive the fake gateway.
VALID_JSON: dict[type[BaseModel], str] = {
    FlatSchema: '{"name":"x","score":3}',
    NestedSchema: '{"title":"t","inner":{"label":"l","weight":1.5}}',
    EnumSchema: '{"color":"green"}',
    LiteralSchema: '{"kind":"video"}',
    OptionalSchema: '{"required_field":"r","maybe":null}',
}


# ── Fake gateway implementing the LLMGateway Protocol ────────────────────────
class FakeGateway:
    """Returns a scripted sequence of response texts; counts calls; enforces budget."""

    def __init__(self, texts: list[str], *, budget_remaining: int | None = None) -> None:
        self._texts = list(texts)
        self.calls: list[LLMGenerateRequest] = []
        self._budget_remaining = budget_remaining

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        self.calls.append(request)
        if (
            self._budget_remaining is not None
            and request.requested_max_tokens > self._budget_remaining
        ):
            from skylize.adapters.llm import TokenBudgetExceeded

            raise TokenBudgetExceeded(
                f"requested {request.requested_max_tokens} > remaining "
                f"{self._budget_remaining}"
            )
        text = self._texts.pop(0)
        return LLMGenerateResponse(
            text=text,
            provider="fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:  # pragma: no cover
        raise NotImplementedError


class RecordingAudit:
    """Captures audit.record kwargs; satisfies the AuditSink Protocol."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> UUID:
        self.records.append(kwargs)
        return uuid4()


def _request(
    capability: StructuredCapability = StructuredCapability.NONE,
    *,
    max_tokens: int = 1000,
) -> StructuredRequest:
    return StructuredRequest(
        prompt="produce output",
        requested_max_tokens=max_tokens,
        governance_token_id=uuid4(),
        org_id="org_1",
        correlation_id=uuid4(),
        agent_id="agent_test",
        capability=capability,
    )


# ── Translation: OpenAI (json_schema, strict) ────────────────────────────────
@pytest.mark.parametrize("schema", ALL_SHAPES)
def test_translate_openai_is_strict_json_schema(schema: type[BaseModel]) -> None:
    payload = translate_openai(schema)
    assert payload["type"] == "json_schema"
    js = payload["json_schema"]
    assert js["name"] == schema.__name__
    assert js["strict"] is True

    # Strict mode: every object node closed and all properties required.
    def assert_strict(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"].keys())
            for v in node.values():
                assert_strict(v)
        elif isinstance(node, list):
            for item in node:
                assert_strict(item)

    assert_strict(js["schema"])


# ── Translation: Anthropic (forced single tool) ──────────────────────────────
@pytest.mark.parametrize("schema", ALL_SHAPES)
def test_translate_anthropic_forces_single_tool(schema: type[BaseModel]) -> None:
    payload = translate_anthropic(schema)
    assert len(payload["tools"]) == 1
    tool = payload["tools"][0]
    assert payload["tool_choice"] == {"type": "tool", "name": tool["name"]}
    # input_schema is the model's own JSON schema (refs preserved — Anthropic ok).
    assert tool["input_schema"] == schema.model_json_schema()


# ── Translation: Gemini (responseSchema, inlined, stripped) ──────────────────
@pytest.mark.parametrize("schema", ALL_SHAPES)
def test_translate_gemini_inlines_refs_and_strips_unsupported(
    schema: type[BaseModel],
) -> None:
    payload = translate_gemini(schema)
    assert payload["responseMimeType"] == "application/json"
    rs = payload["responseSchema"]

    forbidden = {"$defs", "$ref", "additionalProperties", "title", "default", "$schema"}

    def assert_clean(node: Any) -> None:
        if isinstance(node, dict):
            assert not (forbidden & node.keys()), f"unsupported key in {node.keys()}"
            for v in node.values():
                assert_clean(v)
        elif isinstance(node, list):
            for item in node:
                assert_clean(item)

    assert_clean(rs)


def test_translate_gemini_nested_model_is_fully_inlined() -> None:
    # NestedSchema has a $ref to Inner; after translation the inner object's
    # properties must appear inline, not as a reference.
    rs = translate_gemini(NestedSchema)["responseSchema"]
    inner = rs["properties"]["inner"]
    assert inner["type"] == "object"
    assert set(inner["properties"].keys()) == {"label", "weight"}


def test_translate_gemini_enum_round_trips() -> None:
    rs = translate_gemini(EnumSchema)["responseSchema"]
    color = rs["properties"]["color"]
    # Enum inlined to its allowed values.
    assert color.get("enum") == ["red", "green", "blue"]


def test_build_provider_payload_dispatch() -> None:
    assert build_provider_payload(FlatSchema, StructuredCapability.JSON_SCHEMA) == (
        translate_openai(FlatSchema)
    )
    assert build_provider_payload(FlatSchema, StructuredCapability.TOOL_USE) == (
        translate_anthropic(FlatSchema)
    )
    assert build_provider_payload(FlatSchema, StructuredCapability.RESPONSE_SCHEMA) == (
        translate_gemini(FlatSchema)
    )
    assert build_provider_payload(FlatSchema, StructuredCapability.NONE) == {}


# ── Orchestration: happy path for every shape ────────────────────────────────
@pytest.mark.parametrize("schema", ALL_SHAPES)
async def test_generate_structured_returns_validated_instance(
    schema: type[BaseModel],
) -> None:
    gateway = FakeGateway([VALID_JSON[schema]])
    result = await generate_structured(gateway, _request(), schema)
    assert isinstance(result, schema)
    assert len(gateway.calls) == 1  # no fallback needed


async def test_generate_structured_strips_code_fence() -> None:
    fenced = "```json\n" + VALID_JSON[FlatSchema] + "\n```"
    gateway = FakeGateway([fenced])
    result = await generate_structured(gateway, _request(), FlatSchema)
    assert result.name == "x"


# ── Orchestration: fallback / retry / raise ──────────────────────────────────
async def test_invalid_then_valid_triggers_single_retry_and_audits() -> None:
    gateway = FakeGateway(['{"name":"x"}', VALID_JSON[FlatSchema]])  # 1st missing score
    audit = RecordingAudit()
    req = _request()

    result = await generate_structured(gateway, req, FlatSchema, audit=audit)

    assert result.score == 3
    assert len(gateway.calls) == 2  # exactly one retry
    assert len(audit.records) == 1
    rec = audit.records[0]
    assert rec["action_type"] == STRUCTURED_FALLBACK_ACTION
    assert rec["result"] == "failed"
    assert rec["correlation_id"] == req.correlation_id


async def test_two_invalid_responses_raise_structured_validation_error() -> None:
    gateway = FakeGateway(['{"bad":1}', '{"still":"bad"}'])
    audit = RecordingAudit()

    with pytest.raises(StructuredValidationError) as exc_info:
        await generate_structured(gateway, _request(), FlatSchema, audit=audit)

    assert exc_info.value.schema_name == "FlatSchema"
    assert exc_info.value.raw_text == '{"still":"bad"}'
    assert len(gateway.calls) == 2  # one retry, no infinite loop
    assert len(audit.records) == 1  # fallback audited once


async def test_fallback_without_audit_sink_still_retries() -> None:
    gateway = FakeGateway(['{"bad":1}', VALID_JSON[FlatSchema]])
    result = await generate_structured(gateway, _request(), FlatSchema, audit=None)
    assert result.name == "x"
    assert len(gateway.calls) == 2


# ── Token budget is enforced on the structured path (AC #7) ───────────────────
async def test_token_budget_exceeded_propagates_without_fallback() -> None:
    from skylize.adapters.llm import TokenBudgetExceeded

    gateway = FakeGateway([VALID_JSON[FlatSchema]], budget_remaining=100)
    audit = RecordingAudit()

    with pytest.raises(TokenBudgetExceeded):
        await generate_structured(
            gateway,
            _request(max_tokens=5000),  # over the 100 remaining
            FlatSchema,
            audit=audit,
        )
    # Budget refusal is pre-egress: no retry, no fallback audit.
    assert len(gateway.calls) == 1
    assert audit.records == []


def test_structured_request_projects_budget_fields_to_generate_request() -> None:
    req = _request(max_tokens=4242)
    gen = req.to_generate_request(system="sys")
    assert gen.requested_max_tokens == 4242
    assert gen.governance_token_id == req.governance_token_id
    assert gen.org_id == "org_1"
    assert gen.system == "sys"
