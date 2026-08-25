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


async def test_principal_authority_is_wired_into_both_consumers_as_one_instance() -> None:
    """`GovernanceAuthority.mint` and `AgentExecutionService` ask different
    questions of the principal's authority -- mint gates the requested scope
    against it, the execution service derives which scope to request -- so both
    must hold it, and it must be the SAME instance. Two providers would be two
    answers to "what may this human do", which is the one thing that must have a
    single source."""
    settings = Settings(backend="memory", anthropic_api_key="sk-test-not-a-real-key")
    container = await build_container(settings)
    try:
        provider = container.agent_execution._principal_authority
        assert provider is not None, "AgentExecutionService has no principal provider"
        assert container.authority._principal_authority is provider
    finally:
        await container.aclose()


async def test_wiring_the_provider_changes_nothing_without_a_principal() -> None:
    """The registration must be INERT for every existing request path.

    No current caller passes `on_behalf_of_principal`, so the provider must never
    be consulted on those paths -- proven by installing one that detonates if
    touched and then walking the same code the autonomous shape walks. A provider
    that were consulted here would mean every ordinary execute() had started
    depending on principal rows that mostly do not exist.
    """
    from skylize.contracts.registry import MVP_REGISTRY

    class _Detonating:
        async def snapshot_for(self, *, org_id: str, principal_id: str, at: object) -> object:
            raise AssertionError(
                "the principal provider was consulted on a path that passes no "
                "on_behalf_of_principal"
            )

    settings = Settings(backend="memory", anthropic_api_key="sk-test-not-a-real-key")
    container = await build_container(settings)
    try:
        container.agent_execution._principal_authority = _Detonating()  # type: ignore[assignment]
        contract = MVP_REGISTRY.resolve("hook_generator_agent")

        # None means "request no scope override", which is exactly mint's own
        # default branch -- so the mint behaves as it did before the parameter.
        scope = await container.agent_execution._principal_scope_for(
            contract, org_id="org_a", principal_id=None
        )
        assert scope is None
    finally:
        await container.aclose()


async def test_container_aclose_disposes_the_anthropic_egress_clients() -> None:
    """P3: the adapter's SDK clients are released by Container.aclose(), not GC.

    The adapter now builds ONE sync and ONE async client per instance and reuses
    them for every call, so nothing else would ever close their TCP pools. The
    disposal must be registered on the composition root's closer list — the
    GuardedLLMGateway wrap hides `aclose`, so a bootstrap that forgot to register
    it while the concrete adapter was in hand would leak silently.
    """
    from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter

    settings = Settings(backend="memory", anthropic_api_key="sk-test-not-a-real-key")
    container = await build_container(settings)

    assert isinstance(container.llm, GuardedLLMGateway)
    adapter = container.llm._gateway
    assert isinstance(adapter, AnthropicAdapter)

    # Force both pools open WITHOUT any network call: the client objects are
    # constructed lazily on first use, and constructing one opens no socket.
    sync_client = adapter._sync_client()
    async_client = adapter._async_client()
    assert sync_client.is_closed() is False
    assert async_client.is_closed() is False

    await container.aclose()

    assert sync_client.is_closed() is True, "sync egress pool survived aclose()"
    assert async_client.is_closed() is True, "async egress pool survived aclose()"
