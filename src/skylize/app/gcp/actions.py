"""The externally-mutating half of the GCP kill switch: stop a VM, drop its external IP.

This is the first code in Skylize that changes a CUSTOMER's infrastructure.
Everything here is shaped by that.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
Two Compute Engine calls, in order, both POST, both idempotency-keyed:

  1. ``instances.stop``             - stops the VM.
  2. ``instances.deleteAccessConfig`` - removes the external-IP access config.

NOT ``addresses.delete``, and NOT anything touching a disk. Both are
IRREVERSIBLE, and the HITL replay path this action runs under releases a row back
to 'pending' on a transient failure (app/hitl/service.py:234-246) so a human can
retry - which re-executes the whole run. That retry is only safe for operations
that converge on a state. Disk deletion was excluded by owner decision for
exactly this reason; ``addresses.delete`` is excluded here on the same reasoning,
and the reasoning is recorded so a later pass does not add it casually.

WHAT THE IP RELEASE ACTUALLY BUYS - CORRECTED AGAINST GOOGLE'S DOCS
-------------------------------------------------------------------
It is a REACHABILITY control, not a cost control. Verified 2026-09-04 against
docs.cloud.google.com/compute/docs/instances/suspend-stop-reset-instances-overview:

  * "Compute Engine releases ephemeral IP addresses when an instance is stopped,
    and it assigns a new ephemeral IP address to the instance when the instance
    restarts."
  * "Static external IP addresses are maintained. If you reserve a static
    external IP address and don't assign it to an instance, OR YOU ASSIGN IT AN
    INSTANCE IN THE `TERMINATED` STATE, then you're charged at a higher rate than
    for static and ephemeral external IP addresses that are in use."

So, precisely:
  - For an EPHEMERAL IP the stop has already released the address. Removing the
    access config is still what stops a RESTART from silently handing the
    instance a brand-new public IP. That is the real value.
  - For a STATIC IP the stop alone already moved it to the higher unassigned
    rate. Detaching it does not reduce that, and only ``addresses.delete`` would
    - which is out of scope above.

Nobody should describe this action as saving money on IP charges. It shrinks the
blast radius by removing the machine's route to the internet and keeping it
removed across a restart.

IDEMPOTENCY - THE POINT OF THE WHOLE MODULE
--------------------------------------------
``HitlQueueService.approve`` mints a FRESH correlation id per approval attempt
(app/hitl/service.py:160) and releases the row to 'pending' on a transient
failure, so a human retry re-runs everything. An idempotency key derived from
``correlation_id`` would therefore be different on every retry and Google's
deduplication - the exact mechanism that makes the retry safe - would never fire.

``requestId`` is therefore derived from ``hitl_id``, which is stable across those
retries. Google's own wording (Compute v1 discovery, revision 20260828): "Specify
a unique request ID so that if you must retry your request, the server will know
to ignore the request if it has already been completed... The request ID must be
a valid UUID with the exception that zero UUID is not supported."

That same wording forces the second half of the design: the server ignores a
request whose id it has ALREADY SEEN. One id shared by both calls would make the
access-config removal look like a duplicate of the stop and be dropped. So each
operation gets its own deterministic derivation - stable across retries, distinct
across operations. See ``request_id_for``.

WHEN THERE IS NO ``hitl_id`` THIS ACTION REFUSES. It does not fall back to
``correlation_id``. Without a retry-stable key the idempotency contract above
cannot be honoured, and an action that mutates customer infrastructure must not
run on a weaker guarantee than the one it claims.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from ...dal.gcp_wif import GcpWifConnectionRow
from .keys import WifSigningKey
from .oidc import issuer_url
from .tokens import mint_id_token

log = logging.getLogger("skylize.gcp.actions")

COMPUTE_API_BASE = "https://compute.googleapis.com/compute/v1"
STS_TOKEN_URL = "https://sts.googleapis.com/v1/token"

_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: Namespace for deterministic requestId derivation. A fixed, arbitrary UUID:
#: its only job is to keep Skylize's derived ids from colliding with any other
#: uuid5 namespace. It must NEVER change - changing it would silently break the
#: retry-deduplication of every in-flight approval.
_REQUEST_ID_NS = UUID("6f0b6f7a-6a1c-4c2e-9a3f-4b5d6e7f8a90")

#: Operation discriminators. Distinct values are what stop Google treating the
#: second call as a duplicate of the first (see module docstring).
OP_STOP = "instances.stop"
OP_RELEASE_IP = "instances.deleteAccessConfig"

#: Compute Engine's default external-IP access config name. Overridable per
#: target because a customer may have named theirs differently.
DEFAULT_ACCESS_CONFIG = "External NAT"
DEFAULT_NETWORK_INTERFACE = "nic0"

_TIMEOUT_SECONDS = 30.0


class GcpActionError(RuntimeError):
    """A kill-switch action could not be completed."""


class MissingIdempotencyAnchor(GcpActionError):
    """No `hitl_id` was available, so no retry-stable `requestId` can be derived.

    Deliberately fatal rather than falling back to `correlation_id`: that value is
    fresh on every approval attempt, so a fallback would produce a DIFFERENT
    request id on the retry path Google's deduplication exists to protect, while
    still looking correct.
    """


def request_id_for(hitl_id: UUID, operation: str) -> UUID:
    """A deterministic, non-zero `requestId` for one (approval, operation) pair.

    Stable across retries of the SAME approval - which is what makes Google
    ignore the duplicate rather than acting twice. Distinct across operations -
    which is what stops the second call being ignored as a duplicate of the first.
    Distinct across approvals - so a genuinely new decision is genuinely executed.
    """
    generated = uuid5(_REQUEST_ID_NS, f"{hitl_id}:{operation}")
    if int(generated) == 0:  # pragma: no cover - uuid5 cannot produce this
        raise GcpActionError("derived a zero requestId, which Google rejects")
    return generated


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What one Compute call did. `ok=False` with `attempted=True` is a REAL
    failure; `attempted=False` means the step never ran (an earlier step failed)."""

    operation: str
    attempted: bool
    ok: bool
    detail: str
    request_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class KillSwitchOutcome:
    """The result of a stop-and-release, per step.

    PARTIAL SUCCESS IS A FIRST-CLASS STATE, not a collapsed boolean. "The VM
    stopped but its public IP is still attached" and "nothing happened at all"
    demand different follow-up, and a caller that could only see success/failure
    would report the first as a failure and invite a retry that re-stops an
    already-stopped VM while never revealing what actually remains undone.
    """

    steps: tuple[StepOutcome, ...] = field(default_factory=tuple)

    @property
    def stopped(self) -> bool:
        return any(s.operation == OP_STOP and s.ok for s in self.steps)

    @property
    def ip_released(self) -> bool:
        return any(s.operation == OP_RELEASE_IP and s.ok for s in self.steps)

    @property
    def fully_succeeded(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps if s.attempted) \
            and all(s.attempted for s in self.steps)

    @property
    def partial(self) -> bool:
        """Something changed on the customer's infrastructure and something did not."""
        return any(s.ok for s in self.steps) and not self.fully_succeeded

    def summary(self) -> str:
        """A human-readable line that never overstates what happened."""
        parts = []
        for s in self.steps:
            if not s.attempted:
                parts.append(f"{s.operation}: NOT ATTEMPTED ({s.detail})")
            elif s.ok:
                parts.append(f"{s.operation}: ok")
            else:
                parts.append(f"{s.operation}: FAILED ({s.detail})")
        return "; ".join(parts)


class GcpKillSwitchExecutor:
    """Mints a federated credential and performs the two Compute calls.

    Holds its own STS exchange rather than reusing `WifHealthProbe`'s. That is a
    deliberate duplication: the probe (23d339c) is a frozen, separately-proven
    component and this pass does not modify it. Consolidating the two behind one
    federation client is a worthwhile follow-up, and is NOT done here because
    refactoring a proven safety component to add a mutating caller is the wrong
    order to do those two things in.
    """

    def __init__(
        self,
        *,
        key: WifSigningKey,
        issuer_base_url: str,
        environment: str,
        http_client_factory: Any,
    ) -> None:
        self._key = key
        self._issuer_base_url = issuer_base_url
        self._environment = environment
        self._client_factory = http_client_factory

    async def stop_and_release(
        self,
        *,
        row: GcpWifConnectionRow,
        project: str,
        zone: str,
        instance: str,
        hitl_id: UUID | None,
        access_config: str = DEFAULT_ACCESS_CONFIG,
        network_interface: str = DEFAULT_NETWORK_INTERFACE,
    ) -> KillSwitchOutcome:
        """Stop the instance, then drop its external access config.

        ORDERING. Stop first, then release. The stop is the action that actually
        contains the runaway workload, so it goes first and is never made to wait
        on the more cosmetic call. It also means a failure of the second step
        leaves the customer in the SAFER partial state - machine stopped, IP still
        attached - rather than the reverse.

        Google does not document whether `deleteAccessConfig` is permitted on a
        TERMINATED instance (checked 2026-09-04), so that is UNVERIFIED. The code
        does not assume it works: a refusal there is surfaced as a real,
        actionable partial failure rather than swallowed, which is the correct
        behaviour under either answer.
        """
        if hitl_id is None:
            raise MissingIdempotencyAnchor(
                "no hitl_id is available, so no retry-stable requestId can be "
                "derived. This action mutates customer infrastructure and runs "
                "under a HITL replay path that retries on transient failure; "
                "without a stable idempotency key Google's deduplication cannot "
                "protect that retry. Refusing rather than falling back to "
                "correlation_id, which is minted fresh per approval attempt."
            )

        token = await self._federated_token(row)

        stop = await self._post(
            url=f"{COMPUTE_API_BASE}/projects/{project}/zones/{zone}"
                f"/instances/{instance}/stop",
            params={"requestId": str(request_id_for(hitl_id, OP_STOP))},
            token=token,
            operation=OP_STOP,
        )
        if not stop.ok:
            # Do NOT attempt the IP release against an instance we could not
            # stop. The failure is reported with the second step explicitly
            # marked not-attempted, so the caller can see exactly how far this
            # got rather than inferring it.
            return KillSwitchOutcome(steps=(
                stop,
                StepOutcome(
                    operation=OP_RELEASE_IP, attempted=False, ok=False,
                    detail="skipped because the instance could not be stopped",
                ),
            ))

        release = await self._post(
            url=f"{COMPUTE_API_BASE}/projects/{project}/zones/{zone}"
                f"/instances/{instance}/deleteAccessConfig",
            params={
                "accessConfig": access_config,
                "networkInterface": network_interface,
                "requestId": str(request_id_for(hitl_id, OP_RELEASE_IP)),
            },
            token=token,
            operation=OP_RELEASE_IP,
            # A 404 here is SUCCESS, not a failure: for an ephemeral address the
            # stop above already released it and there may be no access config
            # left to remove. The desired end state - no external route - holds.
            ok_statuses=(200, 404),
        )
        return KillSwitchOutcome(steps=(stop, release))

    # -- internals ----------------------------------------------------------

    async def _federated_token(self, row: GcpWifConnectionRow) -> str:
        assertion = mint_id_token(
            key=self._key,
            issuer=issuer_url(self._issuer_base_url, row.issuer_slug),
            org_id=row.org_id,
            audience=row.audience,
            environment=self._environment,
        )
        payload = {
            "audience": row.audience,
            "grantType": _GRANT_TYPE,
            "requestedTokenType": _REQUESTED_TOKEN_TYPE,
            "scope": _SCOPE,
            "subjectTokenType": _SUBJECT_TOKEN_TYPE,
            "subjectToken": assertion,
        }
        try:
            async with self._client_factory(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(STS_TOKEN_URL, json=payload)
        except Exception as exc:  # noqa: BLE001
            raise GcpActionError(f"could not reach Google STS: {exc}") from exc

        if resp.status_code != 200:
            raise GcpActionError(
                f"STS refused the federation assertion ({resp.status_code}): "
                f"{resp.text[:300]}"
            )
        body = resp.json() if callable(getattr(resp, "json", None)) else {}
        access = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(access, str) or not access:
            raise GcpActionError("STS returned 200 with no access_token")
        return access

    async def _post(
        self,
        *,
        url: str,
        params: dict[str, str],
        token: str,
        operation: str,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> StepOutcome:
        """One Compute call. NEVER raises - every outcome becomes a StepOutcome.

        That is deliberate: a raised exception here would collapse a PARTIAL
        result (stopped but not released) into a bare failure and lose the fact
        that the customer's machine is already down. The caller needs both facts.
        """
        request_id = UUID(params["requestId"])
        try:
            async with self._client_factory(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    url, params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(
                operation=operation, attempted=True, ok=False,
                detail=f"transport failure: {exc}", request_id=request_id,
            )

        if resp.status_code in ok_statuses:
            return StepOutcome(
                operation=operation, attempted=True, ok=True,
                detail=f"HTTP {resp.status_code}", request_id=request_id,
            )
        return StepOutcome(
            operation=operation, attempted=True, ok=False,
            detail=f"HTTP {resp.status_code}: {resp.text[:300]}",
            request_id=request_id,
        )
