"""Architectural invariant: the elevated-action gate has exactly one chokepoint.

Deny-by-default for elevated actions (integration_inputs.md 2.5, Q2.5d) rests on
three structural facts, not on convention:

  1. A tool handler is invoked from exactly ONE place — `ToolProxy.invoke`. If a
     second dispatch site appeared, it could run a sharing handler without any of
     the gates.
  2. A `ToolContext` is constructed in exactly ONE place — the same method. The
     `permission_grant` field is the handler's proof that the gate ran, so a
     context built elsewhere could hand a handler a forged or absent grant.
  3. A `PermissionGrant` is minted ONLY inside `app/permissions/gate.py`. It is
     the authorization token; anything else producing one would be minting its
     own permission.

These are source-level assertions on purpose. A runtime test cannot prove the
ABSENCE of an alternative dispatch path, and that absence is exactly what makes
the opt-in profile safe. A future pass that legitimately adds a second call site
must update this file deliberately — and that edit is the moment to re-check that
the new path enforces the same gates.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "skylize"


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _sites(pattern: str) -> list[str]:
    """Every (file:line) where `pattern` matches, comments and strings excluded."""
    found: list[str] = []
    rx = re.compile(pattern)
    for path in _python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if rx.search(line):
                rel = path.relative_to(SRC).as_posix()
                found.append(f"{rel}:{lineno}")
    return sorted(found)


def test_tool_handlers_are_invoked_from_exactly_one_place() -> None:
    """Asserted on file + count, not on a line number: an unrelated edit above
    the call site is noise, a SECOND call site is the actual signal."""
    sites = _sites(r"\.handler\(")
    assert len(sites) == 1 and sites[0].startswith("tools/proxy.py:"), (
        f"tool handlers are dispatched from {sites}. Exactly one dispatch site is "
        "what guarantees every handler passes the credential, permission, and "
        "spend gates. A new site must enforce them too."
    )


def test_tool_context_is_constructed_in_exactly_one_place() -> None:
    sites = _sites(r"ToolContext\(")
    assert len(sites) == 1 and sites[0].startswith("tools/proxy.py:"), (
        f"ToolContext is constructed at {sites}. `permission_grant` on that "
        "context is a handler's only proof the gate ran; a context built "
        "elsewhere could carry a grant nothing authorized."
    )


def test_permission_grants_are_minted_only_by_the_gate() -> None:
    sites = _sites(r"PermissionGrant\(")
    assert all(s.startswith("app/permissions/gate.py:") for s in sites), (
        f"PermissionGrant is constructed outside the gate at {sites}. It is the "
        "authorization token for an elevated action — minting one anywhere else "
        "is self-authorization."
    )
    assert sites, "expected the gate to mint PermissionGrants"
