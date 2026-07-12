"""Fail CI on any DIRECT LangChain/CrewAI-ecosystem import under src/.

ARCHITECTURAL CONTRACT (owner decision): the codebase must not build agent
logic on the LangChain/CrewAI high-level abstractions. That means NO direct
application-code imports of `langchain`, `langchain_*`, `langsmith`, or
`crewai` anywhere under src/.

`langchain_core` / `langsmith` / `langchain_protocol` arriving TRANSITIVELY as
dependencies of the mandated `langgraph` orchestration layer are ACCEPTED and
load-bearing — LangGraph structurally requires langchain-core at every
published version. This guard therefore scans our SOURCE import statements
(via AST), never the resolved dependency tree — a `pip list` check would
false-positive on the accepted transitive packages. Being AST-based, it also
ignores prose mentions in docstrings/comments (e.g. gateway.py's "CrewAI runs
inside a LangGraph node" docstring is NOT a violation).

    py -3.12 scripts/check_forbidden_imports.py          # scans src/, exit 1 on violation
    py -3.12 scripts/check_forbidden_imports.py path ... # scan explicit paths (used by self-test)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Exact top-level module names that are forbidden as direct imports.
FORBIDDEN_EXACT = {"crewai", "langchain", "langsmith"}
# Prefix families: langchain_core, langchain_openai, langchain_community, ...
# NOTE: `langgraph` matches NEITHER rule and is explicitly allowed (mandated stack).
FORBIDDEN_PREFIXES = ("langchain_",)


def _is_forbidden(top_level: str) -> bool:
    return top_level in FORBIDDEN_EXACT or top_level.startswith(FORBIDDEN_PREFIXES)


def _iter_imports(tree: ast.AST):
    """Yield (module_dotted_path, lineno) for every import statement in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (`from . import x`) carry level > 0 and are internal.
            if node.level == 0 and node.module:
                yield node.module, node.lineno


def scan(paths: list[Path]) -> list[tuple[Path, int, str]]:
    """Return (file, lineno, module) for every forbidden direct import."""
    violations: list[tuple[Path, int, str]] = []
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.rglob("*.py")) if p.is_dir() else [p])
    for f in sorted(set(files)):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for module, lineno in _iter_imports(tree):
            top = module.split(".", 1)[0]
            if _is_forbidden(top):
                violations.append((f, lineno, module))
    return violations


def main() -> int:
    default_src = Path(__file__).resolve().parent.parent / "src"
    parser = argparse.ArgumentParser(
        description="Fail on direct LangChain/CrewAI imports under src/ (transitive deps allowed).",
    )
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="files or directories to scan (default: the repo's src/ tree)",
    )
    args = parser.parse_args()
    paths: list[Path] = args.paths or [default_src]

    violations = scan(paths)
    if violations:
        sys.stderr.write(
            "FORBIDDEN import(s) found — direct LangChain/CrewAI imports are banned:\n"
        )
        for f, lineno, module in violations:
            sys.stderr.write(f"  {f}:{lineno}: imports `{module}`\n")
        sys.stderr.write(
            "\nRule: no direct `langchain` / `langchain_*` / `langsmith` / `crewai` "
            "imports in src/.\nlanggraph's TRANSITIVE langchain_core is accepted — this "
            "guard bans only direct source imports.\n"
        )
        return 1

    sys.stdout.write("OK: no direct LangChain/CrewAI imports in scanned sources.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
