"""The two-legged health probe's classification matrix.

The case that justifies the whole design is
`test_removed_iam_binding_is_caught_because_the_probe_reaches_compute`: STS
succeeds, Compute returns 403, and the probe must report a NON-healthy,
authorization-layer failure. A probe that stopped at the token exchange would
report `valid` there — a false healthy on the most likely real-world failure.

The scripted HTTP client returns real response objects, so these tests exercise
the actual classifier rather than a mock of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from skylize.app.gcp.keys import load_wif_signing_key
from skylize.app.gcp.probe import STS_TOKEN_URL, WifHealthProbe
from skylize.config import Settings
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    InMemoryGcpWifRepository,
)

BASE = "https://oidc.example.com"
FEDERATED_TOKEN = "ya29.FEDERATED-ACCESS-TOKEN-SHOULD-NEVER-BE-PERSISTED"


def _settings() -> Settings:
    return Settings(
        backend="memory", wif_issuer_base_url=BASE, wif_environment="production"
    )


def _row(org: str = "org_acme", state: str = "unverified") -> GcpWifConnectionRow:
    now = datetime.now(timezone.utc)
    return GcpWifConnectionRow(
        conn_id=uuid.uuid4(),
        org_id=org,
        label="",
        issuer_slug="s" * 26,
        gcp_project_id="customer-prod",
        gcp_project_number="123456789",
        workload_identity_pool_id="skylize-pool",
        workload_identity_provider_id="skylize-provider",
        audience="//iam.googleapis.com/projects/123456789/locations/global/"
                 "workloadIdentityPools/skylize-pool/providers/skylize-provider",
        access_mode="direct",
        service_account_email=None,
        signing_key_id="wif-es256-v1",
        jwks_delivery="served",
        expected_jwks_key_id=None,
        connection_state=state,  # type: ignore[arg-type]
        state_reason=None,
        last_probe_at=None,
        last_probe_result=None,
        last_success_at=None,
        created_at=now,
        updated_at=now,
    )


def _target(row: GcpWifConnectionRow) -> GcpWifTargetRow:
    return GcpWifTargetRow(
        target_id=uuid.uuid4(),
        conn_id=row.conn_id,
        org_id=row.org_id,
        gcp_project_id="customer-prod",
        zone="us-central1-a",
        instance_name="runaway-trainer",
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )


class _ScriptedClient:
    """An httpx-shaped async client returning canned responses per host."""

    def __init__(self, sts: httpx.Response | Exception, compute=None) -> None:
        self._sts = sts
        self._compute = compute

    def __call__(self, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, **_kw) -> httpx.Response:
        assert url == STS_TOKEN_URL
        if isinstance(self._sts, Exception):
            raise self._sts
        return self._sts

    async def get(self, url: str, **_kw) -> httpx.Response:
        assert "compute.googleapis.com" in url
        if isinstance(self._compute, Exception):
            raise self._compute
        assert self._compute is not None, "Compute was called unexpectedly"
        return self._compute


def _resp(status: int, json_body=None, text: str = "") -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text)


def _sts_ok() -> httpx.Response:
    return _resp(200, {"access_token": FEDERATED_TOKEN, "expires_in": 3600})


async def _probe(row, *, sts, compute=None, targets=True):
    repo = InMemoryGcpWifRepository()
    await repo.insert(row)
    if targets:
        await repo.add_target(_target(row))
    probe = WifHealthProbe(
        repo=repo,
        key=load_wif_signing_key(_settings()),
        settings=_settings(),
        http_client_factory=_ScriptedClient(sts, compute),
    )
    outcome = await probe.probe_connection(row)
    return outcome, repo


# ---------------------------------------------------------------------------
# THE CASE THE DESIGN EXISTS FOR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_removed_iam_binding_is_caught_because_the_probe_reaches_compute() -> None:
    """STS healthy, Compute denied — the failure an STS-only probe cannot see.

    This is the most likely real-world breakage (IAM cleanup is routine; deleting
    a federation pool is not) and it is invisible at the trust layer. The probe
    must report the AUTHORIZATION layer, and must NOT say `revoked`: the
    federation is intact and telling the customer to rebuild it would be wrong.
    """
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=_sts_ok(), compute=_resp(403, text="denied"))

    assert outcome.healthy is False
    assert outcome.result == "compute_denied"
    assert outcome.state == "misconfigured"
    assert outcome.transient is False
    assert "do NOT re-create" in (outcome.reason or "")


@pytest.mark.asyncio
async def test_fully_healthy_federation_reports_ok_and_valid() -> None:
    row = _row()
    outcome, _ = await _probe(row, sts=_sts_ok(), compute=_resp(200, {"status": "RUNNING"}))
    assert outcome.healthy is True
    assert (outcome.result, outcome.state) == ("ok", "valid")


# ---------------------------------------------------------------------------
# Trust layer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_documented_invalid_grant_is_the_only_thing_that_writes_revoked() -> None:
    """`invalid_grant` is documented as an unreachable/unmatched issuer. On a
    connection that previously worked that means the pool or provider is gone —
    the one signal unambiguous enough to demand a rebuild."""
    row = _row(state="valid")
    outcome, _ = await _probe(
        row,
        sts=_resp(400, {"error": "invalid_grant", "error_description": "no pool"}),
    )
    assert (outcome.result, outcome.state) == ("sts_failed", "revoked")
    assert outcome.transient is False


@pytest.mark.parametrize(
    "code",
    ["unauthorized_client", "invalid_request", "access_denied", "", "unknown_thing"],
)
@pytest.mark.asyncio
async def test_undocumented_sts_errors_never_write_revoked(code: str) -> None:
    """Google does not document the error shape for a disabled pool, a failed
    attribute condition, or an audience mismatch (checked 2026-09-04).

    A wrong `revoked` sends a customer to rebuild an entire federation when the
    fix was one CEL expression, so every undocumented shape lands on
    `misconfigured` instead. This is the same asymmetry `mark_revoked_by_provider`
    enforces for OAuth grants.
    """
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=_resp(400, {"error": code}))
    assert outcome.result == "sts_failed"
    assert outcome.state == "misconfigured", (
        f"{code!r} is undocumented and must not be classified as revoked"
    )


# ---------------------------------------------------------------------------
# Transient faults must never overwrite a verdict
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sts",
    [
        _resp(500, {"error": "internal_failure"}),
        _resp(503, text="upstream unavailable"),
        _resp(429, {"error": "quota_exceeded"}),
    ],
)
@pytest.mark.asyncio
async def test_transient_sts_failures_carry_the_previous_state_forward(sts) -> None:
    """"We could not check" must never collapse into "there was nothing to find".

    A 5xx or a rate limit says nothing about the customer's configuration, so the
    connection keeps the state it had and the outcome is flagged transient.
    """
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=sts)
    assert outcome.transient is True
    assert outcome.state == "valid", "a transient fault downgraded a healthy state"
    assert outcome.result == "unreachable"


@pytest.mark.asyncio
async def test_network_failure_to_sts_is_transient_and_preserves_state() -> None:
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=httpx.ConnectError("dns failure"))
    assert (outcome.transient, outcome.state) == (True, "valid")


@pytest.mark.asyncio
async def test_compute_5xx_is_transient_and_preserves_state() -> None:
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=_sts_ok(), compute=_resp(500, text="boom"))
    assert outcome.result == "compute_unavailable"
    assert (outcome.transient, outcome.state) == (True, "valid")


@pytest.mark.asyncio
async def test_sts_200_without_a_token_is_transient_not_a_verdict() -> None:
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=_resp(200, {"expires_in": 3600}))
    assert (outcome.transient, outcome.state) == (True, "valid")


# ---------------------------------------------------------------------------
# Partial verification is not success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_connection_with_no_targets_cannot_be_called_healthy() -> None:
    """STS proves the trust; nothing proves the binding.

    Reporting this as `valid` is exactly the false-healthy this probe exists to
    prevent, so it gets its own result and an actionable reason.
    """
    row = _row()
    outcome, _ = await _probe(row, sts=_sts_ok(), targets=False)
    assert outcome.healthy is False
    assert (outcome.result, outcome.state) == ("no_targets", "unverified")
    assert "no enabled instance" in (outcome.reason or "")


@pytest.mark.asyncio
async def test_compute_404_is_treated_as_ambiguous_not_as_revocation() -> None:
    """Google returns 404 both for a deleted instance and, sometimes, for one the
    caller may not see. Either way the customer must act, and neither reading
    justifies tearing down the federation."""
    row = _row(state="valid")
    outcome, _ = await _probe(row, sts=_sts_ok(), compute=_resp(404, text="not found"))
    assert outcome.state == "misconfigured"
    assert outcome.state != "revoked"


# ---------------------------------------------------------------------------
# The probe is read-only, and leaks no credential
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_federated_token_is_ever_persisted() -> None:
    """`state_reason` is written verbatim to the database. A Google credential
    must never reach it — which is why the token is a separate return value from
    `_exchange` rather than a field on `ProbeOutcome`."""
    row = _row()
    repo = InMemoryGcpWifRepository()
    await repo.insert(row)
    await repo.add_target(_target(row))
    probe = WifHealthProbe(
        repo=repo,
        key=load_wif_signing_key(_settings()),
        settings=_settings(),
        http_client_factory=_ScriptedClient(_sts_ok(), _resp(200, {"status": "RUNNING"})),
    )
    outcome = await probe.probe_org(row.org_id)
    assert outcome is not None and outcome.healthy

    stored = await repo.get(row.org_id, "")
    assert stored is not None
    assert FEDERATED_TOKEN not in (stored.state_reason or "")
    assert stored.state_reason is None


@pytest.mark.asyncio
async def test_probe_issues_no_mutating_call() -> None:
    """A health check that could stop a machine would be a new incident class.

    The scripted client asserts the method used: `post` only to STS, `get` only
    to Compute. Any mutating verb would fail the assertion inside the client.
    """
    calls: list[tuple[str, str]] = []

    class _Recording(_ScriptedClient):
        async def post(self, url: str, **kw):
            calls.append(("POST", url))
            return await super().post(url, **kw)

        async def get(self, url: str, **kw):
            calls.append(("GET", url))
            return await super().get(url, **kw)

    row = _row()
    repo = InMemoryGcpWifRepository()
    await repo.insert(row)
    await repo.add_target(_target(row))
    probe = WifHealthProbe(
        repo=repo,
        key=load_wif_signing_key(_settings()),
        settings=_settings(),
        http_client_factory=_Recording(_sts_ok(), _resp(200, {"status": "RUNNING"})),
    )
    await probe.probe_connection(row)

    assert calls == [
        ("POST", STS_TOKEN_URL),
        (
            "GET",
            "https://compute.googleapis.com/compute/v1/projects/customer-prod"
            "/zones/us-central1-a/instances/runaway-trainer",
        ),
    ]
    assert not any("/stop" in url for _, url in calls), "the probe must never stop a VM"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_org_persists_the_outcome_and_advances_success_only_on_ok() -> None:
    row = _row()
    repo = InMemoryGcpWifRepository()
    await repo.insert(row)
    await repo.add_target(_target(row))

    def _probe_with(sts, compute):
        return WifHealthProbe(
            repo=repo,
            key=load_wif_signing_key(_settings()),
            settings=_settings(),
            http_client_factory=_ScriptedClient(sts, compute),
        )

    await _probe_with(_sts_ok(), _resp(200, {"status": "RUNNING"})).probe_org(row.org_id)
    healthy = await repo.get(row.org_id, "")
    assert healthy is not None and healthy.connection_state == "valid"
    assert healthy.last_success_at is not None
    first_success = healthy.last_success_at

    await _probe_with(_sts_ok(), _resp(403, text="denied")).probe_org(row.org_id)
    broken = await repo.get(row.org_id, "")
    assert broken is not None
    assert broken.connection_state == "misconfigured"
    assert broken.last_probe_result == "compute_denied"
    assert broken.last_success_at == first_success, (
        "a failed probe advanced last_success_at, which would make a broken "
        "connection look recently healthy to a staleness alert"
    )


@pytest.mark.asyncio
async def test_probe_org_returns_none_when_the_org_has_no_connection() -> None:
    repo = InMemoryGcpWifRepository()
    probe = WifHealthProbe(
        repo=repo,
        key=load_wif_signing_key(_settings()),
        settings=_settings(),
        http_client_factory=_ScriptedClient(_sts_ok()),
    )
    assert await probe.probe_org("org_nobody") is None
