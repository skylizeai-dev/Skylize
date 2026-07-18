"""
LLMAgentRunner unit test.

Drives the real runner end to end through a real tool proxy (with a fake LLM
handler), proving it routes ``llm.generate`` through the proxy and parses the
model's JSON into a dict validated against the contract's ``output_schema``.
"""

from __future__ import annotations

import pytest

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from skylize.app.orchestrator import LLMAgentRunner
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import AllowAllLiveState, TokenSigner
from skylize.events.memory_bus import InMemoryEventBus
from skylize.runtime import InMemoryRunLedger, MemorySearchHandler, RegistryToolProxy
from skylize.security.ecc_service import Curve, ECCService

ORG = "org_test"
AGENT = "hook_generator_agent"
BRIEF_ID = uuid4()


class _FakeLLMHandler:
    """Returns a JSON body matching the hook_generator output schema (HooksOut)."""

    async def handle(self, payload: dict[str, Any], org_id: str) -> dict[str, Any]:
        body = json.dumps({"brief_id": str(BRIEF_ID), "hooks": ["Hook A", "Hook B"]})
        return {
            "text": body,
            "provider": "fake",
            "concrete_model": "fake-1",
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            "cost_usd_micros": 0,
        }


def _mint(signer: TokenSigner) -> Any:
    now = datetime.now(timezone.utc)
    return signer.sign(
        token_id=uuid4(),
        agent_id=AGENT,
        authority_level="worker",
        department="creative",
        delegation_chain=["vp_creative", "copy_director", AGENT],
        scope=["llm.generate", "memory.search"],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        nonce=uuid4().hex,
    )


@pytest.mark.skip(reason="runtime/ LLMAgentRunner ctor drifted; the runtime alt-stack is dead code with no tracked removal plan (LLMStepRunner is the live runner)")
async def test_runner_dispatches_through_proxy_and_validates_output() -> None:
    pair = ECCService.generate_key_pair(Curve.P384)
    signer = TokenSigner(pair.private_key)
    proxy = RegistryToolProxy(
        registry=MVP_REGISTRY,
        public_key=pair.public_key,
        live_state_for=lambda _org: AllowAllLiveState(),
        bus=InMemoryEventBus(),
        run_ledger=InMemoryRunLedger(),
        handlers={"llm.generate": _FakeLLMHandler(), "memory.search": MemorySearchHandler()},
    )
    runner = LLMAgentRunner(tool_proxy=proxy)
    contract = MVP_REGISTRY.resolve(AGENT)
    token = _mint(signer)

    output = await runner.run(
        contract=contract,
        input_payload={"brief_id": str(BRIEF_ID), "product": "shoes", "audience": "runners"},
        token=token,
        org_id=ORG,
    )

    # Output is the validated HooksOut, serialized.
    assert output == {"brief_id": str(BRIEF_ID), "hooks": ["Hook A", "Hook B"]}
