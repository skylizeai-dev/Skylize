"""
Fail the build if any module under src/skylize cannot be imported.

import-linter (lint-imports) only parses import *statements* — it builds a
static module dependency graph via grimp and never executes code, so it
cannot notice that a name doesn't actually exist in the module it's imported
from. A module can pass every import-linter contract while being unable to
load at all (e.g. `from skylize.dal.ports import WorkflowRepository` where
`WorkflowRepository` was never defined — the orchestrator/temporal/activities.py
bug fixed in f1043406). Nothing else catches this either: pytest only imports
modules that are actually referenced from a test file, so a module with no
test and no bootstrap-path import stays invisible until it's exercised at
runtime in production.

This walks every .py file under src/skylize and imports it directly, so the
"can it even load" check is exhaustive regardless of test coverage or
reachability from the composition root.

    python scripts/check_all_modules_importable.py
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "skylize"


def iter_module_names() -> list[str]:
    names = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


def main() -> int:
    # Prefer this checkout's own src/ over any editable install pointed at a
    # different checkout (a worktree sharing one Python environment with the
    # main repo would otherwise silently import the wrong copy).
    sys.path.insert(0, str(SRC_ROOT))

    module_names = iter_module_names()
    failures: list[tuple[str, str]] = []
    for name in module_names:
        try:
            importlib.import_module(name)
        except Exception:
            failures.append((name, traceback.format_exc()))

    if failures:
        sys.stderr.write(f"{len(failures)} module(s) failed to import:\n\n")
        for name, tb in failures:
            sys.stderr.write(f"--- {name} ---\n{tb}\n")
        return 1

    print(f"OK: {len(module_names)} modules under src/skylize import cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
