"""
Composition root — wires concrete adapters into the application services.

This is the one place (besides the DAL) allowed to import database/Redis
concretes; it selects implementations from `Settings.backend`:
  - "memory":   in-memory repos + in-memory bus (tests / no-infra local run)
  - "postgres": asyncpg repos + Redis Streams bus (docker-compose / production)

Everything above this layer depends only on ports, so the swap is invisible to
the Governance Authority, Audit service, and Orchestrator.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dal.connection import Database

from .adapters.llm.content_gate import GuardedLLMGateway, LLMContentGate
from .adapters.llm.demo_adapter import DemoLLMAdapter
from .adapters.llm.gateway import LLMGateway
from .app.agents.execution import AgentExecutionService
from .app.audit.service import AuditService
from .app.auth.service import ApiKeyService
from .app.auth.user_service import UserAuthService
from .app.credentials.encryption import FernetEncryptor
from .app.credentials.vault import CredentialVault
from .app.decision_engine import DecisionEngine
from .app.deliverables.service import DeliverableService
from .app.governance.authority import GovernanceAuthority
from .app.governance.broadcast import GovernanceBroadcast
from .app.orchestrator import LLMStepRunner, Orchestrator
from .app.tenants.service import TenantService
from .config import Settings, get_settings
from .contracts.registry import MVP_REGISTRY
from .dal.credentials import CredentialRepository
from .dal.ports import (
    ApiKeyRepository,
    AuditRepository,
    CapitalRepository,
    DeliverableRepository,
    GovernanceRepository,
    ProcessedEventStore,
    TenantRepository,
    UserRepository,
)
from .events.bus import EventBus
from .memory.knowledge_ingestion import KnowledgeIngestionService
from .tools.builtin import default_tool_registry
from .tools.proxy import ToolProxy


class LLMConfigurationError(RuntimeError):
    """The LLM gateway cannot be wired from the current configuration.

    Raised at composition time so a misconfigured deployment fails to build
    rather than serving fake demo output under a real workload."""


@dataclass
class Container:
    settings: Settings
    bus: EventBus
    audit: AuditService
    authority: GovernanceAuthority
    orchestrator: Orchestrator
    tenants: TenantService
    api_keys: ApiKeyService
    user_auth: UserAuthService
    deliverables: DeliverableService
    credential_vault: CredentialVault
    agent_execution: AgentExecutionService
    knowledge_ingestion: KnowledgeIngestionService | None
    decision_engine: DecisionEngine
    # The single shared content-gated gateway reference. Anything that makes
    # LLM calls (incl. the Temporal worker's LLMJudge) must take THIS, never a
    # bare provider adapter.
    llm: LLMGateway
    _closers: list[Callable[[], Awaitable[None]]]
    # The connection pool on the postgres backend (None on memory). Exposed for
    # sibling processes composed from this root — the Temporal worker builds
    # PgWorkflowRepository(container.db) — not for request-path use: services
    # above this layer keep depending on ports, never on the pool.
    db: "Database | None" = None

    async def aclose(self) -> None:
        # LIFO, like ExitStack: consumers/subscribers are registered after the
        # db/redis concretes they read from, so they must stop FIRST and the
        # pools close last. Append order used to run db/redis close first,
        # which made the governance subscriber's blocked read die on a closed
        # connection and turned clean shutdown into a ConnectionError.
        for closer in reversed(self._closers):
            await closer()


async def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    registry = MVP_REGISTRY
    closers: list[Callable[[], Awaitable[None]]] = []

    bus: EventBus
    gov_repo: GovernanceRepository
    audit_repo: AuditRepository
    tenant_repo: TenantRepository
    apikey_repo: ApiKeyRepository
    user_repo: UserRepository
    deliverable_repo: DeliverableRepository
    credential_repo: CredentialRepository
    broadcast: GovernanceBroadcast
    db: Database | None = None
    # Decision Engine side stores: None → the engine's in-memory defaults
    # (memory backend); the postgres branch below swaps in the durable stores.
    capital_repo: CapitalRepository | None = None
    processed_store: ProcessedEventStore | None = None

    if settings.backend == "memory":
        from .app.governance.broadcast import InMemoryGovernanceBroadcast
        from .dal.credentials import InMemoryCredentialRepository
        from .dal.memory import (
            InMemoryApiKeyRepository,
            InMemoryAuditRepository,
            InMemoryDeliverableRepository,
            InMemoryGovernanceRepository,
            InMemoryTenantRepository,
            InMemoryUserRepository,
        )
        from .events.memory_bus import InMemoryEventBus

        bus = InMemoryEventBus()
        gov_repo = InMemoryGovernanceRepository()
        audit_repo = InMemoryAuditRepository()
        tenant_repo = InMemoryTenantRepository()
        apikey_repo = InMemoryApiKeyRepository()
        user_repo = InMemoryUserRepository()
        deliverable_repo = InMemoryDeliverableRepository()
        credential_repo = InMemoryCredentialRepository()
        broadcast = InMemoryGovernanceBroadcast()
    else:
        from .dal.connection import Database
        from .dal.credentials import PgCredentialRepository
        from .dal.decision_stores import PgCapitalRepository, PgProcessedEventStore
        from .dal.deliverables import PgDeliverableRepository
        from .dal.repositories import (
            PgApiKeyRepository,
            PgAuditRepository,
            PgContractRepository,
            PgGovernanceRepository,
            PgTenantRepository,
        )
        from .dal.users import PgUserRepository
        from .events.redis_adapter import RedisEventBus
        from .events.redis_governance_broadcast import RedisGovernanceBroadcast

        # Runtime connects as the non-superuser app role so RLS actually applies
        # (migrations run separately as the admin role via `alembic upgrade`).
        db = Database(settings.runtime_db_url)
        await db.connect()
        redis_bus = RedisEventBus(settings.redis_url)
        bus = redis_bus
        gov_repo = PgGovernanceRepository(db)
        audit_repo = PgAuditRepository(db)
        tenant_repo = PgTenantRepository(db)
        apikey_repo = PgApiKeyRepository(db)
        user_repo = PgUserRepository(db)
        deliverable_repo = PgDeliverableRepository(db)
        credential_repo = PgCredentialRepository(db)
        capital_repo = PgCapitalRepository(db)
        processed_store = PgProcessedEventStore(db)
        redis_broadcast = RedisGovernanceBroadcast(settings.redis_url)
        broadcast = redis_broadcast

        # Seed the agent contract registry into Postgres (idempotent).
        contract_repo = PgContractRepository(db)
        for contract in registry.all():
            await contract_repo.upsert(contract.agent_id, 1, contract.model_dump_json())

        closers.append(db.close)
        closers.append(redis_bus.close)
        closers.append(redis_broadcast.close)

    audit = AuditService(bus, audit_repo)
    tenants = TenantService(tenant_repo, audit)
    api_keys = ApiKeyService(apikey_repo, audit)

    # Human-user auth (register/login/refresh + /me).
    user_auth = UserAuthService(user_repo, settings)

    # Content gate: deterministic prompt-injection screen. Constructed HERE,
    # ahead of the knowledge store and deliverables, so the very same shared
    # instance screens every knowledge ingest path (ingest / ingest_document)
    # before an embed/upsert can reach Qdrant. The LLM gateway wraps this same
    # gate below, so LLM egress is screened by the identical instance. Business
    # authz (decision_engine) is deliberately kept OFF this gate.
    content_gate = LLMContentGate()

    # Knowledge vector store (tenant-isolated). None when the vector backend is
    # unconfigured; every consumer degrades gracefully.
    knowledge_ingestion: KnowledgeIngestionService | None = None
    if settings.qdrant_url and settings.openai_api_key:
        from .memory.embedding_service import EmbeddingService
        from .memory.qdrant_adapter import QdrantAdapter
        knowledge_ingestion = KnowledgeIngestionService(
            qdrant=QdrantAdapter(settings.qdrant_url, settings.qdrant_api_key),
            embedding_service=EmbeddingService(settings.openai_api_key),
            content_gate=content_gate,
        )

    # Agent-produced deliverables (versioned, human-approvable). Approved
    # deliverables embed back into the tenant's knowledge memory (closed loop).
    deliverables = DeliverableService(deliverable_repo, knowledge_ingestion)

    # Credential vault (at-rest Fernet encryption). With no configured key the
    # memory backend mints an ephemeral one — fine for dev/tests; production sets
    # `credential_encryption_key` so stored credentials survive a restart.
    encryptor = FernetEncryptor(
        settings.credential_encryption_key or FernetEncryptor.generate_key()
    )
    credential_vault = CredentialVault(encryptor, credential_repo, audit)

    authority = GovernanceAuthority.build(
        repo=gov_repo, audit=audit, bus=bus, registry=registry, settings=settings,
        broadcast=broadcast,
    )
    # Warm the snapshot from the DB system of record BEFORE serving requests, so
    # a restart never forgets an active kill switch / revocation / suspension.
    await authority.rehydrate()
    # Launch the cross-instance invalidation subscriber as a background task so
    # a kill/revoke on any instance reaches this one's snapshot.
    subscriber_task = asyncio.create_task(authority.start_subscriber())

    async def _stop_subscriber() -> None:
        subscriber_task.cancel()
        with suppress(asyncio.CancelledError):
            await subscriber_task

    closers.append(_stop_subscriber)

    # Engine selection (SKYLIZE_DECISION_ENGINE). This composition root wires the
    # inline engine and only the inline engine. The OPA-backed decision_engine
    # package is now a separate worker PROCESS
    # (`python -m skylize.decision_engine.worker`), not an alternative wiring
    # here — its consumer was rebuilt onto the EventBus port per ADR-0005, but it
    # owns its own Postgres/Redis/OPA concretes and is selected by the same flag
    # from the other side (`worker.require_opa_engine`).
    #
    # So the guard stays, and it is now an interlock rather than a
    # not-implemented notice: exactly one engine per environment may emit
    # terminal `decision.*` events (decision_engine.md §2). Under 'opa' the API
    # process must NOT also run inline, and failing closed here is what stops it.
    # The flag remains 'inline' everywhere until the OPA engine's HITL resume
    # path, real Rego, and a live OPA server land.
    # See ADR-0004: docs/architecture/adr/0004-opa-production-arbiter.md
    if settings.decision_engine != "inline":
        raise RuntimeError(
            f"SKYLIZE_DECISION_ENGINE={settings.decision_engine!r}: this process wires "
            "only the inline engine. The OPA engine runs as its own worker "
            "(python -m skylize.decision_engine.worker) and is not production-ready "
            "(no HITL resume path, no real Rego, no live OPA server)."
        )

    # Decision Engine (business-action authz; per environment, exactly one
    # engine emits terminal `decision.*` events, selected by
    # SKYLIZE_DECISION_ENGINE). Wired behind the CapitalRepository /
    # ProcessedEventStore ports — durable Pg stores on the postgres backend
    # (budget_ledger + decision_processed_events, migration 0011), in-memory
    # defaults on the memory backend. With no configured orgs it is wired but
    # idle; tenants are subscribed as they are provisioned
    # (SKYLIZE_DECISION_ENGINE_ORG_IDS seeds subscriptions at startup).
    # Deliberately NOT handed the LLM gateway: business authz and LLM content
    # safety (content_gate) stay separate.
    decision_engine = DecisionEngine(
        bus, registry, audit, settings,
        capital=capital_repo, processed=processed_store,
    )
    await decision_engine.start()
    for org_id in settings.decision_engine_org_ids:
        decision_engine.subscribe(org_id)
    closers.append(decision_engine.stop)

    # LLM gateway: the live Anthropic adapter when a key is configured. With no
    # key we fail closed rather than silently serving fake output — UNLESS demo
    # mode is explicitly opted into (llm_demo_mode), in which case the
    # DemoLLMAdapter is wired and it logs a WARNING on every call.
    llm: LLMGateway
    if settings.anthropic_api_key:
        from .adapters.llm.anthropic_adapter import AnthropicAdapter
        from .adapters.llm.spend_ceiling import SpendCeilingEnforcer
        from .dal.cost_ledger import CostLedgerDAL
        from .dal.org_spend_ceiling import OrgSpendCeilingDAL

        # Billing-grade cost ledger (ADR-0006): wired on the postgres backend
        # so every Anthropic egress is price-gated pre-call and recorded
        # post-call. On the memory backend there is no durable store, so the
        # adapter falls back to Settings-float estimates (logged at WARNING).
        cost_ledger = CostLedgerDAL(db) if db is not None else None
        # Org spend-ceiling gate (migration 0014): wired alongside the ledger on
        # the postgres backend. Both egresses refuse a call before egress when
        # period-to-date spend plus a biased-high estimate would breach the
        # org-wide ceiling; a missing ceiling row fails closed. No ceiling store on
        # the memory backend, so the gate is left unwired (None) there.
        spend_ceiling = (
            SpendCeilingEnforcer(
                ceiling_dal=OrgSpendCeilingDAL(db),
                cost_ledger=cost_ledger,
                audit=audit,
                bus=bus,
            )
            if db is not None and cost_ledger is not None
            else None
        )
        llm = AnthropicAdapter(
            settings=settings,
            cost_ledger=cost_ledger,
            spend_ceiling=spend_ceiling,
        )
    elif settings.llm_demo_mode:
        llm = DemoLLMAdapter()
    else:
        raise LLMConfigurationError(
            "SKYLIZE_ANTHROPIC_API_KEY is not set. Refusing to build the container "
            "with a silent demo fallback. Set SKYLIZE_ANTHROPIC_API_KEY for real "
            "LLM egress, or set SKYLIZE_LLM_DEMO_MODE=true to explicitly run the "
            "non-production demo adapter."
        )

    # Content gate (constructed above, shared with the knowledge store) wraps the
    # single gateway reference, so every downstream holder of `llm`
    # (Orchestrator's LLMStepRunner, AgentExecutionService, ToolProxy's LLM
    # dispatch) is gated uniformly without a per-call-site change.
    llm = GuardedLLMGateway(llm, gate=content_gate)

    # The workflow agent step runs the same governed LLM gateway as direct
    # execution — no stubbed output on any reachable path.
    orchestrator = Orchestrator(
        registry=registry, authority=authority, audit=audit, bus=bus,
        runner=LLMStepRunner(llm),
    )
    # Governed tool dispatch (IF-TOOL). Every tool_use block from the LLM passes
    # through the proxy: token signature/expiry/revocation/scope/budget checks
    # against the live governance snapshot, then audit. Null ports back
    # memory.search / search.web until real providers are wired.
    tool_proxy = ToolProxy(
        registry=default_tool_registry(credential_vault=credential_vault),
        audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
    )
    agent_execution = AgentExecutionService(
        registry, llm, deliverables, tools=tool_proxy, authority=authority, audit=audit
    )

    return Container(
        settings=settings, bus=bus, audit=audit, authority=authority,
        orchestrator=orchestrator, tenants=tenants, api_keys=api_keys,
        user_auth=user_auth, deliverables=deliverables,
        credential_vault=credential_vault, agent_execution=agent_execution,
        knowledge_ingestion=knowledge_ingestion, decision_engine=decision_engine,
        llm=llm, _closers=closers, db=db,
    )
