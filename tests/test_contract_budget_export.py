"""Gate: a generated agent spec must never contradict its real AgentContract.

Generated specs used to print `max_token_budget` / `max_execution_time_seconds`
from a hardcoded per-authority-level table in scripts/gen_agent_specs.js. For
11 of the 15 roster agents that actually HAVE a registered AgentContract, that
table printed a number contradicting the contract -- while looking authoritative.

The fix is a Python->JSON->JS bridge (scripts/export_contract_budgets.py ->
scripts/contract_budgets.json), because the JS generators cannot read the Python
registry at generation time: the spec gate sandboxes docs/ + scripts/ only, with
no src/ (scripts/check_agent_network_data.py:159-169).

This module guards both halves of that bridge:

1. The committed JSON must match a fresh export. Otherwise a contract budget
   change silently leaves the specs printing the old number -- exactly the
   "generated file drift" failure the roster gate already guards for the UI data.

2. Every registered agent WITH a spec must print that contract's real numbers
   and `budget_source = "contract"`. A spec showing
   `level_default_unimplemented` for a contract-backed agent, or a number the
   contract disagrees with, fails here.

Unlike the JS generators, a Python test CAN import src/ -- mirroring
scripts/check_agent_network_data.py:204-205.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXPORT_JSON = ROOT / "scripts" / "contract_budgets.json"
SPEC_ROOT = ROOT / "docs" / "03_agents" / "01_executive_board"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


def _registry():
    from skylize.contracts.registry import MVP_REGISTRY

    return {c.agent_id: c for c in MVP_REGISTRY.all()}


def _spec_paths() -> dict[str, Path]:
    """agent_id -> spec path, keyed by filename stem."""
    return {p.stem: p for p in SPEC_ROOT.rglob("*.md")}


def test_committed_export_matches_a_fresh_one() -> None:
    """scripts/contract_budgets.json must not be stale w.r.t. MVP_REGISTRY."""
    import export_contract_budgets

    assert EXPORT_JSON.exists(), (
        f"{EXPORT_JSON.relative_to(ROOT)} is missing. "
        "Run `python scripts/export_contract_budgets.py` and commit the result."
    )
    committed = EXPORT_JSON.read_text(encoding="utf-8").replace("\r\n", "\n")
    fresh = export_contract_budgets.render().replace("\r\n", "\n")
    assert committed == fresh, (
        f"{EXPORT_JSON.relative_to(ROOT)} is out of date with MVP_REGISTRY. "
        "Run `python scripts/export_contract_budgets.py`, re-run "
        "`node scripts/gen_agent_specs.js` and "
        "`node scripts/gen_agent_network_data.js`, and commit all of it. "
        "Never hand-edit the export."
    )


def test_export_covers_every_registered_contract() -> None:
    exported = json.loads(EXPORT_JSON.read_text(encoding="utf-8"))["budgets"]
    missing = sorted(set(_registry()) - set(exported))
    assert not missing, f"registered agent_id(s) absent from the export: {missing}"


@pytest.mark.parametrize(
    "agent_id",
    sorted(set(_registry()) & set(_spec_paths())),
)
def test_spec_shows_the_real_contract_budget(agent_id: str) -> None:
    """A contract-backed agent's spec must print the contract, marked as such."""
    contract = _registry()[agent_id]
    text = _spec_paths()[agent_id].read_text(encoding="utf-8")

    source = re.search(r'`budget_source = "([a-z_]+)"`', text)
    assert source, (
        f"{agent_id}: its spec carries no `budget_source` marker at all. "
        "Re-run `node scripts/gen_agent_specs.js`."
    )
    assert source.group(1) == "contract", (
        f"{agent_id} HAS a registered AgentContract "
        f"(max_token_budget={contract.max_token_budget}, "
        f"max_execution_time_seconds={contract.max_execution_time_seconds}) "
        f'but its spec is labelled budget_source="{source.group(1)}". The '
        "generator failed to resolve it -- check that "
        "scripts/contract_budgets.json is current and that "
        "gen_agent_specs.js budgetFor() reads it."
    )

    budget = re.search(r"`max_token_budget = (\d+)`", text)
    secs = re.search(r"`max_execution_time_seconds = (\d+)`", text)
    assert budget and secs, f"{agent_id}: spec does not state its budget envelope"

    assert int(budget.group(1)) == contract.max_token_budget, (
        f"{agent_id}: spec prints max_token_budget={budget.group(1)} but the "
        f"contract says {contract.max_token_budget}. Re-export and regenerate."
    )
    assert int(secs.group(1)) == contract.max_execution_time_seconds, (
        f"{agent_id}: spec prints max_execution_time_seconds={secs.group(1)} "
        f"but the contract says {contract.max_execution_time_seconds}. "
        "Re-export and regenerate."
    )


def test_uncontracted_agents_are_labelled_honestly() -> None:
    """A spec with no contract must SAY its numbers are unimplemented defaults."""
    registry, specs = _registry(), _spec_paths()
    wrong = []
    for agent_id, path in sorted(specs.items()):
        if agent_id in registry:
            continue
        source = re.search(
            r'`budget_source = "([a-z_]+)"`', path.read_text(encoding="utf-8")
        )
        if not source or source.group(1) != "level_default_unimplemented":
            wrong.append(agent_id)
    assert not wrong, (
        "agent(s) with NO registered AgentContract whose spec does not carry "
        f'budget_source="level_default_unimplemented": {wrong}. Their printed '
        "budget is an authority-level guess and must be labelled as one."
    )
