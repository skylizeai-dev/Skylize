from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .config import DecisionEngineSettings
from .exceptions import OPAPolicyDenied
from .models import DecisionContext, OPAResult, ScoringResult

log = logging.getLogger(__name__)

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
    ) -> OPAResult:
        """Evaluate the action against OPA (guardrails.md §5 contract):
        ``{allow, require_human, deny: [reasons], policy_version}``.

        Raises ``OPAPolicyDenied`` on any transport/protocol failure so the
        caller treats the action as denied (fail-closed).
        """
        body = {"input": self._build_input(context, scoring_result)}

        # httpx's `json=` uses the stdlib encoder, which raises TypeError on a raw
        # UUID/datetime. That escapes as a bare TypeError rather than a denial,
        # which contradicts this class's fail-closed contract. The sole production
        # producer already hands us a JSON-native payload (consumer.py:239, locked
        # by tests/decision_engine/test_consumer.py:207), so this is defence in
        # depth for a second producer, not a live bug — but "unreachable today"
        # is not a reason to let the contract be false.
        try:
            json.dumps(body)
        except (TypeError, ValueError) as exc:
            raise OPAPolicyDenied(
                self._policy_path, "OPA input is not JSON-serializable — fail-closed"
            ) from exc

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
            body_doc = response.json()
        except ValueError as exc:
            # Malformed 200 body must not be read as an allow.
            raise OPAPolicyDenied(
                self._policy_path, "OPA returned malformed response — fail-closed"
            ) from exc

        # A 200 whose top-level document is valid JSON but not an object (a list,
        # string or number) has no `.get`. Before this guard that raised a bare
        # AttributeError out of `evaluate`, escaping the fail-closed contract
        # entirely — the caller saw a crash, not a denial. The isinstance check
        # further down guards the `result` VALUE; this one guards the envelope.
        if not isinstance(body_doc, dict):
            raise OPAPolicyDenied(
                self._policy_path,
                f"OPA response body was {type(body_doc).__name__}, expected an object "
                "— fail-closed",
            )

        result = body_doc.get("result") or {}

        if not isinstance(result, dict):
            # A bare scalar (e.g. `opa_policy_path` misconfigured to a leaf
            # boolean rule) is not the package-document contract this client
            # parses — fail closed rather than crash on `.get`.
            raise OPAPolicyDenied(
                self._policy_path,
                f"OPA result was {type(result).__name__}, expected an object — fail-closed",
            )

        # Default-deny: a missing/false `allow` is a denial.
        allow = bool(result.get("allow", False))
        # Accept the task contract key `deny_reasons`; fall back to guardrails.md
        # §5's `deny` field name. Default to [].
        raw_reasons = result.get("deny_reasons")
        if raw_reasons is None:
            raw_reasons = result.get("deny", [])
        deny_reasons = list(raw_reasons)

        # Absent `require_human` is the normal Rego idiom for an undefined
        # rule (no defer condition matched) — not a fail-closed gap, since
        # reaching here already required an explicit `allow`.
        require_human = bool(result.get("require_human", False))

        policy_version = result.get("policy_version")
        if allow and policy_version is None:
            # A live allow with no policy_version can't be replayed/audited
            # against the policy that produced it — flag loudly, but don't
            # convert it into a spurious deny (guardrails.md §5 doesn't gate
            # `allow` on `policy_version` presence).
            log.warning(
                "opa_allow_missing_policy_version",
                extra={"policy_path": self._policy_path, "event_id": context.event_id},
            )

        return OPAResult(
            allow=allow,
            require_human=require_human,
            deny_reasons=deny_reasons,
            policy_version=policy_version,
        )

    async def close(self) -> None:
        await self._client.aclose()
