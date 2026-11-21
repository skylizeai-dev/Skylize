"""Tripwire: nothing under src/skylize may reference `sweep_expired`.

WHY THIS EXISTS. Owner ruling OD30: an OUTCOME UNKNOWN failure KEEPS its spend
hold, and only reconciliation EVIDENCE may release it, never the passage of time
(docs/architecture/idempotency_reconciliation_design.md). But `sweep_expired`
(dal/spend_reservation.py) expires every `held` row once `expires_at` passes, so
the moment anything SCHEDULES it, a retained hold is handed back to the ceiling
after DEFAULT_HOLD_TTL (15 minutes) and the ceiling is bypassed for spend that
may already have happened.

Today nothing schedules it: the only references in src are the method definitions
and prose comments. This test keeps it that way until the sweeper has a
reconcile-aware predicate. It FAILS the moment any module under src/skylize
references the name, whether as a call, an attribute, a bare name, an import, a
keyword, or a string literal equal to the name (the by-name forms a scheduler or
`getattr` would use).

When the sweeper is fixed, wiring it in is a deliberate act: delete this test in
the same change and say so in the PR.

Static (`ast`), no imports of the code under scan, no I/O beyond reading source.
"""

from __future__ import annotations

import ast
from pathlib import Path

NAME = "sweep_expired"

#: Repo root derived from this file (tests/unit/<this file>), never hardcoded.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "skylize"

#: The one module that defines the method. A `def` is a definition, not a
#: reference, so it never counts anywhere; this path is only asserted to have been
#: SEEN so a broken scan cannot pass vacuously.
DEFINING_MODULE = SRC_ROOT / "dal" / "spend_reservation.py"

FAIL_MESSAGE = (
    "OD30: `sweep_expired` is referenced under src/skylize, but it expires ANY "
    "`held` reservation at expires_at and so would free a hold that OD30 says must "
    "be KEPT until reconciliation evidence arrives. Do not schedule or call it "
    "until the sweeper skips reconcile-required holds. See "
    "docs/architecture/idempotency_reconciliation_design.md and "
    "docs/decisions/2026-11-07_owner_rulings.md (OD30). References found:\n"
)


def find_references(source: str) -> list[tuple[int, str]]:
    """Return (lineno, kind) for every reference to `sweep_expired` in `source`.

    Counted: a Name, an Attribute, an import alias, a keyword argument, and a
    string literal EQUAL to the name (so `getattr(x, "sweep_expired")` and a
    by-name schedule are caught). NOT counted: `def`/`async def` (definitions),
    comments, and docstrings or any longer string that merely mentions the name.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == NAME:
            found.append((node.lineno, "name"))
        elif isinstance(node, ast.Attribute) and node.attr == NAME:
            found.append((node.lineno, "attribute"))
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] == NAME:
            found.append((node.lineno, "import"))
        elif isinstance(node, ast.keyword) and node.arg == NAME:
            found.append((node.value.lineno, "keyword"))
        elif isinstance(node, ast.Constant) and node.value == NAME:
            found.append((node.lineno, "string-literal"))
    return sorted(found)


def _defines(source: str) -> bool:
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == NAME
        for n in ast.walk(ast.parse(source))
    )


def test_no_module_under_src_references_sweep_expired() -> None:
    py_files = sorted(SRC_ROOT.rglob("*.py"))
    # Guard against a vacuous pass: a wrong root or an empty glob finds nothing.
    assert py_files, f"no .py files found under {SRC_ROOT}; the tripwire scanned nothing"
    assert DEFINING_MODULE in py_files, f"{DEFINING_MODULE} not scanned; root is wrong"

    offenders: list[str] = []
    defining_module_seen_defining = False
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        if path == DEFINING_MODULE and _defines(source):
            defining_module_seen_defining = True
        for lineno, kind in find_references(source):
            offenders.append(f"  {path.relative_to(REPO_ROOT).as_posix()}:{lineno} ({kind})")

    assert defining_module_seen_defining, (
        f"`def {NAME}` not found in {DEFINING_MODULE.name}; the scan or the repo moved"
    )
    assert not offenders, FAIL_MESSAGE + "\n".join(offenders)


# -- the scanner itself: prove each form fires and each non-form does not --------


def test_scanner_flags_every_reference_form() -> None:
    cases = {
        "call on an object": "async def f(r):\n    await r.sweep_expired(now=1)\n",
        "bare name": "x = sweep_expired\n",
        "import": "from skylize.dal.spend_reservation import sweep_expired\n",
        "keyword": "schedule(job=1, sweep_expired=True)\n",
        "getattr by name": 'fn = getattr(repo, "sweep_expired")\n',
        "string schedule": 'JOBS = ["sweep_expired"]\n',
    }
    for label, source in cases.items():
        assert find_references(source), f"scanner missed: {label}"


def test_scanner_ignores_definitions_comments_and_prose() -> None:
    quiet = (
        "class R:\n"
        "    async def sweep_expired(self):\n"
        '        """Docstring that mentions sweep_expired in prose."""\n'
        "        # comment: sweep_expired reclaims at expires_at\n"
        '        return "sweep_expired reclaims holds"\n'
    )
    assert find_references(quiet) == []
    assert _defines(quiet)
