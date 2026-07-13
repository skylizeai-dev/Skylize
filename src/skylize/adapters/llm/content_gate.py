"""
LLM content gate — deterministic, stateless first-line screening for
prompt-injection signals before a request reaches any provider.

Why deterministic and not an LLM call: `prompt_injection_agent` and
`llm_safety_agent` (docs/03_agents/.../CSO_Security/managers/workers/) are
themselves `llm.generate`-calling agents. Gating every provider egress by
invoking one of those agents would recurse (the agent's own `llm.generate`
call would hit the same gate). This module is instead a fast, explainable,
no-I/O pattern screen — a first line of defense, not a replacement for the
Safety Suite's deeper (asynchronous, LLM-based) review. It never reads or
writes memory, so wiring it in front of every egress can never put the
memory-read/write-access=[] Safety Suite agents into a memory loop.

A non-match is not proof of safety; it only means none of the known
signatures fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .gateway import (
    LLMGateway,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
)

if TYPE_CHECKING:
    from ...tools.base import ToolDefinition


class GuardrailViolation(Exception):
    """Raised when the content gate denies a request before provider egress.

    Carries the matched signal names for audit/logging; the flagged text
    itself is never forwarded past this point.
    """

    def __init__(self, signals: list[str]) -> None:
        self.signals = signals
        super().__init__("blocked by content gate: " + ", ".join(signals))


@dataclass(frozen=True, slots=True)
class _Signal:
    name: str
    pattern: re.Pattern[str]


_SIGNALS: tuple[_Signal, ...] = (
    _Signal(
        "instruction_override",
        re.compile(
            r"ignore (all|any|the) (previous|prior|above|preceding) instructions"
            r"|disregard (all|any|the) (previous|prior|above|system) (instructions|prompt)"
            r"|forget (all|any|the) (previous|prior|above) instructions",
            re.IGNORECASE,
        ),
    ),
    _Signal(
        "system_prompt_exfiltration",
        re.compile(
            r"(reveal|print|repeat|show|output) (your|the) (system prompt|instructions)"
            r"|what (are|is) your (system prompt|instructions)",
            re.IGNORECASE,
        ),
    ),
    _Signal(
        "role_override",
        re.compile(
            r"you are now\b|from now on you are|new instructions\s*:|system\s*:\s*override"
            r"|act as if you have no (restrictions|rules|guidelines)"
            r"|enter (developer|dan|jailbreak) mode",
            re.IGNORECASE,
        ),
    ),
)


class LLMContentGate:
    """Stateless deterministic prompt-injection screen.

    No I/O, no memory access, no LLM call of its own — safe to run in front
    of every provider egress without recursion or added latency.
    """

    def check(self, *texts: str | None) -> None:
        """Raise ``GuardrailViolation`` if any text matches a known signal."""
        matched: list[str] = []
        for text in texts:
            if not text:
                continue
            for signal in _SIGNALS:
                if signal.name not in matched and signal.pattern.search(text):
                    matched.append(signal.name)
        if matched:
            raise GuardrailViolation(matched)


class GuardedLLMGateway:
    """`LLMGateway` decorator that gates every request through an
    `LLMContentGate` before delegating to the wrapped gateway.

    Constructed once at the composition root (bootstrap.py) and threaded
    through as the shared `llm` reference, so every caller that already
    receives that reference — `AgentExecutionService`, `LLMStepRunner`,
    `ToolProxy.dispatch_llm` — is gated uniformly without touching each call
    site individually.
    """

    def __init__(self, gateway: LLMGateway, gate: LLMContentGate | None = None) -> None:
        self._gateway = gateway
        self._gate = gate or LLMContentGate()

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        self._gate.check(request.prompt, request.system)
        return await self._gateway.generate(request)

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        self._gate.check(request.prompt, request.system)
        return self._gateway.generate_sync(request)

    async def generate_with_tools(
        self, request: LLMGenerateWithToolsRequest, tools: list["ToolDefinition"]
    ) -> LLMGenerateResponse:
        texts: list[str | None] = [request.system]
        for message in request.messages:
            for block in message.content:
                if block.kind == "text":
                    texts.append(block.text)
                elif block.kind == "tool_result":
                    # Tool output is the classic indirect-injection vector
                    # (web/MCP content re-entering the model's context) —
                    # screen it, not just first-party user/system text.
                    texts.append(block.tool_output)
        self._gate.check(*texts)
        return await self._gateway.generate_with_tools(request, tools)


__all__ = ["GuardrailViolation", "LLMContentGate", "GuardedLLMGateway"]
