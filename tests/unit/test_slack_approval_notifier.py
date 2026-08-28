"""SlackApprovalNotifier — platform-level HITL approval notifier.

Covers, with no live Slack call (mocked httpx client per test):
  * a successful post carries the bot token, channel, and escalation context;
  * an HTTP failure and a Slack-side `ok: false` are both swallowed (logged,
    not raised) — the caller (AgentExecutionService._enqueue_hitl) must never
    fail the request because a convenience notification failed;
  * `resolve_slack_notifier_config` (bootstrap.py): both unset -> disabled,
    both set -> enabled, exactly one set -> fails closed at boot;
  * `AgentExecutionService._enqueue_hitl` calls the notifier exactly once on a
    governed defer when one is wired, and never calls it when none is wired
    (existing behaviour, unchanged).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from skylize.app.agents.execution import AgentDeferredToHuman, AgentExecutionService
from skylize.app.audit.service import AuditService
from skylize.app.decision_engine.evaluator import DecisionEvaluator
from skylize.app.notifications.slack import SlackApprovalNotifier
from skylize.bootstrap import ConfigurationError, resolve_slack_notifier_config
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import (
    InMemoryAuditRepository,
    InMemoryCapitalRepository,
    InMemoryHitlQueueRepository,
)
from skylize.events.memory_bus import InMemoryEventBus

GOV_ORG = "org_governed"

_INPUT = {
    "brand_name": "Acme",
    "product_description": "A widget",
    "target_audience": "founders",
}


def _llm(payload: dict[str, Any]) -> MagicMock:
    from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage

    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text=json.dumps(payload),
            provider="demo",
            concrete_model="demo-v1",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd_micros=0,
        )
    )
    return llm


def _deliverables() -> MagicMock:
    svc = MagicMock()
    svc.create_deliverable = AsyncMock()
    return svc


# ── SlackApprovalNotifier: the HTTP call itself ──────────────────────────────

def _mock_response(*, status_code: int = 200, body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {"ok": True}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


async def test_notify_posts_bot_token_channel_and_context() -> None:
    notifier = SlackApprovalNotifier(bot_token="xoxb-test", channel_id="C123")
    hitl_id = uuid4()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        await notifier.notify_pending_approval(
            hitl_id=hitl_id,
            org_id=GOV_ORG,
            proposing_agent="hook_generator_agent",
            action_kind="deliverable.create",
            trigger_reason="policy_defer",
            expires_at=None,
        )

    mock_client.post.assert_called_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"
    assert kwargs["json"]["channel"] == "C123"
    assert str(hitl_id) in kwargs["json"]["text"]
    assert GOV_ORG in kwargs["json"]["text"]
    assert "hook_generator_agent" in kwargs["json"]["text"]


async def test_notify_swallows_http_failure() -> None:
    notifier = SlackApprovalNotifier(bot_token="xoxb-test", channel_id="C123")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(status_code=500)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        # Must not raise.
        await notifier.notify_pending_approval(
            hitl_id=uuid4(),
            org_id=GOV_ORG,
            proposing_agent="a",
            action_kind="k",
            trigger_reason="r",
            expires_at=None,
        )


async def test_notify_swallows_slack_not_ok() -> None:
    notifier = SlackApprovalNotifier(bot_token="xoxb-test", channel_id="C123")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(
            body={"ok": False, "error": "channel_not_found"}
        )
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        # Must not raise even though Slack reports failure in a 200 body.
        await notifier.notify_pending_approval(
            hitl_id=uuid4(),
            org_id=GOV_ORG,
            proposing_agent="a",
            action_kind="k",
            trigger_reason="r",
            expires_at=None,
        )


# ── resolve_slack_notifier_config (bootstrap.py) ─────────────────────────────

def test_resolve_config_disabled_when_both_unset() -> None:
    settings = Settings(_env_file=None, slack_bot_token="", slack_approval_channel_id="")
    assert resolve_slack_notifier_config(settings) is None


def test_resolve_config_enabled_when_both_set() -> None:
    settings = Settings(
        _env_file=None, slack_bot_token="xoxb-abc", slack_approval_channel_id="C1"
    )
    assert resolve_slack_notifier_config(settings) == ("xoxb-abc", "C1")


def test_resolve_config_fails_closed_on_token_without_channel() -> None:
    settings = Settings(
        _env_file=None, slack_bot_token="xoxb-abc", slack_approval_channel_id=""
    )
    with pytest.raises(ConfigurationError):
        resolve_slack_notifier_config(settings)


def test_resolve_config_fails_closed_on_channel_without_token() -> None:
    settings = Settings(_env_file=None, slack_bot_token="", slack_approval_channel_id="C1")
    with pytest.raises(ConfigurationError):
        resolve_slack_notifier_config(settings)


# ── wiring: AgentExecutionService._enqueue_hitl notifies on defer ───────────

def _execution(hitl_repo: InMemoryHitlQueueRepository, notifier: SlackApprovalNotifier | None):
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    return AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_llm({"hooks": ["a", "b", "c"]}),
        deliverables=_deliverables(),
        audit=audit,
        evaluator=DecisionEvaluator(registry=MVP_REGISTRY, capital=InMemoryCapitalRepository()),
        hitl=hitl_repo,
        bus=bus,
        governed_org_ids=frozenset({GOV_ORG}),
        slack_notifier=notifier,
    )


async def test_defer_notifies_slack_when_notifier_wired() -> None:
    notifier = MagicMock(spec=SlackApprovalNotifier)
    notifier.notify_pending_approval = AsyncMock()
    execution = _execution(InMemoryHitlQueueRepository(), notifier)

    with pytest.raises(AgentDeferredToHuman) as ei:
        await execution.execute(
            org_id=GOV_ORG, agent_id="hook_generator_agent",
            input_data=dict(_INPUT), user_id="u1",
        )

    notifier.notify_pending_approval.assert_called_once()
    kwargs = notifier.notify_pending_approval.call_args.kwargs
    assert kwargs["hitl_id"] == ei.value.hitl_id
    assert kwargs["org_id"] == GOV_ORG


async def test_defer_does_not_notify_when_no_notifier_wired() -> None:
    execution = _execution(InMemoryHitlQueueRepository(), None)

    with pytest.raises(AgentDeferredToHuman):
        await execution.execute(
            org_id=GOV_ORG, agent_id="hook_generator_agent",
            input_data=dict(_INPUT), user_id="u1",
        )
    # No notifier wired: nothing to assert on except that execution completed
    # without attempting to call one (would have raised AttributeError on None
    # if the guard in _enqueue_hitl were missing).
