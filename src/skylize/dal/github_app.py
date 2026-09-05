"""GitHub App installation store (migration 0026). Non-secret per-tenant state.

Design: docs/06_integrations/integration_inputs.md §2.4 Q2.4d, `[APPROVED]`
2026-09-05.

WHAT IS AND IS NOT IN HERE. Nothing in this module handles a secret. The App
private key is platform-level and lives in `app/github/keys.py` behind `Settings`;
installation access tokens are minted per call in `app/github/tokens.py` and never
persisted. What this store holds is the answer to two non-secret questions:

    1. Which GitHub installation is this org's? (`gh_installation_id`)
    2. Is that installation's branch protection actually protecting them?
       (`connection_state`, `last_probe_result`)

That is why there is no encryptor here, no `key_id`, and no parallel to
`app/credentials/oauth.py`'s refresh machinery — see migration 0026's docstring
for the full argument against reusing `oauth_credentials`.

EVERY STATEMENT RUNS IN A TENANT SESSION. Same discipline as
`dal/gcp_wif.py`: RLS on both tables is ENABLE + FORCE, so the non-superuser
`skylize_app` role is a genuine policy subject and a query outside
`tenant_session(org_id)` returns nothing rather than everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

#: The five owner-approved states (§2.4 Q2.4c, migration 0026). Only 'protected'
#: means the §2.4 Tier 2 guarantee actually holds. 'bypass_granted' is STRICTLY
#: WORSE than 'unprotected' and the two must never be collapsed: an unprotected
#: customer knows they have a gap, a bypass_granted customer believes they are
#: protected and is not.
ConnectionState = Literal[
    "unverified",
    "protected",
    "unprotected",
    "bypass_granted",
    "revoked",
]

#: WHICH LAYER the probe answered at — information `connection_state` cannot carry.
#: Mirrors `dal/gcp_wif.py`'s `ProbeResult` and migration 0024's reasoning for
#: making it a column rather than a log line.
#:
#: 'ok'                  - an active ruleset covers the branch and this App cannot
#:                         bypass it.
#: 'no_ruleset'          - read succeeded; nothing restricts pushes to the branch.
#: 'bypass_granted'      - a covering ruleset exists but this App can bypass it.
#: 'classic_only'        - CLASSIC branch protection detected, but its bypass
#:                         configuration is unreadable under the Tier 1 permission
#:                         set (needs administration:read, which Tier 1 forbids).
#: 'auth_failed'         - the App assertion was rejected. A PLATFORM problem
#:                         (key/app id/clock), never the tenant's fault.
#: 'installation_gone'   - 404/410: the customer uninstalled. Terminal.
#: 'rulesets_unreadable' - the repo or its rulesets could not be read.
#: 'unreachable'         - network fault, timeout, 429, 5xx. Transient.
#: 'no_repos'            - the installation has no enabled repo rows to probe.
GithubProbeResult = Literal[
    "ok",
    "no_ruleset",
    "bypass_granted",
    "classic_only",
    "auth_failed",
    "installation_gone",
    "rulesets_unreadable",
    "unreachable",
    "no_repos",
]

AccountType = Literal["Organization", "User"]
RepositorySelection = Literal["all", "selected"]


@dataclass(frozen=True, slots=True)
class GithubInstallationRow:
    install_id: UUID
    org_id: str
    label: str                    # '' = the default installation for this org
    #: GitHub's numeric installation id. NOT a secret: possession grants nothing
    #: without the platform App private key. GLOBALLY unique in the schema — see
    #: migration 0026 for why that index is a tenancy control.
    gh_installation_id: int
    gh_account_login: str
    gh_account_type: AccountType
    repository_selection: RepositorySelection
    connection_state: ConnectionState
    state_reason: str | None
    last_probe_at: datetime | None
    last_probe_result: GithubProbeResult | None
    last_success_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GithubRepoRow:
    repo_id: UUID
    install_id: UUID
    org_id: str
    owner: str
    name: str
    #: The branch whose protection is probed and whose integrity the Tier 2
    #: guarantee concerns. A real column, not a constant — see migration 0026.
    protected_branch: str
    enabled: bool
    created_at: datetime

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GithubAppRepository(Protocol):
    async def insert(self, row: GithubInstallationRow) -> None: ...

    async def get(self, org_id: str, label: str) -> GithubInstallationRow | None: ...

    async def list_for_org(self, org_id: str) -> list[GithubInstallationRow]: ...

    async def set_connection_state(
        self,
        *,
        install_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool: ...

    async def record_probe(
        self,
        *,
        install_id: UUID,
        org_id: str,
        result: GithubProbeResult,
        state: ConnectionState | None,
        reason: str | None,
        probed_at: datetime,
    ) -> bool: ...

    async def add_repo(self, row: GithubRepoRow) -> None: ...

    async def list_repos(
        self, org_id: str, install_id: UUID, *, enabled_only: bool = True
    ) -> list[GithubRepoRow]: ...


_INSTALL_COLUMNS = (
    "install_id, org_id, label, gh_installation_id, gh_account_login, "
    "gh_account_type, repository_selection, connection_state, state_reason, "
    "last_probe_at, last_probe_result, last_success_at, created_at, updated_at"
)

_REPO_COLUMNS = (
    "repo_id, install_id, org_id, owner, name, protected_branch, enabled, "
    "created_at"
)


def _install_row(rec: Any) -> GithubInstallationRow:
    return GithubInstallationRow(
        install_id=rec["install_id"],
        org_id=rec["org_id"],
        label=rec["label"],
        gh_installation_id=rec["gh_installation_id"],
        gh_account_login=rec["gh_account_login"],
        gh_account_type=rec["gh_account_type"],
        repository_selection=rec["repository_selection"],
        connection_state=rec["connection_state"],
        state_reason=rec["state_reason"],
        last_probe_at=rec["last_probe_at"],
        last_probe_result=rec["last_probe_result"],
        last_success_at=rec["last_success_at"],
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
    )


def _repo_row(rec: Any) -> GithubRepoRow:
    return GithubRepoRow(
        repo_id=rec["repo_id"],
        install_id=rec["install_id"],
        org_id=rec["org_id"],
        owner=rec["owner"],
        name=rec["name"],
        protected_branch=rec["protected_branch"],
        enabled=rec["enabled"],
        created_at=rec["created_at"],
    )


class PgGithubAppRepository:
    """Postgres-backed installation store. Every statement in a tenant session."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def tenant_session(self, org_id: str) -> Any:
        return self._db.tenant_session(org_id)

    async def insert(self, row: GithubInstallationRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"""
                INSERT INTO github_app_installations ({_INSTALL_COLUMNS})
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                row.install_id, row.org_id, row.label, row.gh_installation_id,
                row.gh_account_login, row.gh_account_type,
                row.repository_selection, row.connection_state, row.state_reason,
                row.last_probe_at, row.last_probe_result, row.last_success_at,
                row.created_at, row.updated_at,
            )

    async def get(self, org_id: str, label: str) -> GithubInstallationRow | None:
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                f"SELECT {_INSTALL_COLUMNS} FROM github_app_installations "
                "WHERE org_id=$1 AND label=$2",
                org_id, label,
            )
            return None if r is None else _install_row(r)

    async def list_for_org(self, org_id: str) -> list[GithubInstallationRow]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_INSTALL_COLUMNS} FROM github_app_installations "
                "WHERE org_id=$1 ORDER BY label",
                org_id,
            )
            return [_install_row(r) for r in rows]

    async def set_connection_state(
        self,
        *,
        install_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool:
        """Mark durable state. The row is NEVER deleted, even on 'revoked':
        deleting it would destroy the evidence that an installation existed and
        was lost, which is the same reason `dal/gcp_wif.py` keeps its rows."""
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE github_app_installations "
                "SET connection_state=$3, state_reason=$4, updated_at=now() "
                "WHERE install_id=$1 AND org_id=$2",
                install_id, org_id, state, reason,
            )
            return bool(tag != "UPDATE 0")

    async def record_probe(
        self,
        *,
        install_id: UUID,
        org_id: str,
        result: GithubProbeResult,
        state: ConnectionState | None,
        reason: str | None,
        probed_at: datetime,
    ) -> bool:
        """Record a probe attempt.

        `state=None` means "record the attempt, LEAVE THE STATE ALONE" — the
        transient path. This is the whole reason the parameter is nullable rather
        than the caller passing the current state back in: a caller that had to
        read-then-write would race another replica, and a caller that forgot would
        silently overwrite a good state with a guess. `last_success_at` advances
        only on 'ok'.
        """
        async with self._db.tenant_session(org_id) as conn:
            if state is None:
                tag = await conn.execute(
                    "UPDATE github_app_installations "
                    "SET last_probe_at=$3, last_probe_result=$4, "
                    "    state_reason=$5, updated_at=now() "
                    "WHERE install_id=$1 AND org_id=$2",
                    install_id, org_id, probed_at, result, reason,
                )
            else:
                tag = await conn.execute(
                    "UPDATE github_app_installations "
                    "SET connection_state=$3, state_reason=$4, last_probe_at=$5, "
                    "    last_probe_result=$6, updated_at=now(), "
                    "    last_success_at=CASE WHEN $6='ok' THEN $5 "
                    "                         ELSE last_success_at END "
                    "WHERE install_id=$1 AND org_id=$2",
                    install_id, org_id, state, reason, probed_at, result,
                )
            return bool(tag != "UPDATE 0")

    async def add_repo(self, row: GithubRepoRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"""
                INSERT INTO github_app_repos ({_REPO_COLUMNS})
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                row.repo_id, row.install_id, row.org_id, row.owner, row.name,
                row.protected_branch, row.enabled, row.created_at,
            )

    async def list_repos(
        self, org_id: str, install_id: UUID, *, enabled_only: bool = True
    ) -> list[GithubRepoRow]:
        clause = " AND enabled" if enabled_only else ""
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_REPO_COLUMNS} FROM github_app_repos "
                f"WHERE org_id=$1 AND install_id=$2{clause} ORDER BY owner, name",
                org_id, install_id,
            )
            return [_repo_row(r) for r in rows]


class _NullTxn:
    """No-op async context manager so the in-memory repo matches the Pg surface."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class InMemoryGithubAppRepository:
    """Dict-backed parity implementation for unit tests and the memory backend.

    Enforces the SAME two uniqueness rules the migration does — `(org_id, label)`
    and the GLOBAL uniqueness of `gh_installation_id` — because the second one is
    a tenancy control (migration 0026), and a fake that silently permitted two
    orgs to claim one installation would let a test pass that Postgres would
    reject. A parity fake that is laxer than the real thing is worse than no fake.
    """

    def __init__(self) -> None:
        self._installs: dict[tuple[str, str], GithubInstallationRow] = {}
        self._repos: dict[UUID, GithubRepoRow] = {}

    def tenant_session(self, org_id: str) -> Any:  # noqa: ARG002 - parity only
        return _NullTxn()

    async def insert(self, row: GithubInstallationRow) -> None:
        key = (row.org_id, row.label)
        if key in self._installs:
            raise ValueError(
                f"installation already exists for org {row.org_id!r} label "
                f"{row.label!r} (unique index idx_github_app_installations_unique)"
            )
        for existing in self._installs.values():
            if existing.gh_installation_id == row.gh_installation_id:
                raise ValueError(
                    f"gh_installation_id {row.gh_installation_id} is already "
                    f"claimed by org {existing.org_id!r} (unique index "
                    "idx_github_app_installations_gh_installation). One GitHub "
                    "installation belongs to exactly one org; allowing two would "
                    "let one org mint tokens against another's repositories."
                )
        self._installs[key] = row

    async def get(self, org_id: str, label: str) -> GithubInstallationRow | None:
        return self._installs.get((org_id, label))

    async def list_for_org(self, org_id: str) -> list[GithubInstallationRow]:
        return sorted(
            (r for r in self._installs.values() if r.org_id == org_id),
            key=lambda r: r.label,
        )

    def _find(self, install_id: UUID, org_id: str) -> tuple[str, str] | None:
        for key, row in self._installs.items():
            if row.install_id == install_id and row.org_id == org_id:
                return key
        return None

    async def set_connection_state(
        self,
        *,
        install_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
    ) -> bool:
        key = self._find(install_id, org_id)
        if key is None:
            return False
        from dataclasses import replace

        self._installs[key] = replace(
            self._installs[key], connection_state=state, state_reason=reason
        )
        return True

    async def record_probe(
        self,
        *,
        install_id: UUID,
        org_id: str,
        result: GithubProbeResult,
        state: ConnectionState | None,
        reason: str | None,
        probed_at: datetime,
    ) -> bool:
        key = self._find(install_id, org_id)
        if key is None:
            return False
        from dataclasses import replace

        cur = self._installs[key]
        self._installs[key] = replace(
            cur,
            connection_state=cur.connection_state if state is None else state,
            state_reason=reason,
            last_probe_at=probed_at,
            last_probe_result=result,
            last_success_at=probed_at if result == "ok" else cur.last_success_at,
        )
        return True

    async def add_repo(self, row: GithubRepoRow) -> None:
        for existing in self._repos.values():
            if (
                existing.install_id == row.install_id
                and existing.owner == row.owner
                and existing.name == row.name
            ):
                raise ValueError(
                    f"repo {row.full_name} already registered for this "
                    "installation (unique index idx_github_app_repos_unique)"
                )
        self._repos[row.repo_id] = row

    async def list_repos(
        self, org_id: str, install_id: UUID, *, enabled_only: bool = True
    ) -> list[GithubRepoRow]:
        return sorted(
            (
                r
                for r in self._repos.values()
                if r.org_id == org_id
                and r.install_id == install_id
                and (r.enabled or not enabled_only)
            ),
            key=lambda r: (r.owner, r.name),
        )
