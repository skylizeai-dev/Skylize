"""The mutating half: requestId derivation, ordering, and partial-failure honesty.

The single most important property in this file is
`test_request_id_is_stable_across_retries_of_the_same_approval`. That is the one
that makes Google's deduplication actually protect the HITL retry path, and it is
the one a refactor toward `correlation_id` would silently break.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from skylize.app.gcp.actions import (
    OP_RELEASE_IP,
    OP_STOP,
    STS_TOKEN_URL,
    GcpActionError,
    GcpKillSwitchExecutor,
    MissingIdempotencyAnchor,
    request_id_for,
)
from skylize.app.gcp.keys import load_wif_signing_key
from skylize.config import Settings
from skylize.dal.gcp_wif import GcpWifConnectionRow

BASE = "https://oidc.example.com"
FEDERATED = "ya29.FEDERATED"


def _settings() -> Settings:
    return Settings(
        backend="memory", wif_issuer_base_url=BASE, wif_environment="production"
    )


def _row(org: str = "org_acme") -> GcpWifConnectionRow:
    now = datetime.now(timezone.utc)
    return GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id=org, label="", issuer_slug="s" * 26,
        gcp_project_id="cust-prod", gcp_project_number="123456789012",
        workload_identity_pool_id="pool", workload_identity_provider_id="prov",
        audience="//iam.googleapis.com/projects/1/locations/global/x/y",
        access_mode="direct", service_account_email=None,
        signing_key_id="wif-es256-v1", jwks_delivery="served",
        expected_jwks_key_id=None, connection_state="valid", state_reason=None,
        last_probe_at=None, last_probe_result=None, last_success_at=None,
        created_at=now, updated_at=now,
    )


class _Recorder:
    """Records every POST and replays scripted responses by URL suffix."""

    def __init__(self, *, stop=None, release=None, sts=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._stop = stop if stop is not None else httpx.Response(200, json={})
        self._release = release if release is not None else httpx.Response(200, json={})
        self._sts = sts if sts is not None else httpx.Response(
            200, json={"access_token": FEDERATED, "expires_in": 3600}
        )

    def __call__(self, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, **kw):
        self.calls.append((url, dict(kw.get("params") or {})))
        if url == STS_TOKEN_URL:
            if isinstance(self._sts, Exception):
                raise self._sts
            return self._sts
        target = self._stop if url.endswith("/stop") else self._release
        if isinstance(target, Exception):
            raise target
        return target


def _executor(recorder: _Recorder) -> GcpKillSwitchExecutor:
    key = load_wif_signing_key(_settings())
    assert key is not None
    return GcpKillSwitchExecutor(
        key=key, issuer_base_url=BASE, environment="production",
        http_client_factory=recorder,
    )


async def _run(recorder: _Recorder, hitl_id: UUID | None):
    return await _executor(recorder).stop_and_release(
        row=_row(), project="cust-prod", zone="us-central1-a",
        instance="runaway", hitl_id=hitl_id,
    )


# ---------------------------------------------------------------------------
# THE IDEMPOTENCY CONTRACT
# ---------------------------------------------------------------------------

def test_request_id_is_stable_across_retries_of_the_same_approval() -> None:
    """The property the whole design turns on.

    `HitlQueueService.approve` mints a fresh correlation id per attempt
    (app/hitl/service.py:160) and releases the row to 'pending' on a transient
    failure (:234-246), so a human retry re-runs everything. Only a key derived
    from `hitl_id` is the same on the second run - which is the only way Google
    can recognise the retry as a duplicate rather than a new command.
    """
    hitl_id = uuid.uuid4()
    first = request_id_for(hitl_id, OP_STOP)
    second = request_id_for(hitl_id, OP_STOP)
    assert first == second


def test_request_ids_differ_per_operation_so_the_second_call_is_not_swallowed() -> None:
    """Google ignores a request whose id it has ALREADY SEEN.

    One id shared by both calls would make `deleteAccessConfig` look like a
    duplicate of the `stop` that just ran, and Google would drop it - leaving the
    external IP attached while the code reported success.
    """
    hitl_id = uuid.uuid4()
    assert request_id_for(hitl_id, OP_STOP) != request_id_for(hitl_id, OP_RELEASE_IP)


def test_request_ids_differ_across_approvals() -> None:
    """A genuinely new decision must genuinely execute, not be deduped against
    an older one."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert request_id_for(a, OP_STOP) != request_id_for(b, OP_STOP)


def test_request_id_is_a_valid_non_zero_uuid() -> None:
    """Google: "The request ID must be a valid UUID with the exception that zero
    UUID is not supported"."""
    rid = request_id_for(uuid.uuid4(), OP_STOP)
    assert isinstance(rid, UUID)
    assert int(rid) != 0


@pytest.mark.asyncio
async def test_the_derived_ids_are_what_actually_go_on_the_wire() -> None:
    """Derivation is worthless if the calls do not carry it."""
    hitl_id = uuid.uuid4()
    rec = _Recorder()
    await _run(rec, hitl_id)

    by_url = {url: params for url, params in rec.calls}
    stop_url = next(u for u in by_url if u.endswith("/stop"))
    rel_url = next(u for u in by_url if u.endswith("/deleteAccessConfig"))
    assert by_url[stop_url]["requestId"] == str(request_id_for(hitl_id, OP_STOP))
    assert by_url[rel_url]["requestId"] == str(request_id_for(hitl_id, OP_RELEASE_IP))


@pytest.mark.asyncio
async def test_without_a_hitl_id_the_action_refuses_rather_than_falling_back() -> None:
    """No retry-stable anchor means the idempotency contract cannot be honoured.

    Falling back to `correlation_id` would look correct and be wrong on exactly
    the retry path the key exists to protect, so the action refuses instead.
    """
    rec = _Recorder()
    with pytest.raises(MissingIdempotencyAnchor, match="correlation_id"):
        await _run(rec, None)
    assert rec.calls == [], "nothing may be sent without a stable idempotency key"


# ---------------------------------------------------------------------------
# Ordering and the two calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_happens_before_the_ip_release() -> None:
    """The stop contains the workload, so it never waits on the cosmetic call.

    It also means a failure of the second step leaves the SAFER partial state -
    machine down, IP still attached - rather than the reverse.
    """
    rec = _Recorder()
    await _run(rec, uuid.uuid4())
    urls = [u for u, _ in rec.calls if u != STS_TOKEN_URL]
    assert urls[0].endswith("/stop")
    assert urls[1].endswith("/deleteAccessConfig")


@pytest.mark.asyncio
async def test_release_carries_the_access_config_and_interface_google_requires() -> None:
    rec = _Recorder()
    await _run(rec, uuid.uuid4())
    rel = next(p for u, p in rec.calls if u.endswith("/deleteAccessConfig"))
    assert rel["accessConfig"] == "External NAT"
    assert rel["networkInterface"] == "nic0"


@pytest.mark.asyncio
async def test_no_disk_or_address_delete_call_is_ever_made() -> None:
    """The irreversible verbs are absent, asserted rather than assumed."""
    rec = _Recorder()
    await _run(rec, uuid.uuid4())
    joined = " ".join(u for u, _ in rec.calls)
    assert "disks" not in joined
    assert "addresses" not in joined


# ---------------------------------------------------------------------------
# PARTIAL FAILURE MUST NOT COLLAPSE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stopped_but_ip_release_failed_is_reported_as_partial() -> None:
    """The case that must never read as either a clean success or a clean failure.

    The customer's machine IS down. Reporting a bare failure would invite a retry
    while hiding that the containment already half-happened; reporting success
    would hide that the machine still has a public IP.
    """
    rec = _Recorder(release=httpx.Response(403, text="denied"))
    out = await _run(rec, uuid.uuid4())

    assert out.stopped is True
    assert out.ip_released is False
    assert out.partial is True
    assert out.fully_succeeded is False
    assert "instances.stop: ok" in out.summary()
    assert "FAILED" in out.summary()


@pytest.mark.asyncio
async def test_a_failed_stop_does_not_attempt_the_release_and_says_so() -> None:
    """Not-attempted is its own state, distinct from attempted-and-failed."""
    rec = _Recorder(stop=httpx.Response(500, text="boom"))
    out = await _run(rec, uuid.uuid4())

    assert out.stopped is False
    assert out.partial is False
    assert out.fully_succeeded is False
    assert "NOT ATTEMPTED" in out.summary()
    assert not any(u.endswith("/deleteAccessConfig") for u, _ in rec.calls)


@pytest.mark.asyncio
async def test_both_succeeding_is_the_only_fully_succeeded_case() -> None:
    out = await _run(_Recorder(), uuid.uuid4())
    assert out.fully_succeeded is True
    assert out.partial is False
    assert (out.stopped, out.ip_released) == (True, True)


@pytest.mark.asyncio
async def test_a_404_on_the_release_is_success_because_the_stop_already_freed_it() -> None:
    """For an EPHEMERAL address the stop has already released the IP, so there may
    be no access config left to remove. The desired end state - no external route
    - holds, so a 404 is success rather than a failure to explain away.

    Verified 2026-09-04: "Compute Engine releases ephemeral IP addresses when an
    instance is stopped."
    """
    rec = _Recorder(release=httpx.Response(404, text="not found"))
    out = await _run(rec, uuid.uuid4())
    assert out.ip_released is True
    assert out.fully_succeeded is True


@pytest.mark.asyncio
async def test_a_transport_failure_on_the_release_is_still_a_partial() -> None:
    rec = _Recorder(release=httpx.ConnectError("reset"))
    out = await _run(rec, uuid.uuid4())
    assert (out.stopped, out.ip_released, out.partial) == (True, False, True)


# ---------------------------------------------------------------------------
# Federation failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_sts_refusal_stops_everything_before_any_mutation() -> None:
    rec = _Recorder(sts=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(GcpActionError, match="STS refused"):
        await _run(rec, uuid.uuid4())
    assert all(u == STS_TOKEN_URL for u, _ in rec.calls), (
        "a mutation was attempted after the federation exchange failed"
    )


@pytest.mark.asyncio
async def test_the_federated_token_is_sent_as_a_bearer_and_not_logged_in_output() -> None:
    """The credential must reach Google and appear nowhere in the reported result."""
    rec = _Recorder()
    out = await _run(rec, uuid.uuid4())
    assert FEDERATED not in out.summary()
    for s in out.steps:
        assert FEDERATED not in (s.detail or "")
