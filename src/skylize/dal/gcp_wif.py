"""GCP WIF trust-state repository: rows, Protocol, asyncpg and in-memory impls.

Both tables are RLS-scoped (migration 0024), so every read and write goes through
``Database.tenant_session(org_id)``. Each statement ALSO carries an explicit
``org_id`` predicate, so the policy and the query agree rather than the query
relying on RLS alone - the same belt-and-braces discipline as
``PgOAuthCredentialRepository`` (dal/oauth_credentials.py) and
``PgCredentialRepository`` (dal/credentials.py:80-123).

NOTHING HERE IS ENCRYPTED, and nothing here needs to be. Unlike
``oauth_credentials``, which moves ciphertext and leaves the key to
``app/credentials/oauth.py``, a WIF connection stores no secret at all: every
column is either configuration the customer typed into their own Google Cloud
IAM console or a value Skylize publishes on a public endpoint. See migration
0024's docstring for why that is a property of the federation model rather than
an oversight.

THERE IS NO CROSS-TENANT READ IN THIS MODULE, DELIBERATELY. Every method takes an
``org_id`` and runs inside that org's session. An earlier draft carried a
``list_all_for_probe()`` so the health probe could enumerate orgs; it was removed
rather than fixed, for a reason worth recording so it is not reintroduced:

  * ``Database.admin_session()`` is the only non-tenant scope available, and it
    is documented to return NOTHING for an RLS table (dal/connection.py:83-87).
    Such a method would therefore have returned an empty list forever - a probe
    that silently checks nothing and reports every connection healthy by never
    looking, which is strictly worse than no probe.
  * The alternative, a ``rehydrate``-style carve-out in migration 0024's policy,
    would put the table naming every customer's stoppable infrastructure into the
    same bypass class as the governance snapshot. Migration 0021 refused exactly
    that for OAuth grants (0021:63-68) and this table is not a weaker case.

So org enumeration is the CALLER's job (see ``app/gcp/probe.py``): the probe is
handed the orgs to visit and re-enters ``tenant_session(org_id)`` for each. That
keeps this repository free of any RLS bypass and keeps migration 0024 out of
0002's carve-out list.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

#: Durable trust state. See migration 0024's docstring for the full rationale;
#: the short version is that each value maps to a DIFFERENT customer remedy:
#:   unverified    -> finish onboarding
#:   valid         -> nothing
#:   revoked       -> re-create the federation (trust relationship is gone)
#:   misconfigured -> edit one setting (trust works, this path cannot act)
ConnectionState = Literal["unverified", "valid", "revoked", "misconfigured"]

#: WHICH LAYER the last probe failed at. `connection_state` says what the
#: customer must do; this says where it broke. Keeping them separate is what
#: makes "trust broken" and "trust fine, IAM binding insufficient" distinguishable
#: in a query - the single most important operational fact about a federated
#: connection, and the reason the probe must reach Compute rather than stopping
#: at the token exchange.
ProbeResult = Literal[
    "ok",
    "sts_failed",            # trust layer: pool/provider gone, or the assertion was rejected
    "compute_denied",        # authorization layer: STS fine, IAM binding insufficient
    "compute_unavailable",   # Compute reachable but transient (5xx/timeout) - NOT a verdict
    "unreachable",           # neither layer reached: network/DNS - NOT a verdict
    # The trust layer answered but the authorization layer could not be reached
    # because the connection names no enabled target to read. Its own value
    # rather than a success: a connection verified only as far as STS is exactly
    # the false-healthy report this probe exists to prevent.
    "no_targets",
]

AccessMode = Literal["direct", "impersonation"]
JwksDelivery = Literal["served", "uploaded"]


@dataclass(frozen=True, slots=True)
class GcpWifConnectionRow:
    conn_id: UUID
    org_id: str
    label: str                        # '' = the default connection for this org
    #: Public, unguessable path segment of this org's OIDC issuer URL. NOT a
    #: secret (see migration 0024) - it defeats enumeration, it does not
    #: authenticate.
    issuer_slug: str
    gcp_project_id: str               # human-facing id, used in Compute API URLs
    gcp_project_number: str           # numeric, used in the audience + principal
    workload_identity_pool_id: str
    workload_identity_provider_id: str
    #: Stored VERBATIM rather than reassembled from the four fields above. The
    #: customer may configure a custom allowed audience, and a derived value
    #: would diverge silently from such a customer - surfacing as an STS
    #: rejection at the moment the connection is relied upon.
    audience: str
    access_mode: AccessMode
    #: NULL iff access_mode == 'direct'. The migration's CHECK enforces the
    #: biconditional, so a half-configured impersonation trust cannot be stored.
    service_account_email: str | None
    #: Which signing key id this connection's tokens are minted with. A rotation
    #: anchor only; rotation is not implemented.
    signing_key_id: str
    #: 'served' = Google fetches Skylize's JWKS. 'uploaded' = the customer pasted
    #: the key set in with --jwk-json-path, which makes any future Skylize
    #: rotation a CUSTOMER action.
    jwks_delivery: JwksDelivery
    expected_jwks_key_id: str | None
    connection_state: ConnectionState
    state_reason: str | None
    last_probe_at: datetime | None
    last_probe_result: ProbeResult | None
    last_success_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GcpWifTargetRow:
    target_id: UUID
    conn_id: UUID
    org_id: str
    gcp_project_id: str
    zone: str
    instance_name: str
    enabled: bool
    created_at: datetime


class GcpWifRepository(Protocol):
    async def insert(self, row: GcpWifConnectionRow) -> None: ...

    async def get(self, org_id: str, label: str) -> GcpWifConnectionRow | None: ...

    async def list_for_org(self, org_id: str) -> list[GcpWifConnectionRow]: ...

    async def set_connection_state(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool: ...

    async def record_probe(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        result: ProbeResult,
        state: ConnectionState,
        reason: str | None,
        probed_at: datetime,
    ) -> bool: ...

    async def add_target(self, row: GcpWifTargetRow) -> None: ...

    async def list_targets(
        self, org_id: str, conn_id: UUID, *, enabled_only: bool = True
    ) -> list[GcpWifTargetRow]: ...


# ---------------------------------------------------------------------------
# asyncpg implementation
# ---------------------------------------------------------------------------

_CONN_COLUMNS = (
    "conn_id, org_id, label, issuer_slug, gcp_project_id, gcp_project_number, "
    "workload_identity_pool_id, workload_identity_provider_id, audience, "
    "access_mode, service_account_email, signing_key_id, jwks_delivery, "
    "expected_jwks_key_id, connection_state, state_reason, last_probe_at, "
    "last_probe_result, last_success_at, created_at, updated_at"
)

_TARGET_COLUMNS = (
    "target_id, conn_id, org_id, gcp_project_id, zone, instance_name, "
    "enabled, created_at"
)


def _conn_row(rec: Any) -> GcpWifConnectionRow:
    return GcpWifConnectionRow(
        conn_id=rec["conn_id"],
        org_id=rec["org_id"],
        label=rec["label"],
        issuer_slug=rec["issuer_slug"],
        gcp_project_id=rec["gcp_project_id"],
        gcp_project_number=rec["gcp_project_number"],
        workload_identity_pool_id=rec["workload_identity_pool_id"],
        workload_identity_provider_id=rec["workload_identity_provider_id"],
        audience=rec["audience"],
        access_mode=rec["access_mode"],
        service_account_email=rec["service_account_email"],
        signing_key_id=rec["signing_key_id"],
        jwks_delivery=rec["jwks_delivery"],
        expected_jwks_key_id=rec["expected_jwks_key_id"],
        connection_state=rec["connection_state"],
        state_reason=rec["state_reason"],
        last_probe_at=rec["last_probe_at"],
        last_probe_result=rec["last_probe_result"],
        last_success_at=rec["last_success_at"],
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
    )


def _target_row(rec: Any) -> GcpWifTargetRow:
    return GcpWifTargetRow(
        target_id=rec["target_id"],
        conn_id=rec["conn_id"],
        org_id=rec["org_id"],
        gcp_project_id=rec["gcp_project_id"],
        zone=rec["zone"],
        instance_name=rec["instance_name"],
        enabled=rec["enabled"],
        created_at=rec["created_at"],
    )


class PgGcpWifRepository:
    """Postgres-backed WIF trust store. Every statement runs in a tenant session."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def tenant_session(self, org_id: str) -> Any:
        return self._db.tenant_session(org_id)

    async def insert(self, row: GcpWifConnectionRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"""
                INSERT INTO gcp_wif_connections ({_CONN_COLUMNS})
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                        $17,$18,$19,$20,$21)
                """,
                row.conn_id, row.org_id, row.label, row.issuer_slug,
                row.gcp_project_id, row.gcp_project_number,
                row.workload_identity_pool_id, row.workload_identity_provider_id,
                row.audience, row.access_mode, row.service_account_email,
                row.signing_key_id, row.jwks_delivery, row.expected_jwks_key_id,
                row.connection_state, row.state_reason, row.last_probe_at,
                row.last_probe_result, row.last_success_at,
                row.created_at, row.updated_at,
            )

    async def get(self, org_id: str, label: str) -> GcpWifConnectionRow | None:
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                f"SELECT {_CONN_COLUMNS} FROM gcp_wif_connections "
                "WHERE org_id=$1 AND label=$2",
                org_id, label,
            )
            return None if r is None else _conn_row(r)

    async def list_for_org(self, org_id: str) -> list[GcpWifConnectionRow]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_CONN_COLUMNS} FROM gcp_wif_connections "
                "WHERE org_id=$1 ORDER BY label",
                org_id,
            )
            return [_conn_row(r) for r in rows]

    async def set_connection_state(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool:
        """Mark durable trust state. The row is NEVER deleted: deletion would
        destroy the evidence that a federation existed and was lost."""
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE gcp_wif_connections "
                "SET connection_state=$3, state_reason=$4, updated_at=now() "
                "WHERE conn_id=$1 AND org_id=$2",
                conn_id, org_id, state, reason,
            )
            return bool(tag != "UPDATE 0")

    async def record_probe(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        result: ProbeResult,
        state: ConnectionState,
        reason: str | None,
        probed_at: datetime,
    ) -> bool:
        """Persist one probe outcome: the layer that answered, the resulting
        state, and the timestamps.

        `last_success_at` advances ONLY on 'ok'. It is the freshness signal a
        staleness alert reads, so a probe that failed must never refresh it -
        that would make a broken connection look recently healthy.
        """
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                """
                UPDATE gcp_wif_connections
                   SET last_probe_at=$3,
                       last_probe_result=$4,
                       connection_state=$5,
                       state_reason=$6,
                       last_success_at=CASE WHEN $4='ok' THEN $3 ELSE last_success_at END,
                       updated_at=now()
                 WHERE conn_id=$1 AND org_id=$2
                """,
                conn_id, org_id, probed_at, result, state, reason,
            )
            return bool(tag != "UPDATE 0")

    async def add_target(self, row: GcpWifTargetRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"""
                INSERT INTO gcp_wif_targets ({_TARGET_COLUMNS})
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                row.target_id, row.conn_id, row.org_id, row.gcp_project_id,
                row.zone, row.instance_name, row.enabled, row.created_at,
            )

    async def list_targets(
        self, org_id: str, conn_id: UUID, *, enabled_only: bool = True
    ) -> list[GcpWifTargetRow]:
        sql = (
            f"SELECT {_TARGET_COLUMNS} FROM gcp_wif_targets "
            "WHERE org_id=$1 AND conn_id=$2"
        )
        if enabled_only:
            sql += " AND enabled"
        sql += " ORDER BY gcp_project_id, zone, instance_name"
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(sql, org_id, conn_id)
            return [_target_row(r) for r in rows]


# ---------------------------------------------------------------------------
# In-memory implementation (tests / memory backend)
# ---------------------------------------------------------------------------

class InMemoryGcpWifRepository:
    """Test/memory-backend twin. Tenancy is enforced by the same explicit
    ``org_id`` predicate the Pg implementation carries, so a test that leaked
    across orgs here would leak there too."""

    def __init__(self) -> None:
        self._conns: dict[UUID, GcpWifConnectionRow] = {}
        self._targets: dict[UUID, GcpWifTargetRow] = {}

    def tenant_session(self, org_id: str) -> Any:  # noqa: ARG002 - parity only
        return _NullTxn()

    async def insert(self, row: GcpWifConnectionRow) -> None:
        self._conns[row.conn_id] = row

    async def get(self, org_id: str, label: str) -> GcpWifConnectionRow | None:
        for row in self._conns.values():
            if row.org_id == org_id and row.label == label:
                return row
        return None

    async def list_for_org(self, org_id: str) -> list[GcpWifConnectionRow]:
        return sorted(
            (r for r in self._conns.values() if r.org_id == org_id),
            key=lambda r: r.label,
        )

    async def set_connection_state(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool:
        row = self._conns.get(conn_id)
        if row is None or row.org_id != org_id:
            return False
        self._conns[conn_id] = replace(row, connection_state=state, state_reason=reason)
        return True

    async def record_probe(
        self,
        *,
        conn_id: UUID,
        org_id: str,
        result: ProbeResult,
        state: ConnectionState,
        reason: str | None,
        probed_at: datetime,
    ) -> bool:
        row = self._conns.get(conn_id)
        if row is None or row.org_id != org_id:
            return False
        self._conns[conn_id] = replace(
            row,
            last_probe_at=probed_at,
            last_probe_result=result,
            connection_state=state,
            state_reason=reason,
            last_success_at=probed_at if result == "ok" else row.last_success_at,
        )
        return True

    async def add_target(self, row: GcpWifTargetRow) -> None:
        self._targets[row.target_id] = row

    async def list_targets(
        self, org_id: str, conn_id: UUID, *, enabled_only: bool = True
    ) -> list[GcpWifTargetRow]:
        return sorted(
            (
                t
                for t in self._targets.values()
                if t.org_id == org_id
                and t.conn_id == conn_id
                and (t.enabled or not enabled_only)
            ),
            key=lambda t: (t.gcp_project_id, t.zone, t.instance_name),
        )


class _NullTxn:
    """Stands in for the asyncpg transaction scope on the memory backend."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False
