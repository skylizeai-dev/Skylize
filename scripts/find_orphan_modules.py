"""
Orphan-module contract — fail CI when a module becomes unreachable.

import-linter's contracts constrain edges that EXIST in the import graph; none
of its built-in contract types can say "every module must be imported by
something". pytest has the same blind spot from the other side: collection only
imports what a test file actually references. Put together, a module nothing
imports (the orchestrator/temporal/activities.py incident) is invisible to
every existing gate even when it cannot load at all.

This script closes the reachability half of that gap (the loadability half is
scripts/check_all_modules_importable.py):

  1. Build the same static import graph import-linter uses (grimp).
  2. A module is an ORPHAN if nothing in src/skylize imports it, no test
     imports it, and it is not a declared entry point below.
  3. Compare against the committed allowlist scripts/orphan_modules.txt —
     the ratchet. Known orphans are listed there for a human to disposition
     (delete / wire / test); a NEW orphan fails the build. An entry that is no
     longer an orphan also fails, so the allowlist can only shrink honestly.

Run: python scripts/find_orphan_modules.py [--update]
    --update rewrites scripts/orphan_modules.txt with the current orphan set
    (use when intentionally accepting the state, e.g. after this file's
    initial adoption; the diff still goes through review).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import grimp

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
ALLOWLIST = Path(__file__).resolve().parent / "orphan_modules.txt"

# Modules that legitimately have no importer inside src/ or tests/ — process
# entry points and executable roots. Keep this list honest: an entry here must
# be somebody's documented way INTO the code, not a parking spot.
ENTRY_POINTS: frozenset[str] = frozenset(
    {
        "skylize",                             # the package root itself
        "skylize.edge.gateway",                # uvicorn/asgi entry (deploy.ps1, Dockerfile)
        "skylize.services.obsidian_writer.app", # standalone writer service entry
    }
)


def _test_imports() -> set[str]:
    """Every skylize.* module name imported anywhere under tests/."""
    imported: set[str] = set()
    for path in TESTS_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("skylize"):
                        imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module and node.module.startswith("skylize"):
                    imported.add(node.module)
                    # `from skylize.a import b` may import MODULE b, not just a name.
                    for alias in node.names:
                        imported.add(f"{node.module}.{alias.name}")
    return imported


def find_orphans() -> list[str]:
    graph = grimp.build_graph("skylize")
    test_imported = _test_imports()

    orphans: list[str] = []
    for module in sorted(graph.modules):
        if module in ENTRY_POINTS:
            continue
        # A package __init__ is reachable when any of its submodules is —
        # importing skylize.app.audit.service necessarily executes
        # skylize.app.audit. Treat "has any imported descendant" as reachable.
        descendants = graph.find_descendants(module)
        candidates = {module} | descendants
        imported_by_src = any(graph.find_modules_that_directly_import(m) for m in candidates)
        imported_by_tests = any(m in test_imported for m in candidates)
        has_entry_descendant = any(m in ENTRY_POINTS for m in candidates)
        if not (imported_by_src or imported_by_tests or has_entry_descendant):
            orphans.append(module)

    # Report only the highest orphaned ancestor (an orphaned package implies
    # every descendant is unreachable through it; listing all is noise).
    top_level = [
        m for m in orphans
        if not any(m != o and m.startswith(o + ".") for o in orphans)
    ]
    return top_level


def main() -> int:
    orphans = find_orphans()

    if "--update" in sys.argv:
        ALLOWLIST.write_text(
            "# Known orphaned modules — nothing in src/ or tests/ imports these.\n"
            "# Each entry needs a human disposition: delete, wire, or test.\n"
            "# Regenerate deliberately with: python scripts/find_orphan_modules.py --update\n"
            + "".join(f"{m}\n" for m in orphans),
            encoding="utf-8",
        )
        print(f"orphan_modules.txt updated ({len(orphans)} entries).")
        return 0

    known = {
        line.strip()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    } if ALLOWLIST.exists() else set()

    new = [m for m in orphans if m not in known]
    resolved = sorted(known - set(orphans))

    if new:
        sys.stderr.write(
            f"{len(new)} NEW orphaned module(s) — nothing imports them, no test loads them:\n"
            + "".join(f"  {m}\n" for m in new)
            + "Wire it, test it, or (deliberately) add it to scripts/orphan_modules.txt.\n"
        )
    if resolved:
        sys.stderr.write(
            f"{len(resolved)} allowlist entr(y/ies) no longer orphaned — remove from "
            f"scripts/orphan_modules.txt:\n" + "".join(f"  {m}\n" for m in resolved)
        )
    if new or resolved:
        return 1

    print(f"OK: no new orphan modules ({len(orphans)} known, allowlisted).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
