"""The connection-state probe: does §2.4's Tier 2 guarantee actually hold?

The single most important test in this file is
`test_bypass_is_detected_even_though_bypass_actors_is_redacted`. It encodes an
empirically discovered fact that the obvious implementation gets wrong:

    `[EMPIRICALLY VERIFIED]` 2026-09-05 against a live repository. An installation
    token reading a ruleset that HAS a bypass actor configured sees
    `"bypass_actors": null` — REDACTED, not empty — while `current_user_can_bypass`
    correctly reports the caller's own verdict.

A probe matching app ids against `bypass_actors` would therefore read `null` every
time, conclude "not bypassed", and record `protected` for a repository the App can
push straight through. These tests pin the correct signal so that regression cannot
land silently.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from skylize.app.github.probe import (
    PUSH_RESTRICTING_RULES,
    GithubConnectionProbe,
    _covers_branch,
    _restricts_pushes,
)
from skylize.app.github.tokens import GithubAppTokenMinter

INSTALL_ID = 4242
OWNER, REPO, BRANCH = "acme", "widgets", "main"


class _FakeMinter(GithubAppTokenMinter):
    """Yields a token without touching crypto or the network.

    Subclasses the real minter rather than duck-typing it so a signature change on
    `mint_installation_token` breaks these tests instead of silently diverging.
    """

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def mint_installation_token(self, **kw: Any) -> Any:  # type: ignore[override]
        self.calls.append(kw)
        if self.fail is not None:
            raise self.fail

        class _T:
            token = "ghs_fake"

        return _T()


def _probe(routes: dict[str, Any], *, minter: _FakeMinter | None = None):
    """Build a probe whose HTTP layer is a table of path-suffix -> Response."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        for suffix, resp in routes.items():
            if path.endswith(suffix):
                return resp() if callable(resp) else resp
        return httpx.Response(404, json={"message": "no route", "path": path})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GithubConnectionProbe(minter=minter or _FakeMinter(), client=client)


def _ruleset(
    rid: int = 1,
    *,
    enforcement: str = "active",
    include: list[str] | None = None,
    rules: list[str] | None = None,
    can_bypass: str = "never",
    bypass_actors: Any = None,
) -> dict[str, Any]:
    return {
        "id": rid,
        "name": f"rs-{rid}",
        "enforcement": enforcement,
        "bypass_actors": bypass_actors,
        "current_user_can_bypass": can_bypass,
        "rules": [{"type": t} for t in (rules or ["update", "non_fast_forward"])],
        "conditions": {
            "ref_name": {"include": include or [f"refs/heads/{BRANCH}"], "exclude": []}
        },
    }


async def _run(routes: dict[str, Any], *, minter: _FakeMinter | None = None):
    return await _probe(routes, minter=minter).probe_repo(
        installation_id=INSTALL_ID, owner=OWNER, repo=REPO, branch=BRANCH
    )


# ---------------------------------------------------------------------------
# THE THREE PROTECTION STATES — the STOP-gate requirement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_protected_when_active_ruleset_covers_branch_and_no_bypass() -> None:
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}]),
        "/rulesets/1": httpx.Response(200, json=_ruleset(1, can_bypass="never")),
    })
    assert out.state == "protected"
    assert out.result == "ok"
    assert out.terminal is True
    assert out.covering_ruleset_ids == (1,)


@pytest.mark.asyncio
async def test_unprotected_when_no_rulesets_and_no_classic_protection() -> None:
    out = await _run({
        "/rulesets": httpx.Response(200, json=[]),
        f"/branches/{BRANCH}": httpx.Response(200, json={"protected": False}),
    })
    assert out.state == "unprotected"
    assert out.result == "no_ruleset"
    assert out.terminal is True
    assert "contents:write alone therefore permits a direct push" in (out.reason or "")


@pytest.mark.asyncio
async def test_bypass_is_detected_even_though_bypass_actors_is_redacted() -> None:
    """THE load-bearing test. See the module docstring.

    This models exactly what a live installation token sees: `bypass_actors` is
    `null` (GitHub redacts it) while `current_user_can_bypass` says "always". A
    probe reading the former concludes "protected"; the correct probe concludes
    "bypass_granted".
    """
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 9}]),
        "/rulesets/9": httpx.Response(
            200,
            json=_ruleset(9, can_bypass="always", bypass_actors=None),
        ),
    })
    assert out.state == "bypass_granted", (
        "the probe fell back to bypass_actors (which is redacted to null for an "
        "installation token) and wrongly reported protection"
    )
    assert out.result == "bypass_granted"
    assert out.terminal is True


@pytest.mark.asyncio
async def test_unprotected_and_bypass_granted_are_never_the_same_state() -> None:
    """§2.4 requires these be distinguishable; the STOP gate forbids collapsing them.

    bypass_granted is strictly worse: the customer believes they are protected.
    """
    none = await _run({
        "/rulesets": httpx.Response(200, json=[]),
        f"/branches/{BRANCH}": httpx.Response(200, json={"protected": False}),
    })
    bypass = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}]),
        "/rulesets/1": httpx.Response(200, json=_ruleset(1, can_bypass="always")),
    })
    assert none.state != bypass.state
    assert {none.state, bypass.state} == {"unprotected", "bypass_granted"}


@pytest.mark.asyncio
async def test_partial_bypass_counts_as_bypass() -> None:
    """'pull_requests_only' still falsifies "nothing can push directly"."""
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 3}]),
        "/rulesets/3": httpx.Response(
            200, json=_ruleset(3, can_bypass="pull_requests_only")
        ),
    })
    assert out.state == "bypass_granted"


@pytest.mark.asyncio
async def test_one_bypassable_ruleset_among_several_wins() -> None:
    """Any bypass path defeats the guarantee; the verdict must not be a majority vote."""
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
        "/rulesets/1": httpx.Response(200, json=_ruleset(1, can_bypass="never")),
        "/rulesets/2": httpx.Response(200, json=_ruleset(2, can_bypass="always")),
    })
    assert out.state == "bypass_granted"


# ---------------------------------------------------------------------------
# Rulesets that exist but deliver nothing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_ruleset_is_not_protection() -> None:
    """'evaluate' and 'disabled' rulesets block nothing."""
    for mode in ("evaluate", "disabled"):
        out = await _run({
            "/rulesets": httpx.Response(200, json=[{"id": 1}]),
            "/rulesets/1": httpx.Response(200, json=_ruleset(1, enforcement=mode)),
            f"/branches/{BRANCH}": httpx.Response(200, json={"protected": False}),
        })
        assert out.state == "unprotected", mode


@pytest.mark.asyncio
async def test_ruleset_not_covering_the_branch_is_not_protection() -> None:
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}]),
        "/rulesets/1": httpx.Response(
            200, json=_ruleset(1, include=["refs/heads/release/*"])
        ),
        f"/branches/{BRANCH}": httpx.Response(200, json={"protected": False}),
    })
    assert out.state == "unprotected"


@pytest.mark.asyncio
async def test_ruleset_with_no_push_restricting_rule_is_not_protection() -> None:
    """A signed-commits-only ruleset is active and covering but blocks no push.

    Counting it would overstate what the customer actually has.
    """
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}]),
        "/rulesets/1": httpx.Response(
            200, json=_ruleset(1, rules=["required_signatures", "required_linear_history"])
        ),
        f"/branches/{BRANCH}": httpx.Response(200, json={"protected": False}),
    })
    assert out.state == "unprotected"
    assert out.result == "no_ruleset"


def test_push_restricting_rule_set_is_exactly_the_three() -> None:
    assert PUSH_RESTRICTING_RULES == {"update", "non_fast_forward", "pull_request"}


# ---------------------------------------------------------------------------
# Classic protection: detectable, but its bypass is NOT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classic_protection_is_unprotected_plus_classic_only() -> None:
    """Deliberately pessimistic.

    Classic protection's bypass configuration needs administration:read, which
    Tier 1 forbids. Recording `protected` would assert a guarantee never verified —
    the exact outcome this subsystem exists to prevent. The distinction survives in
    `last_probe_result`.
    """
    out = await _run({
        "/rulesets": httpx.Response(200, json=[]),
        f"/branches/{BRANCH}": httpx.Response(200, json={"protected": True}),
    })
    assert out.state == "unprotected"
    assert out.result == "classic_only"
    assert "administration:read" in (out.reason or "")


# ---------------------------------------------------------------------------
# Terminal vs transient — a transient failure must not overwrite a good state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uninstalled_is_terminal_revoked() -> None:
    from skylize.app.github.tokens import GithubInstallationGone

    out = await _run({}, minter=_FakeMinter(fail=GithubInstallationGone("gone")))
    assert out.state == "revoked"
    assert out.result == "installation_gone"
    assert out.terminal is True


@pytest.mark.asyncio
async def test_transient_mint_failure_is_non_terminal() -> None:
    from skylize.app.github.tokens import GithubTransientError

    out = await _run({}, minter=_FakeMinter(fail=GithubTransientError("503")))
    assert out.result == "unreachable"
    assert out.terminal is False, (
        "a transient failure must not be written as a protection verdict"
    )


@pytest.mark.asyncio
async def test_platform_key_failure_is_non_terminal_and_not_revoked() -> None:
    """A wrong platform key must not mark every tenant revoked at once."""
    from skylize.app.github.tokens import GithubTokenError

    out = await _run({}, minter=_FakeMinter(fail=GithubTokenError("401 platform")))
    assert out.state != "revoked"
    assert out.result == "auth_failed"
    assert out.terminal is False


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_http_listing_rulesets_is_non_terminal(code: int) -> None:
    out = await _run({"/rulesets": httpx.Response(code, json={})})
    assert out.result == "unreachable"
    assert out.terminal is False


@pytest.mark.asyncio
async def test_403_listing_rulesets_is_non_terminal() -> None:
    """Ruleset reads need only metadata:read, so a 403 is a config anomaly.

    It is emphatically NOT evidence about branch protection, so no protection
    verdict may be written from it.
    """
    out = await _run({"/rulesets": httpx.Response(403, json={})})
    assert out.result == "rulesets_unreadable"
    assert out.terminal is False


@pytest.mark.asyncio
async def test_an_unreadable_single_ruleset_refuses_to_conclude() -> None:
    """The ruleset we could not read might be the one granting bypass.

    Concluding `protected` from a partial view is how a false guarantee gets
    recorded.
    """
    out = await _run({
        "/rulesets": httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
        "/rulesets/1": httpx.Response(200, json=_ruleset(1)),
        "/rulesets/2": httpx.Response(500, json={}),
    })
    assert out.result == "rulesets_unreadable"
    assert out.terminal is False
    assert out.state != "protected"


@pytest.mark.asyncio
async def test_probe_narrows_the_token_to_the_single_repo() -> None:
    """Even the probe's own read must obey attenuation-only."""
    m = _FakeMinter()
    await _run({"/rulesets": httpx.Response(200, json=[])}, minter=m)
    assert m.calls[0]["repositories"] == [REPO]
    assert m.calls[0]["installation_id"] == INSTALL_ID


# ---------------------------------------------------------------------------
# Branch-matching helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "include,branch,expected",
    [
        (["refs/heads/main"], "main", True),
        (["~ALL"], "anything", True),
        (["~DEFAULT_BRANCH"], "main", True),
        (["refs/heads/release/*"], "release/1.2", True),
        (["refs/heads/release/*"], "main", False),
        (["refs/heads/develop"], "main", False),
    ],
)
def test_covers_branch(include: list[str], branch: str, expected: bool) -> None:
    assert _covers_branch({"conditions": {"ref_name": {"include": include}}}, branch) is expected


def test_exclude_wins_over_include() -> None:
    detail = {
        "conditions": {
            "ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/main"]}
        }
    }
    assert _covers_branch(detail, "main") is False
    assert _covers_branch(detail, "other") is True


def test_ruleset_with_no_conditions_applies_to_all_branches() -> None:
    assert _covers_branch({}, "main") is True


def test_restricts_pushes_requires_a_relevant_rule() -> None:
    assert _restricts_pushes({"rules": [{"type": "update"}]}) is True
    assert _restricts_pushes({"rules": [{"type": "pull_request"}]}) is True
    assert _restricts_pushes({"rules": [{"type": "required_signatures"}]}) is False
    assert _restricts_pushes({"rules": []}) is False
    assert _restricts_pushes({}) is False
