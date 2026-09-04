"""Proactive health probe for a customer's WIF federation.

THE PROBLEM THIS SOLVES, stated as the governing audit stated it: "A kill switch
that discovers it has lost its authority at the instant of use is worse than no
kill switch, because it was relied upon."
(docs/audits/audit_gcp_killswitch_readiness.md B.5.)

Under Workload Identity Federation nothing is stored, so nothing goes stale in a
way anyone would notice. A customer can delete the pool, disable the provider, or
remove the IAM role binding, and Skylize learns at the next call - which, for a
safety control, is the moment it is needed.

WHY THE PROBE MUST REACH COMPUTE AND NOT STOP AT THE TOKEN EXCHANGE
-------------------------------------------------------------------
This is the single most important design fact in this module, and it is the one
an implementer is most likely to "optimise" away, so it is stated plainly:

    Removing the IAM role binding leaves the STS token exchange SUCCEEDING and
    only the Compute call failing.

The trust relationship and the authorization are two independent layers on two
independent Google surfaces. A probe that exchanges a token and declares victory
verifies the first and assumes the second. It would report `valid` for a
connection that can no longer do the one thing it exists to do, which is a false
negative on exactly the failure most likely to occur in practice - IAM cleanup is
routine, deleting a federation pool is not.

So the probe is two-legged, and the leg that answered is recorded in
`last_probe_result` rather than being flattened into `connection_state`:

    sts_failed      -> the TRUST relationship is broken   -> re-create federation
    compute_denied  -> trust fine, AUTHORIZATION missing  -> re-add the binding

Those are different remedies. Collapsing them would send a customer to rebuild a
federation that was never broken.

CLASSIFICATION DISCIPLINE, INHERITED AND NON-NEGOTIABLE
-------------------------------------------------------
`app/credentials/oauth.py` establishes that only an UNAMBIGUOUS provider signal
may write a terminal state, because marking a connection dead is destructive to
the customer (`mark_revoked_by_provider`, oauth.py:430-437; `_classify_failure`
"refuses to mark a grant dead on a transient network fault", migration
0021:50-52). The same rule governs here, with one addition specific to federation:

    Google's troubleshooting page documents only a handful of STS error shapes -
    `invalid_grant` for an unreachable issuer/JWKS, `invalid_request` for an
    oversized subject, `quota_exceeded`/429, and invalid JWK format (verified
    2026-09-04). It does NOT document the error returned for a DISABLED pool, a
    failed attribute condition, or an audience mismatch.

Every undocumented shape is therefore classified `misconfigured`, never
`revoked`. A wrong `revoked` tells a customer to rebuild their entire federation
when the fix was one CEL expression. `_classify_sts_failure` carries that
asymmetry explicitly and the design doc's failure table (§7.2 rows 3, 5, 6) marks
these as UNVERIFIED pending empirical characterisation against a real pool.

A TRANSIENT FAILURE NEVER OVERWRITES A GOOD STATE. Network faults, 5xx, and 429
record the probe result and a reason but carry the connection's EXISTING state
forward. "We could not check" must never collapse into "there was nothing to
find" - the same distinction `RefreshUnavailable` draws against `GrantRevoked`
(oauth.py).

THIS MODULE READS ONLY. `_compute_get` issues a GET against `instances.get`. There
is no stop, no mutation, and no verb here that changes a customer's
infrastructure: a health check that could stop a machine would be a new incident
class, not a safety feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ...config import Settings
from ...dal.gcp_wif import (
    ConnectionState,
    GcpWifConnectionRow,
    GcpWifRepository,
    GcpWifTargetRow,
    ProbeResult,
)
from .keys import WifSigningKey
from .oidc import issuer_url
from .tokens import WifTokenError, mint_id_token

log = logging.getLogger("skylize.gcp.probe")

STS_TOKEN_URL = "https://sts.googleapis.com/v1/token"
COMPUTE_API_BASE = "https://compute.googleapis.com/compute/v1"

#: OAuth 2.0 token-exchange constants for the STS call
#: (docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds,
#: verified 2026-09-04).
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: STS error codes whose meaning IS documented and unambiguous enough to write a
#: terminal state from. Everything else is treated as `misconfigured`.
#: `invalid_grant` here means Google could not reach or match the issuer - which,
#: on a connection that previously worked, means the pool or provider is gone.
_DOCUMENTED_TRUST_GONE = {"invalid_grant"}

#: Transient by documentation: retry, never a verdict.
_TRANSIENT_STS_CODES = {"quota_exceeded", "internal_failure", "server_error"}

_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """What one probe concluded. `state` is what the customer must act on;
    `result` is which layer answered."""

    result: ProbeResult
    state: ConnectionState
    reason: str | None
    #: True when the outcome is an operational fault on Skylize's side or the
    #: network, NOT a verdict about the customer's configuration. Such an outcome
    #: carries the previous state forward and is worth alerting on internally.
    transient: bool = False

    @property
    def healthy(self) -> bool:
        return self.result == "ok"


class WifHealthProbe:
    """Two-legged federation health check: STS exchange, then a Compute read.

    Constructed with an injected async HTTP client so tests exercise the real
    classification logic against scripted responses rather than a mock of the
    classifier itself.
    """

    def __init__(
        self,
        *,
        repo: GcpWifRepository,
        key: WifSigningKey,
        settings: Settings,
        http_client_factory: Any,
    ) -> None:
        self._repo = repo
        self._key = key
        self._settings = settings
        self._client_factory = http_client_factory

    # -- public API ---------------------------------------------------------

    async def probe_org(self, org_id: str, label: str = "") -> ProbeOutcome | None:
        """Probe one org's connection and PERSIST the outcome. None if no row.

        Org enumeration is deliberately the caller's job - see the note in
        `dal/gcp_wif.py` about why this repository holds no cross-tenant read.
        """
        row = await self._repo.get(org_id, label)
        if row is None:
            return None
        outcome = await self.probe_connection(row)
        await self._repo.record_probe(
            conn_id=row.conn_id,
            org_id=row.org_id,
            result=outcome.result,
            state=outcome.state,
            reason=outcome.reason,
            probed_at=datetime.now(timezone.utc),
        )

        # Log the LAYER and the resulting state, never the reason string and
        # never a token: `reason` can quote a provider error body, and log sinks
        # are a wider audience than the tenant-scoped row. A state change away
        # from healthy is the operationally interesting event, so it is a
        # warning; steady-state health is debug.
        if outcome.result != "ok":
            log.warning(
                "gcp_wif.probe_unhealthy",
                extra={
                    "org_id": org_id,
                    "label": label,
                    "probe_result": outcome.result,
                    "connection_state": outcome.state,
                    "transient": outcome.transient,
                },
            )
        else:
            log.debug(
                "gcp_wif.probe_ok", extra={"org_id": org_id, "label": label}
            )
        return outcome

    async def probe_connection(self, row: GcpWifConnectionRow) -> ProbeOutcome:
        """Run both legs and classify. Pure with respect to storage - the caller
        persists, so this can be exercised without a database."""
        targets = await self._repo.list_targets(
            row.org_id, row.conn_id, enabled_only=True
        )

        try:
            token = self._mint(row)
        except WifTokenError as exc:
            # A mint failure is ALWAYS Skylize-side (bad config, oversized
            # subject). It says nothing about the customer's trust relationship,
            # so it must not touch their state.
            return ProbeOutcome(
                result="unreachable",
                state=row.connection_state,
                reason=f"skylize could not mint an assertion: {exc}",
                transient=True,
            )

        sts, access_token = await self._exchange(token, row)
        if sts.result != "ok" or access_token is None:
            return sts

        if not targets:
            # STS proved the trust relationship; nothing proves the binding.
            # Reporting this as healthy is the exact false-positive this probe
            # exists to prevent, so it gets its own non-healthy result.
            return ProbeOutcome(
                result="no_targets",
                state="unverified",
                reason=(
                    "federation trust verified, but the connection names no "
                    "enabled instance to verify the IAM role binding against. "
                    "Add at least one target."
                ),
            )

        return await self._compute_get(access_token, row, targets[0])

    # -- leg 1: the trust relationship --------------------------------------

    def _mint(self, row: GcpWifConnectionRow) -> str:
        return mint_id_token(
            key=self._key,
            issuer=issuer_url(self._settings.wif_issuer_base_url, row.issuer_slug),
            org_id=row.org_id,
            audience=row.audience,
            environment=self._settings.wif_environment,
        )

    async def _exchange(
        self, token: str, row: GcpWifConnectionRow
    ) -> tuple[ProbeOutcome, str | None]:
        """Exchange the assertion at STS. Returns the outcome and, on success,
        the federated access token.

        THE TOKEN IS A SEPARATE RETURN VALUE, NEVER A FIELD ON `ProbeOutcome`.
        `ProbeOutcome.reason` is persisted verbatim into
        `gcp_wif_connections.state_reason`, so a short-lived Google credential
        riding in that field would be one refactor away from being written to the
        database and read back by anyone with the row. Keeping it out of the
        dataclass entirely means that mistake cannot be made by accident.
        `tests/unit/test_wif_probe.py` pins that no federated token reaches the
        repository.
        """
        payload = {
            "audience": row.audience,
            "grantType": _GRANT_TYPE,
            "requestedTokenType": _REQUESTED_TOKEN_TYPE,
            "scope": _SCOPE,
            "subjectTokenType": _SUBJECT_TOKEN_TYPE,
            "subjectToken": token,
        }
        try:
            async with self._client_factory(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(STS_TOKEN_URL, json=payload)
        except Exception as exc:  # noqa: BLE001 - transport faults are transient
            return ProbeOutcome(
                result="unreachable",
                state=row.connection_state,
                reason=f"could not reach Google STS: {exc}",
                transient=True,
            ), None

        if resp.status_code == 200:
            body = _safe_json(resp)
            access = body.get("access_token")
            if not isinstance(access, str) or not access:
                return ProbeOutcome(
                    result="unreachable",
                    state=row.connection_state,
                    reason="STS returned 200 with no access_token",
                    transient=True,
                ), None
            # `reason=None` on success: nothing about a healthy exchange belongs
            # in a persisted human-readable field, least of all the credential.
            return ProbeOutcome(result="ok", state="valid", reason=None), access

        return self._classify_sts_failure(resp, row), None

    def _classify_sts_failure(
        self, resp: Any, row: GcpWifConnectionRow
    ) -> ProbeOutcome:
        """Turn an STS non-200 into a state, conservatively.

        The asymmetry here is the point: `revoked` is written ONLY for a
        documented, unambiguous signal. Everything undocumented becomes
        `misconfigured`, which points the customer at a setting rather than at
        rebuilding their federation.
        """
        body = _safe_json(resp)
        code = str(body.get("error") or "").strip()
        detail = str(body.get("error_description") or "").strip() or resp.text[:300]

        if resp.status_code >= 500 or code in _TRANSIENT_STS_CODES:
            return ProbeOutcome(
                result="unreachable",
                state=row.connection_state,
                reason=f"STS transient failure ({resp.status_code} {code}): {detail}",
                transient=True,
            )

        if resp.status_code == 429:
            return ProbeOutcome(
                result="unreachable",
                state=row.connection_state,
                reason=f"STS rate limited: {detail}",
                transient=True,
            )

        if code in _DOCUMENTED_TRUST_GONE:
            # Documented: Google could not connect to, or match, the credential's
            # issuer. On a connection that previously exchanged successfully this
            # means the pool or provider is gone.
            return ProbeOutcome(
                result="sts_failed",
                state="revoked",
                reason=(
                    f"Google rejected the federation trust ({code}): {detail}. "
                    "The workload identity pool or provider appears to be deleted "
                    "or disabled; the federation must be re-created."
                ),
            )

        # Everything else: the trust may well be intact and a single setting
        # wrong (audience, attribute condition, a disabled provider whose error
        # shape Google does not document). Point at a setting, not a rebuild.
        return ProbeOutcome(
            result="sts_failed",
            state="misconfigured",
            reason=(
                f"Google refused the assertion ({resp.status_code} "
                f"{code or 'unspecified'}): {detail}. Check the provider's "
                "audience and attribute condition."
            ),
        )

    # -- leg 2: the IAM role binding ----------------------------------------

    async def _compute_get(
        self, access_token: str, row: GcpWifConnectionRow, target: GcpWifTargetRow
    ) -> ProbeOutcome:
        """Read one target instance. READ ONLY - never a mutation.

        `instances.get` is the lightest call that actually exercises the role
        binding on the resource the real action would touch. Anything lighter
        (a project-level list, say) would pass on a binding that does not cover
        this instance, which is the failure it is here to catch.
        """
        url = (
            f"{COMPUTE_API_BASE}/projects/{target.gcp_project_id}"
            f"/zones/{target.zone}/instances/{target.instance_name}"
        )
        try:
            async with self._client_factory(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except Exception as exc:  # noqa: BLE001
            return ProbeOutcome(
                result="compute_unavailable",
                state=row.connection_state,
                reason=f"could not reach the Compute API: {exc}",
                transient=True,
            )

        if resp.status_code == 200:
            return ProbeOutcome(
                result="ok",
                state="valid",
                reason=None,
            )

        if resp.status_code in (401, 403):
            # THE FINDING THIS PROBE EXISTS FOR: the trust relationship is fine
            # (STS already succeeded above) and the role binding is not.
            return ProbeOutcome(
                result="compute_denied",
                state="misconfigured",
                reason=(
                    f"federation succeeded but Compute denied "
                    f"{target.gcp_project_id}/{target.zone}/{target.instance_name} "
                    f"({resp.status_code}). The IAM role binding on that instance "
                    "is missing or insufficient - re-add it. The federation itself "
                    "is intact; do NOT re-create it."
                ),
            )

        if resp.status_code == 404:
            # AMBIGUOUS ON PURPOSE. Google returns 404 both for an instance that
            # was deleted and, in some cases, for one the caller may not know
            # exists. Either way the customer must act - remove a stale target or
            # grant the binding - and neither reading justifies `revoked`.
            return ProbeOutcome(
                result="compute_denied",
                state="misconfigured",
                reason=(
                    f"Compute returned 404 for {target.gcp_project_id}/"
                    f"{target.zone}/{target.instance_name}. The instance is "
                    "deleted, the zone is wrong, or the binding does not permit "
                    "seeing it. Correct the target list or the IAM binding."
                ),
            )

        return ProbeOutcome(
            result="compute_unavailable",
            state=row.connection_state,
            reason=f"Compute returned {resp.status_code}: {resp.text[:300]}",
            transient=True,
        )


def _safe_json(resp: Any) -> dict[str, Any]:
    """Decode a JSON body, never raising. A provider that returns HTML on an
    error path must not turn into a decode traceback that hides the status code
    the classifier actually needs."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}
