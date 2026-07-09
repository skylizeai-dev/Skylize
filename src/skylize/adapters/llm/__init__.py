"""Provider-abstracted LLM gateway (anthropic.md, 01_final_stack.md §4.8)."""

from __future__ import annotations

from .gateway import (
    LLMGateway,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMUsage,
    TokenBudgetExceeded,
)
from .structured import (
    STRUCTURED_FALLBACK_ACTION,
    StructuredCapability,
    StructuredRequest,
    StructuredValidationError,
    build_provider_payload,
    generate_structured,
    translate_anthropic,
    translate_gemini,
    translate_openai,
)

__all__ = [
    "LLMGateway",
    "LLMGenerateRequest",
    "LLMGenerateResponse",
    "LLMUsage",
    "TokenBudgetExceeded",
    "STRUCTURED_FALLBACK_ACTION",
    "StructuredCapability",
    "StructuredRequest",
    "StructuredValidationError",
    "build_provider_payload",
    "generate_structured",
    "translate_anthropic",
    "translate_gemini",
    "translate_openai",
]
