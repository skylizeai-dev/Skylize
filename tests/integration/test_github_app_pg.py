"""github_app_installations / github_app_repos — REAL Postgres, as the app role.

Covers the guarantees only a database can prove (migration 0026):
  * migration shape: FORCE RLS and a `tenant_isolation` policy on BOTH tables,
    and every CHECK constraint;
  * **RLS cross-tenant isolation** — one org cannot read, update, or delete
    another org's installation or its governed repo list, proven as a role that is
    neither a superuser nor the table owner (either would bypass RLS and make the
    test prove nothing);
  * the GLOBALLY-unique `gh_installation_id`, which is a TENANCY control rather
    than a convenience. This is the most important assertion in the file: without
    it two orgs could each claim installation 12345, and org A's governed action
    would mint a token against org B's repositories. RLS cannot catch that — both
    rows are individually well-formed and each org only ever reads its own — so
    the unique index is the only thing standing in the way;
  * that NO column here holds a secret (there is no encrypted column at all,
    unlike oauth_credentials), which is the schema-level expression of §2.4
    Q2.4d's third-credential-shape decision;
  * probe-state persistence, including that a transient probe result does NOT
    overwrite a good connection_state.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.github_app import (
    GithubInstallationRow,
    GithubRepoRow,
    PgGithubAppRepository,
)

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

# See test_gcp_wif_pg.py's note on fixture choice: `pg_schema` for catalogue
# metadata scoped to a throwaway schema, `migrated_public` for anything writing
# through `admin_conn` or `app_db`, both of which use the default search_path.


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"gh_a_{s}", f"gh_b_{s}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gh_id() -> int:
    # Wide random space so parallel runs of this file do not collide on the
    # globally-unique index and produce a spurious failure.
    return int(uuid.uuid4().int % 1_000_000_000) + 1


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM github_app_repos WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute(
        "DELETE FROM github_app_installations WHERE org_id = ANY($1::text[])", orgs
    )


def _install_row(
    org: str,
    *,
    label: str = "",
    gh_id: int | None = None,
    state: str = "unverified",
) -> GithubInstallationRow:
    now = _now()
    return GithubInstallationRow(
        install_id=uuid.uuid4(),
        org_id=org,
        label=label,
        gh_installation_id=gh_id if gh_id is not None else _gh_id(),
        gh_account_login="acme-inc",
        gh_account_type="Organization",
        repository_selection="selected",
        connection_state=state,  # type: ignore[arg-type]
        state_reason=None,
        last_probe_at=None,
        last_probe_result=None,
        last_success_at=None,
        created_at=now,
        updated_at=now,
    )


def _repo_row(inst: GithubInstallationRow, name: str = "widgets") -> GithubRepoRow:
    return GithubRepoRow(
        repo_id=uuid.uuid4(),
        install_id=inst.install_id,
        org_id=inst.org_id,
        owner="acme-inc",
        name=name,
        protected_branch="main",
        enabled=True,
        created_at=_now(),
    )


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` pool connected as the NON-SUPERUSER app role (RLS subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
@pytest.mark.parametrize(
    "table", ["github_app_installations", "github_app_repos"]
)
async def test_rls_is_enabled_and_forced(admin_conn, pg_schema: str, table: str) -> None:
    """FORCE matters: without it the table owner silently bypasses the policy."""
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=$2 AND n.nspname=$1",
        pg_schema, table,
    )
    assert rel is not None, f"{table} missing from {pg_schema}"
    assert rel["relrowsecurity"] is True, f"{table}: RLS not enabled"
    assert rel["relforcerowsecurity"] is True, f"{table}: RLS not FORCED"


@requires_pg
@pytest.mark.parametrize(
    "table", ["github_app_installations", "github_app_repos"]
)
async def test_tenant_isolation_policy_exists(
    admin_conn, pg_schema: str, table: str
) -> None:
    row = await admin_conn.fetchrow(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=$1 AND c.relname=$2 AND p.polname='tenant_isolation'",
        pg_schema, table,
    )
    assert row is not None, f"{table} has no tenant_isolation policy"


@requires_pg
async def test_no_encrypted_column_exists(admin_conn, pg_schema: str) -> None:
    """§2.4 Q2.4d: this table holds NO secret, by design.

    The App private key is platform-level; installation tokens are minted per call
    and never persisted. An encrypted column appearing here would mean someone had
    started storing a credential per tenant, reversing the third-credential-shape
    decision — so its absence is asserted rather than assumed.
    """
    cols = await admin_conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=$1 AND table_name='github_app_installations'",
        pg_schema,
    )
    names = {c["column_name"] for c in cols}
    leaked = {n for n in names if "encrypt" in n or "secret" in n or n == "key_id"}
    assert not leaked, (
        f"github_app_installations grew {leaked}. Migration 0026 deliberately has "
        "no encrypted column and no key_id: the App private key is PLATFORM-level "
        "and installation tokens are never stored. Adding one reverses "
        "integration_inputs.md 2.4 Q2.4d."
    )


@requires_pg
@pytest.mark.parametrize(
    "state", ["unverified", "protected", "unprotected", "bypass_granted", "revoked"]
)
async def test_all_five_approved_states_are_accepted(
    admin_conn, migrated_public: None, state: str
) -> None:
    org = f"gh_st_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        await admin_conn.execute(
            "INSERT INTO github_app_installations "
            "(org_id, gh_installation_id, gh_account_login, gh_account_type, "
            " connection_state) VALUES ($1,$2,'acme','Organization',$3)",
            org, _gh_id(), state,
        )
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_unknown_connection_state_is_rejected(
    admin_conn, migrated_public: None
) -> None:
    org = f"gh_bad_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        with pytest.raises(Exception, match="state_check|violates check"):
            await admin_conn.execute(
                "INSERT INTO github_app_installations "
                "(org_id, gh_installation_id, gh_account_login, gh_account_type, "
                " connection_state) VALUES ($1,$2,'acme','Organization','valid')",
                org, _gh_id(),
            )
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_nonpositive_installation_id_is_rejected(
    admin_conn, migrated_public: None
) -> None:
    org = f"gh_zero_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        with pytest.raises(Exception, match="gh_id_positive|violates check"):
            await admin_conn.execute(
                "INSERT INTO github_app_installations "
                "(org_id, gh_installation_id, gh_account_login, gh_account_type) "
                "VALUES ($1,0,'acme','Organization')",
                org,
            )
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_unknown_probe_result_is_rejected(
    admin_conn, migrated_public: None
) -> None:
    org = f"gh_pr_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        with pytest.raises(Exception, match="probe_result_check|violates check"):
            await admin_conn.execute(
                "INSERT INTO github_app_installations "
                "(org_id, gh_installation_id, gh_account_login, gh_account_type, "
                " last_probe_result) VALUES ($1,$2,'acme','Organization','sts_failed')",
                org, _gh_id(),
            )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# The globally-unique installation id — a TENANCY control
# ---------------------------------------------------------------------------

@requires_pg
async def test_two_orgs_cannot_claim_the_same_installation(
    admin_conn, migrated_public: None
) -> None:
    """THE most important constraint in migration 0026.

    One GitHub installation belongs to exactly one customer account. If two orgs
    could each claim installation N, org A's governed tool call would mint a token
    against org B's repositories. RLS cannot prevent this: both rows are
    individually valid and each org only ever reads its own. The globally-unique
    index is the entire defence.
    """
    org_a, org_b = _orgs()
    shared = _gh_id()
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    try:
        await admin_conn.execute(
            "INSERT INTO github_app_installations "
            "(org_id, gh_installation_id, gh_account_login, gh_account_type) "
            "VALUES ($1,$2,'acme','Organization')",
            org_a, shared,
        )
        with pytest.raises(Exception, match="gh_installation|unique|duplicate"):
            await admin_conn.execute(
                "INSERT INTO github_app_installations "
                "(org_id, gh_installation_id, gh_account_login, gh_account_type) "
                "VALUES ($1,$2,'other','Organization')",
                org_b, shared,
            )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_pg
async def test_one_installation_per_org_label(
    admin_conn, migrated_public: None
) -> None:
    org = f"gh_lbl_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        await admin_conn.execute(
            "INSERT INTO github_app_installations "
            "(org_id, label, gh_installation_id, gh_account_login, gh_account_type) "
            "VALUES ($1,'',$2,'acme','Organization')",
            org, _gh_id(),
        )
        with pytest.raises(Exception, match="unique|duplicate"):
            await admin_conn.execute(
                "INSERT INTO github_app_installations "
                "(org_id, label, gh_installation_id, gh_account_login, "
                " gh_account_type) VALUES ($1,'',$2,'acme','Organization')",
                org, _gh_id(),
            )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS cross-tenant isolation, proven as the app role — THE HARD GATE
# ---------------------------------------------------------------------------

@requires_app_role
async def test_org_cannot_read_another_orgs_installation(
    admin_conn, app_db, migrated_public: None
) -> None:
    org_a, org_b = _orgs()
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    repo = PgGithubAppRepository(app_db)
    try:
        row_a = _install_row(org_a)
        await repo.insert(row_a)

        # org_a sees its own row...
        assert await repo.get(org_a, "") is not None
        # ...and org_b sees nothing, through the same pool and the same code path.
        assert await repo.get(org_b, "") is None
        assert await repo.list_for_org(org_b) == []
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_org_cannot_update_another_orgs_installation(
    admin_conn, app_db, migrated_public: None
) -> None:
    """A cross-tenant UPDATE must affect zero rows, not silently succeed."""
    org_a, org_b = _orgs()
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    repo = PgGithubAppRepository(app_db)
    try:
        row_a = _install_row(org_a, state="protected")
        await repo.insert(row_a)

        changed = await repo.set_connection_state(
            install_id=row_a.install_id,
            org_id=org_b,               # <-- wrong tenant
            state="revoked",
            reason="attempted cross-tenant write",
        )
        assert changed is False, "cross-tenant UPDATE was permitted"

        still = await repo.get(org_a, "")
        assert still is not None
        assert still.connection_state == "protected"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_org_cannot_see_another_orgs_governed_repos(
    admin_conn, app_db, migrated_public: None
) -> None:
    """The child table carries its own org_id precisely so its policy stands alone."""
    org_a, org_b = _orgs()
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    repo = PgGithubAppRepository(app_db)
    try:
        inst = _install_row(org_a)
        await repo.insert(inst)
        await repo.add_repo(_repo_row(inst))

        assert len(await repo.list_repos(org_a, inst.install_id)) == 1
        assert await repo.list_repos(org_b, inst.install_id) == []
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_insert_for_another_org_is_refused_by_with_check(
    admin_conn, app_db, migrated_public: None
) -> None:
    """The policy's WITH CHECK must stop writing a row into another tenant."""
    org_a, org_b = _orgs()
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    try:
        async with app_db.tenant_session(org_a) as conn:
            with pytest.raises(Exception, match="policy|violates"):
                await conn.execute(
                    "INSERT INTO github_app_installations "
                    "(org_id, gh_installation_id, gh_account_login, "
                    " gh_account_type) VALUES ($1,$2,'acme','Organization')",
                    org_b, _gh_id(),
                )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# DAL round-trip and probe-state persistence
# ---------------------------------------------------------------------------

@requires_app_role
async def test_roundtrip_through_real_columns(
    admin_conn, app_db, migrated_public: None
) -> None:
    org, _ = _orgs()
    await _seed_tenant(admin_conn, org)
    repo = PgGithubAppRepository(app_db)
    try:
        inst = _install_row(org)
        await repo.insert(inst)
        got = await repo.get(org, "")
        assert got is not None
        assert got.gh_installation_id == inst.gh_installation_id
        assert got.gh_account_type == "Organization"
        assert got.repository_selection == "selected"
        assert got.connection_state == "unverified"

        await repo.add_repo(_repo_row(inst, "widgets"))
        await repo.add_repo(_repo_row(inst, "gadgets"))
        repos = await repo.list_repos(org, inst.install_id)
        assert [r.name for r in repos] == ["gadgets", "widgets"]
        assert repos[0].protected_branch == "main"
        assert repos[0].full_name == "acme-inc/gadgets"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_probe_records_state_and_advances_success_only_on_ok(
    admin_conn, app_db, migrated_public: None
) -> None:
    org, _ = _orgs()
    await _seed_tenant(admin_conn, org)
    repo = PgGithubAppRepository(app_db)
    try:
        inst = _install_row(org)
        await repo.insert(inst)
        t1 = _now()
        await repo.record_probe(
            install_id=inst.install_id, org_id=org, result="ok",
            state="protected", reason=None, probed_at=t1,
        )
        got = await repo.get(org, "")
        assert got is not None
        assert got.connection_state == "protected"
        assert got.last_probe_result == "ok"
        assert got.last_success_at is not None

        # A non-ok terminal result records the state but must NOT advance
        # last_success_at, which is the "when did this last actually work" field.
        t2 = _now()
        await repo.record_probe(
            install_id=inst.install_id, org_id=org, result="no_ruleset",
            state="unprotected", reason="nothing restricts pushes", probed_at=t2,
        )
        got2 = await repo.get(org, "")
        assert got2 is not None
        assert got2.connection_state == "unprotected"
        assert got2.last_success_at == got.last_success_at
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_transient_probe_does_not_overwrite_a_good_state(
    admin_conn, app_db, migrated_public: None
) -> None:
    """"We could not check" must never collapse into "there was nothing to find".

    `state=None` is the transient path. If this ever starts writing a state, a
    network blip would downgrade a genuinely protected repository and the operator
    would chase a protection regression that never happened.
    """
    org, _ = _orgs()
    await _seed_tenant(admin_conn, org)
    repo = PgGithubAppRepository(app_db)
    try:
        inst = _install_row(org)
        await repo.insert(inst)
        await repo.record_probe(
            install_id=inst.install_id, org_id=org, result="ok",
            state="protected", reason=None, probed_at=_now(),
        )
        await repo.record_probe(
            install_id=inst.install_id, org_id=org, result="unreachable",
            state=None, reason="GitHub 503", probed_at=_now(),
        )
        got = await repo.get(org, "")
        assert got is not None
        assert got.connection_state == "protected", (
            "a transient probe failure overwrote a good protection state"
        )
        assert got.last_probe_result == "unreachable"
        assert got.state_reason == "GitHub 503"
    finally:
        await _cleanup(admin_conn, [org])
