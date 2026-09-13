"""Roster gate: the generated UI data must match the manifest, and the manifest
must know every agent the runtime can actually run.

Two checks, both of which have silently failed before and neither of which any
test catches:

1. DRIFT. `website/src/components/console/agent-network.data.ts` and
   `app/Skylize-Console-Black/src/data/agentNetworkData.js` are both emitted by
   `scripts/gen_agent_network_data.js` from
   `docs/03_agents/_generation_manifest.csv`. They are checked in, so a hand
   edit to either one, or a manifest change without a regeneration, survives
   review and then quietly disagrees with the other surface. This check runs the
   generator and fails if what it produces differs from what is committed.

   Line endings are normalised before comparison: the files are checked out CRLF
   on Windows and the generator writes LF, so a byte comparison would fail for a
   reason that has nothing to do with content.

   The working tree is restored either way, so running this never leaves the
   repository dirty.

2. COVERAGE. Every agent_id in `MVP_REGISTRY.all()` must have a manifest row.
   Without this, an agent can be added to the runtime registry and never appear
   in the org chart, the roster, or the console — governed in production and
   invisible to everyone looking at the documented organization.

   `KNOWN_CODE_ONLY_AGENTS` is the exception list, and it is an exception list,
   not a permanent carve-out: owner ruling 4 kept these eight code-only for now
   and deferred writing their specs. They are named here so that a NINTH
   un-specified agent fails the build while these eight do not. Removing a name
   from this list when its spec lands is the point.

3. SPEC DRIFT. The 154 agent specs under
   `docs/03_agents/01_executive_board/` are emitted by
   `scripts/gen_agent_specs.js` from `scripts/agent_content.js`. Six of them
   once carried hand-authored content the generator could not reproduce, so
   re-running it would have destroyed that content -- and the only thing
   stopping that was nobody happening to run it. The authored content now
   lives in `agent_content.js` (the generator's `tools` / `readMem` /
   `writeMem` / `memoryNote` fields), which is what makes a full run safe.

   This check proves that is still true: it renders every spec into a throw-
   away copy of the tree and fails if any committed spec differs. It fails
   on a hand edit to a spec (content belongs in agent_content.js now) AND on
   a dropped agent_content.js field -- dropping `memoryNote` from the four
   stateless Safety Suite agents makes their specs differ, which is exactly
   the data loss this check exists to prevent. The real tree is never
   written to: the generator runs against the copy.

Run from the repository root:  python scripts/check_agent_network_data.py
Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "03_agents" / "_generation_manifest.csv"
GENERATOR = ROOT / "scripts" / "gen_agent_network_data.js"
SPEC_GENERATOR = ROOT / "scripts" / "gen_agent_specs.js"
SPEC_ROOT = ROOT / "docs" / "03_agents" / "01_executive_board"
GENERATED = (
    ROOT / "website" / "src" / "components" / "console" / "agent-network.data.ts",
    ROOT / "app" / "Skylize-Console-Black" / "src" / "data" / "agentNetworkData.js",
)

#: Canonical id remaps, mirroring CANON_ID in both generators. A manifest row
#: whose path carries the `vc_procurement` typo still covers `vp_procurement`.
CANON_ID = {"vc_procurement": "vp_procurement"}

#: Registered agent_ids that deliberately have no spec file and no manifest row
#: (owner ruling 4). Documented in docs/03_agents/00_organization_chart.md §6.1.
#: Shrink this list as specs land; never grow it without an owner decision.
KNOWN_CODE_ONLY_AGENTS = frozenset(
    {
        "agency_deliverable_drafter",
        "agency_requirements_analyst",
        "cfo_agent",
        "cowork_agent",
        "infrastructure_executor",
        "lead_qualifier_agent",
        "sdr_outreach_agent",
        "seo_keyword_agent",
    }
)


def _normalised(path: Path) -> str:
    """File content with line endings normalised to LF."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def check_generated_files_are_current() -> list[str]:
    """Regenerate both UI data files and report any that differ from HEAD."""
    if shutil.which("node") is None:
        return ["node is not on PATH; cannot verify the generated roster files"]

    missing = [p for p in GENERATED if not p.exists()]
    if missing:
        return [f"generated file is missing: {p.relative_to(ROOT)}" for p in missing]

    backup_dir = Path(tempfile.mkdtemp(prefix="roster_gate_"))
    backups = {}
    try:
        for i, path in enumerate(GENERATED):
            backup = backup_dir / f"{i}{path.suffix}"
            backup.write_bytes(path.read_bytes())
            backups[path] = backup

        result = subprocess.run(
            ["node", str(GENERATOR)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return [
                "gen_agent_network_data.js failed:\n"
                + (result.stderr or result.stdout).strip()
            ]

        failures = []
        for path, backup in backups.items():
            committed = backup.read_text(encoding="utf-8").replace("\r\n", "\n")
            if _normalised(path) != committed:
                failures.append(
                    f"{path.relative_to(ROOT)} is out of date with "
                    f"{MANIFEST.relative_to(ROOT)}. "
                    "Run `node scripts/gen_agent_network_data.js` and commit the "
                    "result. Never hand-edit a generated file."
                )
        return failures
    finally:
        # Restore whatever was committed so the gate leaves no diff behind.
        for path, backup in backups.items():
            path.write_bytes(backup.read_bytes())
        shutil.rmtree(backup_dir, ignore_errors=True)


def check_agent_specs_are_current() -> list[str]:
    """Re-render every agent spec in a temp copy and report any that differ."""
    if shutil.which("node") is None:
        return ["node is not on PATH; cannot verify the agent spec files"]

    committed = sorted(SPEC_ROOT.rglob("*.md"))
    if not committed:
        return [f"no agent specs found under {SPEC_ROOT.relative_to(ROOT)}"]

    # The generator hardcodes its paths relative to the repo root and writes in
    # place, so it is run against a disposable copy of docs/ + scripts/. The
    # real tree is never opened for writing by this gate.
    sandbox = Path(tempfile.mkdtemp(prefix="spec_gate_"))
    try:
        shutil.copytree(ROOT / "docs", sandbox / "docs")
        shutil.copytree(ROOT / "scripts", sandbox / "scripts")

        result = subprocess.run(
            ["node", str(sandbox / "scripts" / SPEC_GENERATOR.name)],
            cwd=sandbox,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return [
                f"{SPEC_GENERATOR.name} failed:" + chr(10)
                + (result.stderr or result.stdout).strip()
            ]

        failures = []
        for path in committed:
            rendered = sandbox / path.relative_to(ROOT)
            if not rendered.exists():
                failures.append(
                    f"{path.relative_to(ROOT)} was not produced by "
                    f"{SPEC_GENERATOR.name}"
                )
                continue
            # Normalised: the tree is mixed CRLF/LF (core.autocrlf, no
            # .gitattributes) and an EOL difference is not spec drift.
            if _normalised(rendered) != _normalised(path):
                failures.append(
                    f"{path.relative_to(ROOT)} differs from a fresh "
                    f"{SPEC_GENERATOR.name} run. Agent specs are generated: put "
                    "the authored content in scripts/agent_content.js (fields: "
                    "role, mission, resp, kpis, inputs, outputs, deps, consumes, "
                    "produces, tools, hitl, failure, readMem, writeMem, "
                    "memoryNote, success, failureNote) and re-run "
                    "`node scripts/gen_agent_specs.js`. Never hand-edit a spec."
                )
        return failures
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def check_every_runtime_agent_has_a_manifest_row() -> list[str]:
    """Report registered agent_ids with no row in the generation manifest."""
    sys.path.insert(0, str(ROOT / "src"))
    from skylize.contracts.registry import MVP_REGISTRY

    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest_ids = {
        CANON_ID.get(row["agent_name"], row["agent_name"]) for row in rows
    }

    failures = []
    unspecified = sorted(
        c.agent_id for c in MVP_REGISTRY.all() if c.agent_id not in manifest_ids
    )
    new = [a for a in unspecified if a not in KNOWN_CODE_ONLY_AGENTS]
    if new:
        failures.append(
            "registered agent_id(s) with no row in "
            f"{MANIFEST.relative_to(ROOT)}: {', '.join(new)}. "
            "Add a spec under docs/03_agents/01_executive_board/ and regenerate "
            "the manifest, or add the id to KNOWN_CODE_ONLY_AGENTS in this "
            "script with an owner decision recording why it stays code-only."
        )

    # The allow-list must not outlive its entries: a name that now HAS a row is
    # a stale exception, and a stale exception is how the next gap hides.
    stale = sorted(KNOWN_CODE_ONLY_AGENTS & manifest_ids)
    if stale:
        failures.append(
            "KNOWN_CODE_ONLY_AGENTS lists agent_id(s) that now have a manifest "
            f"row: {', '.join(stale)}. Remove them from the list in "
            f"{Path(__file__).name}."
        )
    return failures


def main() -> int:
    failures = check_generated_files_are_current()
    failures += check_agent_specs_are_current()
    failures += check_every_runtime_agent_has_a_manifest_row()
    if failures:
        print("ROSTER GATE FAILED:\n")
        for failure in failures:
            print(f"  - {failure}\n")
        return 1
    print(
        "OK: both generated roster files and all agent specs match a fresh "
        "generator run, and every "
        "registered agent_id has a manifest row "
        f"({len(KNOWN_CODE_ONLY_AGENTS)} allow-listed code-only agents)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
