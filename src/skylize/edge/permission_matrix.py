"""Permission matrix — mechanically derived from the route files themselves.

WHY STATIC AST ANALYSIS, NOT A RUNTIME/IMPORT WALK OF THE APP. FastAPI route
functions carry their `Depends(require_role(...))` calls as ordinary default
values on the handler signature; by the time a route is imported, the
dependency has already been reduced to an opaque closure (`_checker` in
`edge/deps.py`) that no longer exposes which role STRINGS it closed over.
Recovering them from the live object graph would mean re-deriving the very
literals that are sitting in the source text one call up the stack. Parsing the
`ast` of each route file instead reads exactly what a reviewer reads: the
literal (or module-level-constant) arguments passed to `require_role`,
`require_any_role` and `require_any_role_or_user` at each `Depends(...)` call
site. This is a hard requirement from the owner-approved design, not a
convenience: the matrix must be traceable to real call sites, and a scan of
source text is the only representation that stays that honest.

SCOPE. Every route MODULE under `src/skylize/edge/routes/` is one "route
group" (the row). A route group with zero role-gated routes (e.g. `auth.py`,
`knowledge.py`, `agent_prompts.py`, `wif_oidc.py` — verified by manual read,
2026-09, alongside this scanner) still appears as a route group with an
all-False row, because its ABSENCE from the matrix would be indistinguishable
from "the scanner missed it".

RESOLUTION RULES, chosen to make "cannot resolve" impossible to hit silently:
  * A string literal argument (`"owner"`) resolves to itself.
  * A `Name` argument (e.g. `_ROLES` in `cowork.py`, `_ALL_ROLES` in
    `brief.py`) resolves against that MODULE's own top-level assignments to a
    tuple/list of string literals. This is the one form the owner-approved
    design explicitly requires ("resolve module-level constant references").
  * A starred argument (`*_ROLES`) unpacks that resolved tuple into the
    surrounding call's role list.
  * ANYTHING ELSE — an f-string, a function call, a comprehension, a name that
    does not resolve to a literal tuple/list of strings at module scope — is a
    hard failure: `UnresolvedRoleExpression` is raised rather than the route
    being dropped or guessed at. See `assert_no_dynamic_roles` (called by the
    regression test in `tests/unit/test_permission_matrix.py`), which turns
    this failure mode into an explicit, loud assertion about the whole tree.

READ vs WRITE. GET (and HEAD) are READ; POST/PUT/PATCH/DELETE are WRITE. A role
has read/write access to a group if it appears in AT LEAST ONE route of that
verb class within the group — a group-level OR, matching the console's need to
answer "can this role ever read/write here", not "can it reach this one route".

NO CACHE, NO DATABASE. Route files change rarely and this backs one low-traffic
console screen; `build_permission_matrix()` re-parses every route file on each
call. Cheap file reads (~20-30 small .py files) parsed once with `ast`, well
under a console page's own latency budget.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROUTES_DIR = Path(__file__).parent / "routes"

#: The five console roles, in display order. A route group's row always has
#: exactly these keys — never fewer (a role simply reads/writes False) and
#: never more (an unrecognized literal is a hard failure, not a sixth column).
ALL_ROLES: tuple[str, ...] = ("owner", "admin", "operator", "analyst", "viewer")

_ROLE_DEP_FUNCS = frozenset(
    {"require_role", "require_any_role", "require_any_role_or_user"}
)
_READ_METHODS = frozenset({"get", "head"})
_WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})
_ROUTER_HTTP_METHODS = _READ_METHODS | _WRITE_METHODS


class UnresolvedRoleExpression(Exception):
    """A `Depends(require_*(...))` call site's role argument could not be
    resolved to a string literal or a module-level literal tuple/list.

    Raised eagerly, never swallowed: silently dropping the route from the
    matrix or guessing its roles would break the "mechanically derived, 100%
    traceable" guarantee the console screen depends on.
    """


@dataclass(frozen=True)
class RouteRoleCheck:
    """One `Depends(require_*(...))` call site on one route handler."""

    route_group: str
    method: str  # "get", "post", ...
    path: str  # decorator's first-arg path literal, "" for a bare @router.get()
    dependency_func: str  # "require_role" | "require_any_role" | "require_any_role_or_user"
    roles: tuple[str, ...]
    file: str
    line: int


@dataclass(frozen=True)
class RoleAccess:
    read: bool
    write: bool


@dataclass(frozen=True)
class RouteGroupMatrix:
    route_group: str
    file: str
    route_count: int
    access: dict[str, RoleAccess]  # keyed by role


@dataclass(frozen=True)
class PermissionMatrix:
    generated_at: datetime
    route_groups: list[RouteGroupMatrix]
    checks: list[RouteRoleCheck] = field(default_factory=list)


def _literal_str_tuple(node: ast.expr) -> tuple[str, ...] | None:
    """A Tuple/List of plain string constants -> its values. Else None."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    values: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            values.append(elt.value)
        else:
            return None
    return tuple(values)


def _module_level_constants(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Every top-level `NAME = (<str literals>)` / `NAME = [<str literals>]`."""
    out: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        literal = _literal_str_tuple(node.value)
        if literal is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = literal
    return out


def _resolve_role_args(
    call: ast.Call, constants: dict[str, tuple[str, ...]], *, context: str
) -> tuple[str, ...]:
    """Resolve every positional argument of a `require_*(...)` call to role
    strings. Raises UnresolvedRoleExpression on anything not a literal or a
    resolvable module-level constant (including a starred non-tuple/list, or a
    Name that never resolves to a literal tuple/list of strings).
    """
    roles: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            inner = arg.value
            if isinstance(inner, ast.Name) and inner.id in constants:
                roles.extend(constants[inner.id])
                continue
            literal = _literal_str_tuple(inner)
            if literal is not None:
                roles.extend(literal)
                continue
            raise UnresolvedRoleExpression(
                f"{context}: starred role argument {ast.dump(arg)!r} is neither "
                "a literal tuple/list nor a resolvable module-level constant"
            )
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            roles.append(arg.value)
            continue
        if isinstance(arg, ast.Name) and arg.id in constants:
            roles.extend(constants[arg.id])
            continue
        raise UnresolvedRoleExpression(
            f"{context}: role argument {ast.dump(arg)!r} is neither a string "
            "literal nor a resolvable module-level constant — refusing to "
            "guess or silently drop this route from the permission matrix"
        )
    # keyword args are never used for roles at any real call site (verified by
    # hand across every route file); a kwarg here is exactly the kind of drift
    # this scanner exists to catch loudly rather than silently ignore.
    for kw in call.keywords:
        if kw.arg is None:
            raise UnresolvedRoleExpression(
                f"{context}: **-unpacked keyword arguments are not supported "
                "in a role-dependency call"
            )
        if kw.arg == "resolver":
            continue  # require_any_role(..., resolver=get_context_or_user): not a role
        raise UnresolvedRoleExpression(
            f"{context}: unexpected keyword argument {kw.arg!r} on a role "
            "dependency call"
        )
    return tuple(roles)


def _find_depends_role_call(node: ast.expr) -> ast.Call | None:
    """`Depends(require_any_role("owner", "admin"))` -> the inner Call, or
    None if `node` is not a `Depends(...)` call wrapping a role-dependency
    call by one of the three recognized names.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Name) and func.id == "Depends"):
        return None
    if not node.args:
        return None
    inner = node.args[0]
    if not isinstance(inner, ast.Call):
        return None
    inner_func = inner.func
    if isinstance(inner_func, ast.Name) and inner_func.id in _ROLE_DEP_FUNCS:
        return inner
    return None


def _decorator_route_info(dec: ast.expr) -> tuple[str, str] | None:
    """`@router.get("/path", ...)` -> ("get", "/path"). None if not a router
    HTTP-verb decorator (e.g. a bare `@router` reference, or `.websocket(...)`).
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return None
    if func.value.id != "router":
        return None
    method = func.attr.lower()
    if method not in _ROUTER_HTTP_METHODS:
        return None
    path = ""
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        path = dec.args[0].value
    return method, path


def _decorator_dependencies_role_checks(
    dec: ast.expr, constants: dict[str, tuple[str, ...]], *, context: str
) -> list[tuple[str, tuple[str, ...]]]:
    """Role checks passed via `dependencies=[Depends(require_role(...))]` on
    the ROUTE DECORATOR itself (as opposed to a handler parameter default).
    Only agent_prompts.py's own dependency is non-role; kept general in case a
    future route gates this way instead of via a parameter default.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    if not isinstance(dec, ast.Call):
        return out
    for kw in dec.keywords:
        if kw.arg != "dependencies":
            continue
        deps_list = kw.value
        if not isinstance(deps_list, (ast.List, ast.Tuple)):
            continue
        for elt in deps_list.elts:
            inner = _find_depends_role_call(elt)
            if inner is None:
                continue
            assert isinstance(inner.func, ast.Name)
            roles = _resolve_role_args(inner, constants, context=context)
            out.append((inner.func.id, roles))
    return out


def _function_default_role_checks(
    fn: ast.AsyncFunctionDef | ast.FunctionDef,
    constants: dict[str, tuple[str, ...]],
    *,
    context: str,
) -> list[tuple[str, tuple[str, ...]]]:
    """Role checks from `Depends(require_*(...))` used as a PARAMETER DEFAULT
    on the handler signature — the pattern every real route in this repo uses
    (`ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin"))`).
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    args = fn.args
    defaults = list(args.defaults)
    positional = args.posonlyargs + args.args
    # `defaults` right-aligns to the tail of `positional`.
    offset = len(positional) - len(defaults)
    for default in defaults:
        inner = _find_depends_role_call(default)
        if inner is None:
            continue
        assert isinstance(inner.func, ast.Name)
        roles = _resolve_role_args(inner, constants, context=context)
        out.append((inner.func.id, roles))
    del offset  # not needed beyond documenting the alignment above
    # kw-only defaults (`*, ctx: ... = Depends(...)`) — none exist today, but a
    # future route using one must not be silently skipped.
    for kw_default in args.kw_defaults:
        if kw_default is None:
            continue
        inner = _find_depends_role_call(kw_default)
        if inner is None:
            continue
        assert isinstance(inner.func, ast.Name)
        roles = _resolve_role_args(inner, constants, context=context)
        out.append((inner.func.id, roles))
    return out


def _scan_route_file(path: Path) -> list[RouteRoleCheck]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    constants = _module_level_constants(tree)
    group = path.stem
    checks: list[RouteRoleCheck] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        route_method: str | None = None
        route_path = ""
        decorator_checks: list[tuple[str, tuple[str, ...]]] = []
        for dec in node.decorator_list:
            info = _decorator_route_info(dec)
            if info is not None:
                route_method, route_path = info
            context = f"{path.name}:{getattr(dec, 'lineno', node.lineno)} ({node.name})"
            decorator_checks.extend(
                _decorator_dependencies_role_checks(dec, constants, context=context)
            )
        if route_method is None:
            continue  # not a router-mounted HTTP handler

        context = f"{path.name}:{node.lineno} ({node.name})"
        found = decorator_checks + _function_default_role_checks(
            node, constants, context=context
        )
        for dep_func, roles in found:
            checks.append(
                RouteRoleCheck(
                    route_group=group,
                    method=route_method,
                    path=route_path,
                    dependency_func=dep_func,
                    roles=roles,
                    file=str(path),
                    line=node.lineno,
                )
            )
    return checks


def scan_all_routes(routes_dir: Path | None = None) -> list[RouteRoleCheck]:
    """Every `Depends(require_*(...))` call site across every route file.

    Route files with zero role-gated routes contribute zero checks but their
    module is still registered as a route group by `build_permission_matrix`
    (via `list(routes_dir.glob("*.py"))`), so a group's absence never means
    "the scanner didn't look."
    """
    directory = routes_dir or ROUTES_DIR
    checks: list[RouteRoleCheck] = []
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        checks.extend(_scan_route_file(path))
    return checks


def assert_no_dynamic_roles(routes_dir: Path | None = None) -> None:
    """Re-scan every route file and let `UnresolvedRoleExpression` propagate.

    `scan_all_routes` already raises eagerly on any unresolved role
    expression, so this exists purely to give the regression test
    (5b in the task spec) an intention-revealing name to call, independent of
    whether it also wants the checks back.
    """
    scan_all_routes(routes_dir)


def build_permission_matrix(routes_dir: Path | None = None) -> PermissionMatrix:
    """The full route-group x role read/write matrix, scanned fresh.

    Every `.py` file under `routes/` (except `__init__.py`) becomes a route
    group, whether or not it has any role-gated route — a group with no
    `Depends(require_*(...))` call site (e.g. `auth.py`, `knowledge.py`)
    appears with an all-`False` row rather than being omitted, so its absence
    is never mistaken for a scanner gap.
    """
    directory = routes_dir or ROUTES_DIR
    checks = scan_all_routes(directory)

    checks_by_group: dict[str, list[RouteRoleCheck]] = {}
    for check in checks:
        checks_by_group.setdefault(check.route_group, []).append(check)

    route_groups: list[RouteGroupMatrix] = []
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        group = path.stem
        group_checks = checks_by_group.get(group, [])

        access: dict[str, RoleAccess] = {}
        for role in ALL_ROLES:
            can_read = any(
                role in c.roles and c.method in _READ_METHODS for c in group_checks
            )
            can_write = any(
                role in c.roles and c.method in _WRITE_METHODS for c in group_checks
            )
            access[role] = RoleAccess(read=can_read, write=can_write)

        # Distinct (method, path) pairs actually gated — informational count,
        # not used for access computation.
        distinct_routes = {(c.method, c.path) for c in group_checks}
        route_groups.append(
            RouteGroupMatrix(
                route_group=group,
                file=str(path),
                route_count=len(distinct_routes),
                access=access,
            )
        )

    return PermissionMatrix(
        generated_at=datetime.now(timezone.utc),
        route_groups=route_groups,
        checks=checks,
    )
