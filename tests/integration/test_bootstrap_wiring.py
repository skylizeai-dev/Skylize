"""Composition-root wiring guards.

The unit tests for the closed loop (test_deliverable_approval_embed.py) build
``DeliverableService`` by hand, so they cannot catch a bootstrap that forgets to
pass ``knowledge_ingestion`` into it — the injection that makes approved
deliverables embed back into tenant knowledge. These tests exercise the real
``build_container`` so that regression fails loudly.
"""

from __future__ import annotations

import pytest

from skylize.adapters.llm.content_gate import GuardedLLMGateway
from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.bootstrap import LLMConfigurationError, build_container
from skylize.config import Settings


async def test_knowledge_ingestion_injected_into_deliverable_service() -> None:
    """When the vector backend is configured, bootstrap must wire the same
    KnowledgeIngestionService into DeliverableService (else the closed loop is
    silently dead in production)."""
    # qdrant_url defaults to a value; supplying an openai key flips the bootstrap
    # branch that builds the service. Constructors only instantiate clients (no
    # network), so this is safe with a fake key.
    settings = Settings(backend="memory", openai_api_key="sk-test-not-a-real-key")
    container = await build_container(settings)

    assert container.knowledge_ingestion is not None
    # The service DeliverableService holds must be the very instance the
    # container exposes — not a second, unwired one.
    assert container.deliverables._knowledge_ingestion is container.knowledge_ingestion


async def test_deliverable_service_tolerates_absent_vector_backend() -> None:
    """With no OpenAI key the vector backend is absent; DeliverableService must
    still be built (closed loop degrades to a no-op, approval never fails)."""
    settings = Settings(backend="memory", openai_api_key="")
    container = await build_container(settings)

    assert container.knowledge_ingestion is None
    assert container.deliverables._knowledge_ingestion is None


async def test_build_fails_closed_without_api_key_or_demo_flag() -> None:
    """No anthropic_api_key AND no explicit demo flag -> the container refuses to
    build with a typed error that names the missing variable, instead of silently
    serving fake demo output."""
    settings = Settings(backend="memory", anthropic_api_key="", llm_demo_mode=False)
    with pytest.raises(LLMConfigurationError, match="SKYLIZE_ANTHROPIC_API_KEY"):
        await build_container(settings)


async def test_demo_mode_requires_explicit_flag_and_wires_demo_adapter() -> None:
    """With the explicit demo flag set (and no key), the demo adapter is wired
    behind the shared content-gate wrapper."""
    settings = Settings(backend="memory", anthropic_api_key="", llm_demo_mode=True)
    container = await build_container(settings)
    try:
        assert isinstance(container.llm, GuardedLLMGateway)
        assert isinstance(container.llm._gateway, DemoLLMAdapter)
    finally:
        await container.aclose()
