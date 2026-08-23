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
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dal.connection import Database
    from .dal.cost_ledger import CostLedgerDAL
    from .dal.org_spend_ceiling import OrgSpendCeilingDAL

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
from .app.hitl.service import HitlQueueService
from .app.orchestrator import LLMStepRunner, Orchestrator
from .app.principal.journal import JournalRepository, WorkJournal
from .app.principal.provider import PrincipalAuthorityService, PrincipalRepository
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
    HitlQueueRepository,
    ProcessedEventStore,
    TenantRepository,
    UserRepository,
)
from .events.bus import EventBus
from .memory.knowledge_ingestion import KnowledgeIngestionService
from .tools.builtin import default_tool_registry
from .tools.proxy import ToolProxy


class ConfigurationError(RuntimeError):
    """The container cannot be wired from the current configuration.

    Raised at composition time so a misconfigured deployment fails to build
    rather than starting with a silently broken guarantee."""


class LLMConfigurationError(ConfigurationError):
    """The LLM gateway cannot be wired from the current configuration.

    Raised at composition time so a misconfigured deployment fails to build
    rather than serving fake demo output under a real workload."""


log = logging.getLogger("skylize.bootstrap")


def resolve_credential_encryption_key(settings: Settings) -> str:
    """Return the credential-vault Fernet key, failing closed on a real backend.

    Resolution order mirrors `app/governance/keys.py::load_signing_key`, which
    answers the identical question for the Authority signing key:
      1. `credential_encryption_key` (SKYLIZE_CREDENTIAL_ENCRYPTION_KEY).
      2. (real backend) error — no key, no start.
      3. (memory backend only) mint an ephemeral key.

    Step 2 is the point. `org_credentials.encrypted_value` is NOT NULL
    (migration 0007) and the row outlives the process that wrote it, but a
    per-boot random key does not: every credential stored under one became
    permanently undecryptable the moment that pod restarted, and the vault
    surfaced it as `DecryptionError("wrong key or corrupted ciphertext")`
    (app/credentials/encryption.py:20-21) — a corruption message for what was
    really a configuration mistake made one restart earlier. The old fallback
    made that outcome the DEFAULT for a production deployment that simply
    forgot the variable, with no signal at boot. The ephemeral path survives
    only for `backend == "memory"`, where `InMemoryCredentialRepository`
    (bootstrap.py:230) discards the rows at process exit anyway, so there is
    nothing left to be undecryptable.

    The key is parsed here rather than at first use so a malformed or
    placeholder value fails the boot too, with a message naming the variable,
    instead of raising a bare `cryptography` ValueError deeper in the wiring.
    """
    key = settings.credential_encryption_key.strip()
    if key:
        try:
            FernetEncryptor(key)
        except Exception as exc:  # noqa: BLE001 — any parse failure is fatal
            raise ConfigurationError(
                "SKYLIZE_CREDENTIAL_ENCRYPTION_KEY is set but is not a valid "
                f"Fernet key ({exc}). It must be urlsafe base64, 32 bytes. "
                'Generate one with: python -c "from cryptography.fernet import '
                'Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
        return key

    if settings.backend != "memory":
        raise ConfigurationError(
            "No credential encryption key configured. Set "
            "SKYLIZE_CREDENTIAL_ENCRYPTION_KEY when SKYLIZE_BACKEND is not "
            f"'memory' (got backend={settings.backend!r}). Credentials are "
            "stored in org_credentials and outlive the process; an ephemeral "
            "per-boot key would make every one of them undecryptable at the "
            "next restart. Refusing to start. Generate a key with: python -c "
            '"from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" and hold it as a real '
            "secret — losing it is losing every stored credential."
        )

    log.warning(
        "No credential encryption key configured; generating an EPHEMERAL "
        "Fernet key. This is allowed only for the in-memory/dev backend "
        "(credentials are discarded at process exit) and must never be used "
        "in production."
    )
    return FernetEncryptor.generate_key()


async def verify_app_role_is_rls_subject(db: "Database") -> None:
    """Refuse to start when the runtime database role can bypass RLS.

    The Settings interlock (config.py `_require_distinct_app_dsn_on_a_real_backend`)
    is a raw string comparison: it catches SKYLIZE_DB_APP_URL left empty or
    copy-pasted equal to SKYLIZE_DB_URL, but any respelling of the same
    superuser DSN (localhost vs 127.0.0.1, an added query parameter, a password
    moved to .pgpass, postgres:// vs postgresql://) sails through it. The
    authoritative check is not a string comparison — it is asking the database:
    connect as the configured role and read its own pg_roles row. A SUPERUSER
    or BYPASSRLS role bypasses every RLS policy regardless of FORCE ROW LEVEL
    SECURITY.

    Mirrors the pg_roles probe in tests/integration/test_postgres_isolation.py::
    test_app_role_is_not_superuser_or_bypassrls, folded into one statement run
    on the runtime's own pool.

    A connection failure is NOT a disqualified role: asyncpg's own errors from
    acquire/fetch propagate untouched, so an unreachable database surfaces as a
    connectivity error, never as an RLS finding. ConfigurationError is raised
    only from a successfully read pg_roles row.
    """
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT current_user AS rolname, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
    if row is None:  # pragma: no cover — current_user always has a pg_roles row
        raise ConfigurationError(
            "Could not read the runtime role's pg_roles row to verify it is "
            "subject to RLS; refusing to start rather than assuming it is."
        )
    disqualifiers = [
        name
        for flag, name in ((row["rolsuper"], "SUPERUSER"), (row["rolbypassrls"], "BYPASSRLS"))
        if flag
    ]
    if disqualifiers:
        raise ConfigurationError(
            f"Runtime database role {row['rolname']!r} (SKYLIZE_DB_APP_URL) has "
            f"{' and '.join(disqualifiers)}. Such a role bypasses every "
            "row-level-security policy regardless of FORCE ROW LEVEL SECURITY, "
            "so RLS tenant isolation would be silently inert while every "
            "request still succeeds. Connect the runtime as the non-superuser, "
            "NOBYPASSRLS skylize_app role created by migration 0003."
        )


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
    hitl: HitlQueueService
    knowledge_ingestion: KnowledgeIngestionService | None
    decision_engine: DecisionEngine
    # The single shared content-gated gateway reference. Anything that makes
    # LLM calls (incl. the Temporal worker's LLMJudge) must take THIS, never a
    # bare provider adapter.
    llm: LLMGateway
    # Append-only, principal-scoped log read by GET /me/brief. See
    # dal/work_journal.py for why nothing writes to it yet.
    work_journal: WorkJournal
    _closers: list[Callable[[], Awaitable[None]]]
    # The connection pool on the postgres backend (None on memory). Exposed for
    # sibling processes composed from this root — the Temporal worker builds
    # PgWorkflowRepository(container.db) — not for request-path use: services
    # above this layer keep depending on ports, never on the pool.
    db: "Database | None" = None
    # Read-side spend stores on the postgres backend (None on memory). The
    # spend position route reads period-to-date spend and the effective-dated
    # ceiling through these; the ceiling WRITE stays an operator action through
    # the audited OrgSpendCeilingDAL.set_ceiling seam — no route writes it.
    cost_ledger: "CostLedgerDAL | None" = None
    spend_ceiling_dal: "OrgSpendCeilingDAL | None" = None

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
    # Resolved BEFORE the backend branch below opens any pool: a deployment
    # missing this key must fail while it is still doing nothing, not after
    # Postgres and Redis connections are live. Consumed at the vault below.
    credential_encryption_key = resolve_credential_encryption_key(settings)
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
    # Request-path HITL writer for the synchronous decision gate (owner decision
    # K3). In-memory on the memory backend; the durable Pg writer on postgres.
    hitl_repo: HitlQueueRepository
    # Work journal (skylize.app.principal): append-only, principal-scoped log
    # read by GET /me/brief. Written non-transactionally (best-effort, logged
    # not raised on failure) from edge/routes/cowork.py:204 (chat turns) and
    # app/hitl/service.py:376 (HITL replay approval) — see those call sites
    # for why the write can't share the deliverable's transaction.
    journal_repo: JournalRepository
    # Human principals + their grants (migration 0019). Read-only: this feeds
    # PrincipalAuthorityService, which GovernanceAuthority.mint consults ONLY when
    # a caller passes `on_behalf_of_principal`. No existing request path does, so
    # wiring it changes no current behaviour -- it removes a fail-closed refusal
    # that the per-employee shape would otherwise hit.
    principal_repo: PrincipalRepository

    if settings.backend == "memory":
        from .app.governance.broadcast import InMemoryGovernanceBroadcast
        from .dal.credentials import InMemoryCredentialRepository
        from .app.principal.provider import InMemoryPrincipalRepository
        from .dal.memory import (
            InMemoryApiKeyRepository,
            InMemoryAuditRepository,
            InMemoryDeliverableRepository,
            InMemoryGovernanceRepository,
            InMemoryHitlQueueRepository,
            InMemoryJournalRepository,
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
        hitl_repo = InMemoryHitlQueueRepository()
        journal_repo = InMemoryJournalRepository()
        principal_repo = InMemoryPrincipalRepository()
    else:
        from .dal.connection import Database
        from .dal.credentials import PgCredentialRepository
        from .dal.decision_stores import PgCapitalRepository, PgProcessedEventStore
        from .dal.deliverables import PgDeliverableRepository
        from .dal.hitl import PgHitlQueueRepository
        from .dal.repositories import (
            PgApiKeyRepository,
            PgAuditRepository,
            PgContractRepository,
            PgGovernanceRepository,
            PgTenantRepository,
        )
        from .dal.principal import PgPrincipalRepository
        from .dal.users import PgUserRepository
        from .dal.work_journal import PostgresJournalRepository
        from .events.redis_adapter import RedisEventBus
        from .events.redis_governance_broadcast import RedisGovernanceBroadcast

        # Runtime connects as the non-superuser app role so RLS actually applies
        # (migrations run separately as the admin role via `alembic upgrade`).
        db = Database(settings.runtime_db_url)
        await db.connect()
        # The Settings string interlock cannot catch a respelled superuser DSN;
        # ask the database itself before wiring anything onto this pool.
        await verify_app_role_is_rls_subject(db)
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
        hitl_repo = PgHitlQueueRepository(db)
        journal_repo = PostgresJournalRepository(db)
        principal_repo = PgPrincipalRepository(db)
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
    work_journal = WorkJournal(journal_repo)

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

    # Credential vault (at-rest Fernet encryption). The key was resolved at the
    # top of this function; on a real backend an unset one already failed the
    # boot, so there is no `or generate_key()` fallback to reach here.
    encryptor = FernetEncryptor(credential_encryption_key)
    credential_vault = CredentialVault(encryptor, credential_repo, audit)

    # Compiles a human's effective authority from their grants. Passed to BOTH
    # consumers below because they ask different questions of it: mint gates the
    # requested scope against it, and AgentExecutionService derives which scope to
    # request in the first place. Neither is reached unless a caller supplies
    # `on_behalf_of_principal`, which no current request path does.
    principal_authority = PrincipalAuthorityService(principal_repo)

    authority = GovernanceAuthority.build(
        repo=gov_repo, audit=audit, bus=bus, registry=registry, settings=settings,
        broadcast=broadcast, principal_authority=principal_authority,
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
    # Billing-grade cost ledger (ADR-0006) and org spend-ceiling store
    # (migration 0014): constructed whenever the postgres pool exists (None on
    # memory — no durable store). Shared by the LLM egress gate below and the
    # read-only spend position route (edge/routes/spend.py) via the Container.
    from .dal.cost_ledger import CostLedgerDAL
    from .dal.org_spend_ceiling import OrgSpendCeilingDAL

    cost_ledger = CostLedgerDAL(db) if db is not None else None
    spend_ceiling_dal = OrgSpendCeilingDAL(db) if db is not None else None

    llm: LLMGateway
    if settings.anthropic_api_key:
        from .adapters.llm.anthropic_adapter import AnthropicAdapter
        from .adapters.llm.spend_ceiling import SpendCeilingEnforcer

        # Org spend-ceiling gate: wired alongside the ledger on the postgres
        # backend so every Anthropic egress is price-gated pre-call and recorded
        # post-call. Both egresses refuse a call before egress when
        # period-to-date spend plus a biased-high estimate would breach the
        # org-wide ceiling; a missing ceiling row fails closed. No ceiling store
        # on the memory backend, so the gate is left unwired (None) there and
        # the adapter falls back to Settings-float estimates (logged at WARNING).
        spend_ceiling = (
            SpendCeilingEnforcer(
                ceiling_dal=spend_ceiling_dal,
                cost_ledger=cost_ledger,
                audit=audit,
                bus=bus,
            )
            if spend_ceiling_dal is not None and cost_ledger is not None
            else None
        )
        anthropic_adapter = AnthropicAdapter(
            settings=settings,
            cost_ledger=cost_ledger,
            spend_ceiling=spend_ceiling,
        )
        # The adapter builds its two SDK egress clients once, on first use, and
        # reuses them for every call; each owns a TCP connection pool. Register
        # the disposal here, on the SAME `_closers` list Container.aclose()
        # drains, so the pools are released deterministically at shutdown instead
        # of waiting on GC finalization. Registered while the concrete adapter is
        # still in hand — the GuardedLLMGateway wrap below hides `aclose`.
        # `_closers` runs LIFO, so this closes before the db/redis pools, which
        # is the right order: stop outbound HTTP first, tear down stores after.
        closers.append(anthropic_adapter.aclose)
        llm = anthropic_adapter
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
    # AgentExecutionService also carries the synchronous decision gate (owner
    # decisions D1/D3/D4/D5): the SAME pure evaluator the async engine uses
    # (decision_engine.evaluator, D2), the request-path HITL writer (K3), the bus
    # for terminal-event emission (D5), and the governed-org switch (D3). With no
    # governed orgs the gate is dormant and execution is unchanged.
    agent_execution = AgentExecutionService(
        registry, llm, deliverables, tools=tool_proxy, authority=authority, audit=audit,
        evaluator=decision_engine.evaluator, hitl=hitl_repo, bus=bus,
        governed_org_ids=frozenset(settings.decision_engine_org_ids),
        principal_authority=principal_authority,
    )
    # The human side of the gate: list pending escalations, approve (which
    # replays the stored request through the SAME agent_execution path with the
    # gate satisfied by the human verdict) or reject. Same repo instance the
    # gate enqueues into.
    hitl_service = HitlQueueService(
        repo=hitl_repo, execution=agent_execution, audit=audit, bus=bus,
        # Only a per-employee replay journals (the envelope must carry a
        # principal); an autonomous approval is unaffected.
        journal=work_journal,
    )

    return Container(
        settings=settings, bus=bus, audit=audit, authority=authority,
        orchestrator=orchestrator, tenants=tenants, api_keys=api_keys,
        user_auth=user_auth, deliverables=deliverables,
        credential_vault=credential_vault, agent_execution=agent_execution,
        hitl=hitl_service,
        knowledge_ingestion=knowledge_ingestion, decision_engine=decision_engine,
        llm=llm, work_journal=work_journal, _closers=closers, db=db,
        cost_ledger=cost_ledger, spend_ceiling_dal=spend_ceiling_dal,
    )
