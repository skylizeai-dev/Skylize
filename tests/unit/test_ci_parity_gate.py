"""scripts/ci_unit_gate.ps1 must stay in step with ci.yml's `unit` job.

The parity script is a hand-maintained copy of ci.yml's unit-job steps. Without
this test, adding a step to ci.yml leaves the script silently passing while it is
no longer parity — the same failure class the script exists to prevent, one level
up (ruff was a CI step no local gate ran, and CI stayed red from 11b595e6).

WHAT THIS ASSERTS
  1. Every `run:` command in ci.yml's `unit` job is present in the script.
  2. The shared commands appear in the same relative ORDER as in ci.yml.
  3. The script parses into a plausible command set at all, so a rewrite that
     defeats the parser fails loudly instead of vacuously passing.

Commands are compared as TOKEN LISTS, not substrings, and the script's tokens are
read from its actual `$steps` / `Invoke-Gate` structures rather than its text, so
a command merely mentioned in a comment or doc block cannot satisfy the test.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_GATE_SCRIPT = _REPO_ROOT / "scripts" / "ci_unit_gate.ps1"

# Commands in the `$steps` table: Exe = 'x'; Args = @('a', 'b')
_STEPS_ENTRY = re.compile(r"Exe\s*=\s*'([^']+)'\s*;?\s*Args\s*=\s*@\(([^)]*)\)")
# The provisioning step, invoked directly: Invoke-Gate -Exe 'pip' -Arguments @(...)
_DIRECT_CALL = re.compile(r"Invoke-Gate\s+-Exe\s+'([^']+)'\s+-Arguments\s+@\(([^)]*)\)")


def _normalise(tokens: list[str]) -> tuple[str, ...]:
    """Canonicalise an invocation for comparison.

    ONE rule, applied narrowly: a leading ``python -m`` is stripped, because a
    console-script entry point and its ``python -m <module>`` form are the same
    program (the script must use ``python -m pytest`` since this machine's
    Application Control policy blocks the pytest.exe shim).

    Everything after that prefix is compared VERBATIM, so this cannot mask a real
    divergence: a changed, added, or dropped argument still fails. Quoting is not
    normalised away either -- tokens come from shlex, so ``-m "not integration"``
    stays a single token and never collapses into two.
    """
    if tokens[:2] == ["python", "-m"]:
        return tuple(tokens[2:])
    return tuple(tokens)


def _ci_unit_commands() -> list[tuple[str, ...]]:
    """Every `run:` command in ci.yml's `unit` job, in file order."""
    workflow = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["unit"]["steps"]
    commands: list[tuple[str, ...]] = []
    for step in steps:
        run = step.get("run")
        if not run:
            continue  # `uses:` steps (checkout, setup-python) run no command
        stripped = run.strip()
        # A multi-line `run: |` block is a script, not one invocation. None exist
        # in the unit job today; fail loudly rather than silently mis-tokenising.
        assert "\n" not in stripped, (
            f"ci.yml unit job gained a multi-line run block: {stripped!r}. "
            "Teach this test how to compare it before trusting the parity gate."
        )
        commands.append(_normalise(shlex.split(stripped)))
    return commands


def _parse_invocations(pattern: re.Pattern[str], text: str) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    for match in pattern.finditer(text):
        exe = match.group(1)
        raw_args = match.group(2).strip()
        args = [a.strip().strip("'\"") for a in raw_args.split(",") if a.strip()]
        commands.append(_normalise([exe, *args]))
    return commands


def _script_gate_commands() -> list[tuple[str, ...]]:
    """The `$steps` table, in array order.

    For these, declaration order IS execution order (the script foreach-es the
    array), so this sequence is what the order assertion may compare against.
    """
    return _parse_invocations(_STEPS_ENTRY, _GATE_SCRIPT.read_text(encoding="utf-8"))


def _script_other_commands() -> list[tuple[str, ...]]:
    """Commands invoked directly rather than through the `$steps` table.

    Today this is only the opt-in `pip install` provisioning step. Its position in
    the SOURCE is not its execution position -- it runs before the gate loop even
    though it is written after the array literal -- so it counts for presence but
    is deliberately excluded from the order assertion.
    """
    return _parse_invocations(_DIRECT_CALL, _GATE_SCRIPT.read_text(encoding="utf-8"))


def _script_commands() -> list[tuple[str, ...]]:
    """Every command the parity script can invoke."""
    return _script_gate_commands() + _script_other_commands()


def test_parity_script_and_ci_yml_both_parse() -> None:
    """Guard the guard: a parser that finds nothing must not pass vacuously."""
    ci = _ci_unit_commands()
    script = _script_commands()
    assert len(ci) >= 7, f"parsed only {len(ci)} run commands from ci.yml's unit job"
    assert len(script) >= len(ci), (
        f"parsed {len(script)} commands from {_GATE_SCRIPT.name} but ci.yml's unit "
        f"job has {len(ci)}. Did the script's $steps structure change shape?"
    )


@pytest.mark.parametrize("command", _ci_unit_commands(), ids=lambda c: " ".join(c))
def test_every_ci_unit_command_is_in_the_parity_script(command: tuple[str, ...]) -> None:
    """Each ci.yml unit-job command must be one the parity script runs."""
    script = _script_commands()
    assert command in script, (
        f"ci.yml's unit job runs {' '.join(command)!r} but "
        f"scripts/ci_unit_gate.ps1 does not. The local gate is no longer parity: "
        f"add the step to the script (or to its -Install path). "
        f"Script currently runs: {[' '.join(c) for c in script]}"
    )


def test_gate_steps_keep_ci_yml_order() -> None:
    """The script must run the gate steps in ci.yml's order.

    Order is part of the contract: cheap gates first means a type error surfaces
    before a full test run, as ci.yml deliberately sequences them.

    Scoped to the `$steps` table. The provisioning `pip install` is excluded --
    it is written after the array literal but executes before the loop, so its
    source position carries no ordering meaning (see _script_other_commands).
    """
    gates = _script_gate_commands()
    provisioning = set(_script_other_commands())
    ci_gates = [c for c in _ci_unit_commands() if c not in provisioning]

    shared_in_script = [c for c in gates if c in ci_gates]
    expected = [c for c in ci_gates if c in gates]
    assert shared_in_script == expected, (
        "scripts/ci_unit_gate.ps1 runs ci.yml's unit-job gates in a different "
        f"order.\n  ci.yml:  {[' '.join(c) for c in expected]}\n"
        f"  script:  {[' '.join(c) for c in shared_in_script]}"
    )
