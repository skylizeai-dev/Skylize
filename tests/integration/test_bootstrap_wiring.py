"""Composition-root wiring guards.

The unit tests for the closed loop (test_deliverable_approval_embed.py) build
``DeliverableService`` by hand, so they cannot catch a bootstrap that forgets to
pass ``knowledge_ingestion`` into it — the injection that makes approved
deliverables embed back into tenant knowledge. These tests exercise the real
``build_container`` so that regression fails loudly.
"""

from __future__ import annotations

from skylize.bootstrap import build_container
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
