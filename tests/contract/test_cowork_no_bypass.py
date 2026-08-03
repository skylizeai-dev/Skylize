"""No "it's just chat" fast path — asserted against the real import graph.

A pasted grep proves a claim at the moment someone ran it. This module makes the
same claim executable, so the day a future edit gives the chat route its own
ToolProxy, its own LLM gateway, or its own token, CI says so instead of a human
having to re-run a search nobody remembers to re-run.

THE CLAIM. `POST /api/v1/cowork/turns` reaches a tool through exactly one
collaborator -- `container.agent_execution` -- and therefore walks the identical
governed pipeline `POST /api/v1/agents/execute` walks. The route itself cannot
dispatch a tool, cannot mint or validate a governance token, and cannot call a
provider, because it does not import anything that could.

Uses grimp, the same static graph import-linter and scripts/find_orphan_modules.py
build, so this is the graph CI already trusts.
"""

from __future__ import annotations

import grimp
import pytest

ROUTE = "skylize.edge.routes.cowork"
EXECUTION = "skylize.app.agents.execution"
PROXY = "skylize.tools.proxy"


@pytest.fixture(scope="module")
def graph() -> grimp.ImportGraph:
    return grimp.build_graph("skylize")


def test_the_chat_route_imports_nothing_from_skylize_tools(graph) -> None:
    """No ToolProxy, no ToolRegistry, no tool handler. The route has no way to
    invoke a tool itself, so there is no code path from it to a side effect that
    could skip validate_tool_call."""
    direct = graph.find_modules_directly_imported_by(ROUTE)
    tool_imports = sorted(m for m in direct if m.startswith("skylize.tools"))
    assert tool_imports == [], (
        f"{ROUTE} now imports {tool_imports} -- a chat route that can reach a tool "
        "directly is exactly the fast path this design forbids"
    )


def test_the_chat_route_cannot_mint_or_validate_a_governance_token(graph) -> None:
    """`contracts.token` holds both TokenSigner and validate_tool_call. A route
    that imported it could construct its own token, or worse, decide for itself
    that a call was valid."""
    direct = graph.find_modules_directly_imported_by(ROUTE)
    assert "skylize.contracts.token" not in direct
    assert "skylize.app.governance.authority" not in direct


def test_the_chat_route_reaches_tools_only_through_agent_execution(graph) -> None:
    """The positive half: it DOES depend on the governed pipeline, and that
    pipeline is what holds the proxy. Without this, the two tests above could be
    satisfied by a route that reaches tools some third way."""
    direct = graph.find_modules_directly_imported_by(ROUTE)
    assert EXECUTION in direct, f"{ROUTE} no longer goes through {EXECUTION}"
    assert PROXY in graph.find_modules_directly_imported_by(EXECUTION)


def test_the_tool_proxy_validates_before_it_can_dispatch(graph) -> None:
    """The sole IF-TOOL dispatcher imports the ordered validation pipeline.

    Structural, not behavioural -- tools/proxy.py:114-130 is where it is actually
    called and tests/integration/test_cowork_scope_denial.py proves a real denial
    -- but if this import ever disappears, the proxy has stopped being a gate.
    """
    assert "skylize.contracts.token" in graph.find_modules_directly_imported_by(PROXY)


def test_no_other_edge_route_dispatches_tools_directly(graph) -> None:
    """Generalises the claim: NO route module anywhere under edge/routes/ holds a
    tool. If a future surface bypasses the pipeline, it fails here even if nobody
    thought to write that surface its own test."""
    offenders = {
        module: sorted(
            m for m in graph.find_modules_directly_imported_by(module)
            if m.startswith("skylize.tools")
        )
        for module in graph.modules
        if module.startswith("skylize.edge.routes.")
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"edge routes importing tools directly: {offenders}"
