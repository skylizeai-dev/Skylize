from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import DecisionEngineSettings
from .exceptions import OPAPolicyDenied
from .models import DecisionContext, ScoringResult

# guardrails.md §4 names the action inputs OPA reads (kind, amount, target,
# governance_token_id, agent.authority_level) but does NOT enumerate a PII
# safe-list for the raw event payload. We therefore allowlist (never denylist):
# only keys known to be policy-relevant and PII-free are forwarded; anything
# unknown is dropped rather than leaked to the policy engine. Fail-closed for
# data exposure, mirroring the default-deny posture of §4.
SAFE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "action_kind",
        "kind",
        "amount",
        "currency",
        "target",
        "channel",
        "campaign_id",
        "ad_account_id",
        "authority_level",
        "governance_token_id",
    }
)

_RETRY_DELAY_SECONDS = 0.1


class OPAClient:
    """HTTP client for Open Policy Agent guardrail evaluation.

    Fail-closed by contract: any failure to obtain an explicit ``allow=true``
    from OPA — timeout, unreachable host, non-200, malformed body, or an
    absent ``allow`` key — results in denial. OPA being down MUST NOT become a
    silent allow (guardrails.md §4: "Absence of an explicit allow is a denial").
    """

    def __init__(self, settings: DecisionEngineSettings) -> None:
        self._settings = settings
        self._policy_path = settings.opa_policy_path
        self._url = (
            f"{settings.opa_url.rstrip('/')}/v1/data/{settings.opa_policy_path}"
        )
        self._client = httpx.AsyncClient(timeout=settings.opa_timeout_seconds)

    def _build_input(
        self,
        context: DecisionContext,
        scoring_result: ScoringResult | None,
    ) -> dict[str, Any]:
        safe_payload = {
            key: value
            for key, value in context.payload.items()
            if key in SAFE_PAYLOAD_KEYS
        }
        input_doc: dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "department": context.department,
            "event_type": context.event_type,
            "payload": safe_payload,
        }
        if scoring_result is not None:
            input_doc["risk_band"] = scoring_result.risk_band.value
            input_doc["risk_score"] = scoring_result.risk_score
            input_doc["opportunity_score"] = scoring_result.opportunity_score
        return input_doc

    async def evaluate(
        self,
        context: DecisionContext,
        scoring_result: ScoringResult | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluate the action against OPA. Returns ``(allow, deny_reasons)``.

        Raises ``OPAPolicyDenied`` on any transport/protocol failure so the
        caller treats the action as denied (fail-closed).
        """
        body = {"input": self._build_input(context, scoring_result)}

        try:
            response = await self._client.post(self._url, json=body)
        except httpx.TimeoutException as exc:
            raise OPAPolicyDenied(
                self._policy_path, "OPA timeout — fail-closed"
            ) from exc
        except httpx.ConnectError:
            # One retry after 100ms on a connection error, then fail-closed.
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            try:
                response = await self._client.post(self._url, json=body)
            except httpx.TimeoutException as exc:
                raise OPAPolicyDenied(
                    self._policy_path, "OPA timeout — fail-closed"
                ) from exc
            except httpx.ConnectError as exc:
                raise OPAPolicyDenied(
                    self._policy_path, "OPA unreachable — fail-closed"
                ) from exc

        if response.status_code != 200:
            raise OPAPolicyDenied(
                self._policy_path, f"OPA returned {response.status_code}"
            )

        try:
            result = response.json().get("result") or {}
        except ValueError as exc:
            # Malformed 200 body must not be read as an allow.
            raise OPAPolicyDenied(
                self._policy_path, "OPA returned malformed response — fail-closed"
            ) from exc

        # Default-deny: a missing/false `allow` is a denial.
        allow = bool(result.get("allow", False))
        # Accept the task contract key `deny_reasons`; fall back to guardrails.md
        # §5's `deny` field name. Default to [].
        raw_reasons = result.get("deny_reasons")
        if raw_reasons is None:
            raw_reasons = result.get("deny", [])
        deny_reasons = list(raw_reasons)

        return (allow, deny_reasons)

    async def close(self) -> None:
        await self._client.aclose()
