"""SlackApprovalNotifier — posts a HITL approval request to Skylize's own
Slack workspace.

Platform-level, not a tenant integration (docs/06_integrations/
integration_inputs.md §2.3, `[APPROVED]` 2026-08-28): one bot token and one
pre-designated channel for the whole platform, sourced from `Settings` exactly
like every other platform secret (`SKYLIZE_CREDENTIAL_ENCRYPTION_KEY`,
`SKYLIZE_ANTHROPIC_API_KEY`, ...) — never `org_credentials`, never resolved
per-org. Post-only: `chat.postMessage` with `chat:write`. No button/
Interactivity handling — that is a separate, unbuilt mechanism (§2.3) and is
explicitly out of scope here.

Best-effort by design: the `hitl_queue` row is already durable by the time
this is called (see `AgentExecutionService._enqueue_hitl`), so a Slack outage
must never fail the request that produced the 202 — it would only lose a
convenience notification, not the escalation itself. Failures are logged, not
raised, mirroring `HitlQueueService._journal_replay`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

log = logging.getLogger(__name__)

_SLACK_API_URL = "https://slack.com/api/chat.postMessage"


class SlackApprovalNotifier:
    """One instance for the process; the token and channel never vary per-call
    (platform-level — contrast `CredentialVault`, which resolves per-org)."""

    def __init__(self, *, bot_token: str, channel_id: str, timeout: float = 10.0) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._timeout = timeout

    async def notify_pending_approval(
        self,
        *,
        hitl_id: UUID,
        org_id: str,
        proposing_agent: str,
        action_kind: str,
        trigger_reason: str,
        expires_at: datetime | None,
    ) -> None:
        text = (
            f":hourglass_flowing_sand: *HITL approval requested*\n"
            f"*Org:* {org_id}\n"
            f"*Agent:* {proposing_agent}\n"
            f"*Action:* {action_kind}\n"
            f"*Reason:* {trigger_reason}\n"
            f"*Expires:* {expires_at.isoformat() if expires_at else 'n/a'}\n"
            f"*HITL ID:* {hitl_id}"
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _SLACK_API_URL,
                    headers={"Authorization": f"Bearer {self._bot_token}"},
                    json={"channel": self._channel_id, "text": text},
                )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            if not body.get("ok"):
                log.error(
                    "slack_approval_notify_failed",
                    extra={
                        "hitl_id": str(hitl_id),
                        "org_id": org_id,
                        "slack_error": body.get("error"),
                    },
                )
        except Exception:
            log.error(
                "slack_approval_notify_failed",
                extra={"hitl_id": str(hitl_id), "org_id": org_id},
                exc_info=True,
            )
