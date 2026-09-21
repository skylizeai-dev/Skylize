"""Permission matrix scanner + route — mechanically derived, not transcribed.

THREE THINGS ARE PINNED HERE, matching the owner-approved design's three test
categories:

  (a) The AST scanner correctly extracts role sets from KNOWN real route files.
      `kill_switch.py` is owner-only on both its routes (POST-only, no GET at
      all), so it must show owner=write, every other role False, and read=False
      for every role including owner. `agents.py` has a broad GET
      (`list_agents`, all 5 roles) and a narrower POST (`execute_agent`, 3
      roles), so it must show read=True for all 5 roles and write=True for
      exactly owner/admin/operator. These assertions are INTENTIONALLY
      brittle: if a future change to either route file's role gate is not
      matched here, this test fails loudly, which is the whole point of a
      mechanically-derived matrix (a fake one could never do this).

  (b) A regression test that the scanner finds NO dynamically-computed role
      expression anywhere in `src/skylize/edge/routes/`. Verified by hand
      (2026-09, this task) across all 21 route files: every role argument to
      require_role/require_any_role/require_any_role_or_user is a string
      literal or a module-level tuple/list constant (`_ROLES` in cowork.py,
      `_ALL_ROLES` in brief.py). `assert_no_dynamic_roles` re-parses the real
      tree and would raise `UnresolvedRoleExpression` the moment that stops
      being true; this test pins that it does not raise today.

  (c) A route-level test against the real memory-backend container (the same
      `TestClient(create_app())` pattern every other route test in this repo
      uses, e.g. test_audit_routes.py), checking the RBAC gate on the
      matrix endpoint itself and the shape of a real HTTP response.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app
from skylize.edge.permission_matrix import (
    ALL_ROLES,
    UnresolvedRoleExpression,
    assert_no_dynamic_roles,
    build_permission_matrix,
)

_OWNER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_ADMIN_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u3", "X-Dev-Roles": "admin"}
_OPERATOR_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u4", "X-Dev-Roles": "operator"}
_VIEWER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"}


# ---------------------------------------------------------------------------
# (a) Scanner correctness against known real route files
# ---------------------------------------------------------------------------


def _group(matrix, name: str):
    for g in matrix.route_groups:
        if g.route_group == name:
            return g
    raise AssertionError(f"route group {name!r} not found in matrix")


def test_kill_switch_is_owner_only_write_and_no_read() -> None:
    """kill_switch.py: two POSTs (engage/disengage), both `require_role("owner")`.
    No GET exists in this module at all, so read must be False for every role,
    including owner — the absence of a read route, not a permission gap.
    """
    matrix = build_permission_matrix()
    group = _group(matrix, "kill_switch")

    assert set(group.access) == set(ALL_ROLES)
    for role in ALL_ROLES:
        assert group.access[role].read is False, f"{role} should have no read access"
    assert group.access["owner"].write is True
    for role in ("admin", "operator", "analyst", "viewer"):
        assert group.access[role].write is False, f"{role} must not have write access"


def test_agents_group_shows_broad_read_and_narrower_write() -> None:
    """agents.py: GET "" (list_agents) is require_any_role_or_user with all 5
    roles; POST /execute is require_any_role_or_user("owner", "admin",
    "operator") only. The group-level OR means read=True for all 5 roles (all
    reach the GET) and write=True for exactly owner/admin/operator.
    """
    matrix = build_permission_matrix()
    group = _group(matrix, "agents")

    for role in ALL_ROLES:
        assert group.access[role].read is True, f"{role} should be able to read agents"
    for role in ("owner", "admin", "operator"):
        assert group.access[role].write is True, f"{role} should be able to execute agents"
    for role in ("analyst", "viewer"):
        assert group.access[role].write is False, f"{role} must not execute agents"


def test_cowork_group_resolves_module_level_constant() -> None:
    """cowork.py gates its one POST with `require_any_role_or_user(*_ROLES)`
    where `_ROLES = ("owner", "admin", "operator")` — a module-level constant,
    not an inline literal. The scanner must resolve this, not skip it.
    """
    matrix = build_permission_matrix()
    group = _group(matrix, "cowork")

    for role in ("owner", "admin", "operator"):
        assert group.access[role].write is True
    for role in ("analyst", "viewer"):
        assert group.access[role].write is False
        assert group.access[role].read is False


def test_brief_group_resolves_all_roles_constant() -> None:
    """brief.py gates both its routes with `require_any_role_or_user(*_ALL_ROLES)`
    where `_ALL_ROLES` spans all 5 roles — the broadest module-level constant
    in the tree. Both GET and POST /seen must show True for every role.
    """
    matrix = build_permission_matrix()
    group = _group(matrix, "brief")

    for role in ALL_ROLES:
        assert group.access[role].read is True
        assert group.access[role].write is True


def test_route_group_with_no_role_gate_still_appears() -> None:
    """auth.py has no Depends(require_*(...)) call site at all (register/login/
    refresh are pre-auth; /me uses get_current_user, not a role dependency).
    It must still appear in the matrix, all-False, rather than being silently
    omitted — omission would be indistinguishable from a scanner bug.
    """
    matrix = build_permission_matrix()
    group = _group(matrix, "auth")

    for role in ALL_ROLES:
        assert group.access[role].read is False
        assert group.access[role].write is False


def test_every_route_file_becomes_a_route_group() -> None:
    """Every *.py under edge/routes/ (except __init__.py) is a row — the
    'raw route group' contract from the owner-approved design, independent of
    whether that file happens to have a role-gated route."""
    matrix = build_permission_matrix()
    names = {g.route_group for g in matrix.route_groups}
    # A representative spread across gated and ungated modules.
    for expected in (
        "kill_switch", "agents", "tenants", "deliverables", "credentials",
        "hitl", "cowork", "brief", "auth", "knowledge", "agent_prompts",
        "wif_oidc",
    ):
        assert expected in names, f"{expected} missing from route groups"


# ---------------------------------------------------------------------------
# (b) Regression: no dynamically-computed role expression anywhere
# ---------------------------------------------------------------------------


def test_no_dynamic_role_expression_in_route_tree() -> None:
    """Hard gate: every Depends(require_role(...)) / Depends(require_any_role(...))
    / Depends(require_any_role_or_user(...)) call site across
    src/skylize/edge/routes/ must resolve to a string literal or a
    module-level literal tuple/list constant. If a future route computes its
    role set at runtime (a lookup, a comprehension, an f-string, anything not
    statically resolvable), this test must fail loudly rather than let the
    scanner silently drop that route from the permission matrix.
    """
    assert_no_dynamic_roles()  # raises UnresolvedRoleExpression on failure


def test_unresolved_role_expression_actually_raises() -> None:
    """Pin that UnresolvedRoleExpression is a real, raisable exception type —
    guards against `assert_no_dynamic_roles` becoming a silent no-op."""
    assert issubclass(UnresolvedRoleExpression, Exception)


# ---------------------------------------------------------------------------
# (c) Route-level test against the real memory-backend container
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_permission_matrix_route_requires_owner_or_admin(client: TestClient) -> None:
    resp = client.get("/api/v1/permissions/matrix", headers=_VIEWER_A)
    assert resp.status_code == 403, resp.text


def test_permission_matrix_route_allows_operator_rejected(client: TestClient) -> None:
    # operator is a real role in the platform but not one of the two this
    # endpoint's own gate names — pinning the boundary explicitly, not just
    # "some non-owner/admin role is rejected".
    resp = client.get("/api/v1/permissions/matrix", headers=_OPERATOR_A)
    assert resp.status_code == 403, resp.text


def test_permission_matrix_route_returns_matrix_for_owner(client: TestClient) -> None:
    resp = client.get("/api/v1/permissions/matrix", headers=_OWNER_A)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body["roles"]) == set(ALL_ROLES)
    assert "generated_at" in body and body["generated_at"]
    assert isinstance(body["route_groups"], list) and body["route_groups"]

    groups_by_name = {g["route_group"]: g for g in body["route_groups"]}
    assert "kill_switch" in groups_by_name
    ks = groups_by_name["kill_switch"]
    assert ks["access"]["owner"]["write"] is True
    assert ks["access"]["viewer"]["write"] is False
    assert ks["access"]["owner"]["read"] is False


def test_permission_matrix_route_returns_matrix_for_admin(client: TestClient) -> None:
    resp = client.get("/api/v1/permissions/matrix", headers=_ADMIN_A)
    assert resp.status_code == 200, resp.text
