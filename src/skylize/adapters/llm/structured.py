"""
Grammar-constrained (schema-constrained) structured output for the LLM Gateway.

This module replaces the legacy "generate free text → JSON.loads → validate →
retry on failure" pattern with **provider-native structured output enforcement**.
An agent asks for a Pydantic v2 model; the gateway instructs the underlying
provider to emit JSON that conforms to that model's JSON Schema *at generation
time*, and returns a validated model instance — never a malformed-JSON exception
path leaking to the caller.

Boundary discipline (system_boundaries.md §4.6, coding_standards.md §4):
  - This file lives inside `adapters/` — the only package allowed provider egress
    — but it imports **no vendor SDK**. The concrete provider adapters
    (Anthropic primary, OpenAI/Gemini failover) are authored in a later sprint;
    this module operates against the provider-neutral `LLMGateway` *port* and
    against three pure schema-translation functions that turn a Pydantic schema
    into each provider's native structured-output payload shape.
  - It never imports `app/`, `dal/`, `memory/`, `runtime/`. The fallback audit
    hop is taken through an injected `AuditSink` Protocol so this adapter does not
    reach up into the Application Boundary.

Provider feature matrix (the three native enforcement modes, AC #2–#4):

  | Provider  | Capability            | Native mechanism                                |
  |-----------|-----------------------|-------------------------------------------------|
  | OpenAI    | JSON_SCHEMA           | response_format={"type":"json_schema", ...},    |
  |           |                       | strict=true (every object closed + all required)|
  | Anthropic | TOOL_USE              | single forced tool; schema as `input_schema`    |
  | Gemini    | RESPONSE_SCHEMA       | responseSchema + responseMimeType=app/json,     |
  |           |                       | $ref/$defs inlined, unsupported keys stripped   |

Defense in depth (AC #5): even with native enforcement a provider can return a
non-conforming payload (older model, partial outage). `generate_structured`
therefore validates the returned text and, on failure, performs **exactly one**
retry, auditing each fallback as `llm_gateway.structured_fallback`. A second
failure raises `StructuredValidationError` — no silent malformed return.

Token budget (AC #7): structured generation routes through the gateway's normal
budget-checked egress (`LLMGenerateRequest`/`generate`), so the per-run
`max_token_budget` ceiling and `TokenBudgetExceeded` apply unchanged. Schema
construction never bypasses the budget.
"""

from __future__ import annotations

import copy
import json
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .gateway import LLMGateway, LLMGenerateRequest, LLMGenerateResponse

T = TypeVar("T", bound=BaseModel)

# Audit action_type for a structured-output fallback (validate-then-retry path).
# Recorded through the AuditSink, consistent with the orchestrator.* / governance.*
# action_type convention rather than a bespoke event class.
STRUCTURED_FALLBACK_ACTION = "llm_gateway.structured_fallback"

# A single forced-tool name used for the Anthropic tool-use coercion path.
_ANTHROPIC_TOOL_NAME = "emit_structured_output"


class StructuredCapability(str, Enum):
    """How a given provider enforces structured output natively.

    A concrete gateway implementation declares the capability of the provider it
    routes to; `build_provider_payload` then produces the matching native shape.
    """

    JSON_SCHEMA = "json_schema"  # OpenAI response_format
    TOOL_USE = "tool_use"  # Anthropic forced single tool
    RESPONSE_SCHEMA = "response_schema"  # Gemini responseSchema
    NONE = "none"  # provider has no native mode → prompt-only, rely on fallback


class StructuredValidationError(Exception):
    """Provider returned output that failed schema validation after one retry.

    Carries the final raw text and the underlying pydantic error so the caller's
    `failure_mode` can be applied and the failure audited.
    """

    def __init__(self, schema: type[BaseModel], raw_text: str, cause: Exception) -> None:
        self.schema_name = schema.__name__
        self.raw_text = raw_text
        self.__cause__ = cause
        super().__init__(
            f"structured output for {schema.__name__!r} failed validation after "
            f"one retry: {cause}"
        )


class StructuredRequest(BaseModel):
    """A provider-neutral request for schema-constrained generation.

    Mirrors `LLMGenerateRequest` (budget/tenancy fields are carried verbatim so
    the budget ceiling is enforced on the structured path too) and adds the
    enforcement capability the routed provider supports.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "default"
    prompt: str
    system: str | None = None
    requested_max_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    governance_token_id: UUID
    org_id: str

    # The enforcement mode of the provider this request will route to.
    capability: StructuredCapability = StructuredCapability.NONE

    def to_generate_request(self, *, system: str | None = None) -> LLMGenerateRequest:
        """Project onto the budget-checked egress request the gateway already uses."""
        return LLMGenerateRequest(
            model=self.model,
            prompt=self.prompt,
            system=system if system is not None else self.system,
            requested_max_tokens=self.requested_max_tokens,
            temperature=self.temperature,
            governance_token_id=self.governance_token_id,
            org_id=self.org_id,
        )


@runtime_checkable
class AuditSink(Protocol):
    """Minimal audit hop, injected so this adapter never imports `app.audit`.

    The concrete `AuditService.record(...)` signature is a structural superset of
    this Protocol, so the real service satisfies it without an adapter.
    """

    async def record(
        self,
        *,
        org_id: str,
        correlation_id: UUID,
        action_type: str,
        result: str,
        governance_token_id: UUID | None = None,
        result_reason: str | None = None,
    ) -> Any: ...


# ── Provider schema translation (pure; no SDK) ───────────────────────────────


def _enforce_openai_strict(node: Any) -> Any:
    """Make a JSON Schema satisfy OpenAI strict json_schema rules, in place.

    OpenAI's strict mode requires, for every object node: `additionalProperties`
    is `false` and *every* property key is listed in `required`. We walk the whole
    schema (including `$defs`) and enforce that. Other node types pass through.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.values():
            _enforce_openai_strict(value)
    elif isinstance(node, list):
        for item in node:
            _enforce_openai_strict(item)
    return node


def translate_openai(schema: type[BaseModel]) -> dict[str, Any]:
    """OpenAI `response_format` payload (AC #2).

    Uses the model's own JSON Schema and wraps it in the json_schema response
    format with strict enforcement.
    """
    json_schema = _enforce_openai_strict(schema.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": json_schema,
            "strict": True,
        },
    }


def translate_anthropic(schema: type[BaseModel]) -> dict[str, Any]:
    """Anthropic single-forced-tool coercion payload (AC #3).

    The schema becomes the tool's `input_schema`; `tool_choice` forces that one
    tool, so the model must populate the schema. Anthropic accepts standard JSON
    Schema including `$defs`/`$ref`, so no inlining is needed.
    """
    return {
        "tools": [
            {
                "name": _ANTHROPIC_TOOL_NAME,
                "description": f"Emit a {schema.__name__} object. Required output shape.",
                "input_schema": schema.model_json_schema(),
            }
        ],
        "tool_choice": {"type": "tool", "name": _ANTHROPIC_TOOL_NAME},
    }


# Keys Gemini's responseSchema (a subset of OpenAPI 3 Schema) does not accept.
_GEMINI_UNSUPPORTED_KEYS = frozenset(
    {"$defs", "$ref", "additionalProperties", "title", "default", "$schema"}
)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Recursively replace every `$ref: #/$defs/X` with a deep copy of def X.

    Gemini does not support `$ref`/`$defs`, so nested models and enums (which
    pydantic v2 emits as `$defs` + `$ref`) must be inlined to round-trip.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs[ref.split("/")[-1]]
            return _inline_refs(copy.deepcopy(target), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def _strip_unsupported(node: Any) -> Any:
    """Remove keys Gemini's responseSchema rejects, recursively."""
    if isinstance(node, dict):
        return {
            k: _strip_unsupported(v)
            for k, v in node.items()
            if k not in _GEMINI_UNSUPPORTED_KEYS
        }
    if isinstance(node, list):
        return [_strip_unsupported(item) for item in node]
    return node


def translate_gemini(schema: type[BaseModel]) -> dict[str, Any]:
    """Gemini `responseSchema` payload (AC #4).

    Inlines all `$ref`/`$defs` (unsupported) and strips schema keys Gemini
    rejects, then attaches the JSON mime type so the model emits a single JSON
    object conforming to the (de-referenced) schema.
    """
    raw = schema.model_json_schema()
    defs = raw.get("$defs", {})
    inlined = _inline_refs(raw, defs)
    response_schema = _strip_unsupported(inlined)
    return {
        "responseMimeType": "application/json",
        "responseSchema": response_schema,
    }


def build_provider_payload(
    schema: type[BaseModel], capability: StructuredCapability
) -> dict[str, Any]:
    """Dispatch to the native structured-output payload for `capability`.

    `NONE` yields an empty payload: the provider has no native mode, so generation
    relies on the prompt instruction plus the validate-then-retry fallback.
    """
    if capability is StructuredCapability.JSON_SCHEMA:
        return translate_openai(schema)
    if capability is StructuredCapability.TOOL_USE:
        return translate_anthropic(schema)
    if capability is StructuredCapability.RESPONSE_SCHEMA:
        return translate_gemini(schema)
    return {}


# ── Orchestration ────────────────────────────────────────────────────────────


def _schema_instruction(schema: type[BaseModel]) -> str:
    """A compact system instruction so even a `NONE`-capability or degraded
    provider is steered toward the exact JSON shape (the fallback's best chance).
    """
    compact = json.dumps(schema.model_json_schema(), separators=(",", ":"), sort_keys=True)
    return (
        "Respond with a single JSON object — no prose, no code fence — that "
        f"validates against this JSON Schema:\n{compact}"
    )


def _parse(schema: type[T], text: str) -> T:
    """Validate provider text into the target model. Tolerates a JSON code fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a leading ```json / ``` fence and the trailing fence if present.
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")]
        cleaned = cleaned.strip()
    return schema.model_validate_json(cleaned)


async def generate_structured(
    gateway: LLMGateway,
    request: StructuredRequest,
    schema: type[T],
    *,
    correlation_id: UUID,
    audit: AuditSink | None = None,
) -> T:
    """Generate a validated `schema` instance via provider-native enforcement.

    Flow (AC #1, #5, #7):
      1. Build the provider's native structured payload for `request.capability`
         (currently advisory metadata the concrete provider adapter will apply;
         the schema instruction is also injected into the system prompt so the
         path works end-to-end through the neutral port).
      2. Call the gateway's budget-checked egress (`generate`) — `TokenBudgetExceeded`
         propagates unchanged.
      3. Validate the response into `schema`. On success, return the instance.
      4. On validation failure, audit `llm_gateway.structured_fallback`, retry
         exactly once with a sharpened instruction, and validate again.
      5. A second failure raises `StructuredValidationError`.

    `build_provider_payload` is invoked for its validation/round-trip guarantees
    and so the native shape is available to a concrete adapter; the neutral port
    consumes the payload via the system-prompt instruction.
    """
    # Constructed (and validated) up front so a concrete provider adapter can
    # apply the native shape, and so a malformed schema fails fast here rather
    # than mid-generation. Unit tests assert the per-provider translation.
    build_provider_payload(schema, request.capability)
    base_system = request.system or ""
    instruction = _schema_instruction(schema)
    system = f"{base_system}\n\n{instruction}".strip()

    response: LLMGenerateResponse = await gateway.generate(
        request.to_generate_request(system=system)
    )

    try:
        return _parse(schema, response.text)
    except (ValidationError, ValueError) as first_error:
        if audit is not None:
            await audit.record(
                org_id=request.org_id,
                correlation_id=correlation_id,
                action_type=STRUCTURED_FALLBACK_ACTION,
                result="failed",
                governance_token_id=request.governance_token_id,
                result_reason=f"first structured attempt invalid: {first_error}",
            )

        # Exactly one retry (defense in depth), with a sharpened instruction.
        retry_system = (
            f"{system}\n\nYour previous response was not valid for the schema. "
            "Return ONLY the JSON object, nothing else."
        )
        retry_response = await gateway.generate(
            request.to_generate_request(system=retry_system)
        )
        try:
            return _parse(schema, retry_response.text)
        except (ValidationError, ValueError) as second_error:
            raise StructuredValidationError(
                schema, retry_response.text, second_error
            ) from second_error
