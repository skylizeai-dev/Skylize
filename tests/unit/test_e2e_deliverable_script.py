"""scripts/e2e_deliverable.py must stay in sync with the live agent-execute
contract (HookGeneratorExecuteIn, src/skylize/schemas/agents/creative.py).

Regression for a drift where the script posted {brief_id, product, audience}
against a contract requiring {brand_name, product_description, target_audience}
(forbid-extra) -- a 422 that nothing in the test suite caught because no test
ran the script itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "e2e_deliverable.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("e2e_deliverable_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not SCRIPT_PATH.exists(), reason="scripts/e2e_deliverable.py not found")
def test_e2e_deliverable_script_round_trips_against_live_contract(monkeypatch):
    monkeypatch.delenv("SKYLIZE_ANTHROPIC_API_KEY", raising=False)
    sys.modules.pop("e2e_deliverable_script", None)
    module = _load_script()
    assert module.main() == 0
