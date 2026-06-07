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

from .app.audit.service import AuditService
from .app.auth.service import ApiKeyService
from .app.governance.authority import GovernanceAuthority
from .app.governance.broadcast import GovernanceBroadcast
from .app.orchestrator import Orchestrator, StubAgentRunner
from .app.tenants.service import TenantService
from .config import Settings, get_settings
from .contracts.registry import MVP_REGISTRY
from .dal.ports import ApiKeyRepository, AuditRepository, GovernanceRepository, TenantRepository
from .events.bus import EventBus


@dataclass
class Container:
    settings: Settings
    bus: EventBus
    audit: AuditService
    authority: GovernanceAuthority
    orchestrator: Orchestrator
    tenants: TenantService
    api_keys: ApiKeyService
    _closers: list[Callable[[], Awaitable[None]]]

    async def aclose(self) -> None:
        for closer in self._closers:
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
    broadcast: GovernanceBroadcast

    if settings.backend == "memory":
        from .app.governance.broadcast import InMemoryGovernanceBroadcast
        from .dal.memory import (
            InMemoryApiKeyRepository,
            InMemoryAuditRepository,
            InMemoryGovernanceRepository,
            InMemoryTenantRepository,
        )
        from .events.memory_bus import InMemoryEventBus

        bus = InMemoryEventBus()
        gov_repo = InMemoryGovernanceRepository()
        audit_repo = InMemoryAuditRepository()
        tenant_repo = InMemoryTenantRepository()
        apikey_repo = InMemoryApiKeyRepository()
        broadcast = InMemoryGovernanceBroadcast()
    else:
        from .dal.connection import Database
        from .dal.repositories import (
            PgApiKeyRepository,
            PgAuditRepository,
            PgContractRepository,
            PgGovernanceRepository,
            PgTenantRepository,
        )
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

    orchestrator = Orchestrator(
        registry=registry, authority=authority, audit=audit, bus=bus, runner=StubAgentRunner()
    )

    return Container(
        settings=settings, bus=bus, audit=audit, authority=authority,
        orchestrator=orchestrator, tenants=tenants, api_keys=api_keys, _closers=closers,
    )
