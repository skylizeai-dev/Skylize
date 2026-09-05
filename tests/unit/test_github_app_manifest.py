"""The App manifest IS Tier 1. These tests are the tripwire on it.

`integration_inputs.md` §2.4's Tier 1 guarantee — repository deletion,
branch-protection editing, collaborator changes and secret modification are
IMPOSSIBLE rather than gated — rests entirely on which permissions the App
manifest requests. There is no runtime code enforcing it, by design: a permission
never requested cannot be misused by any later refactor, which is exactly what
makes the guarantee stronger than a gate.

The corollary is that the guarantee can be destroyed by a one-line edit to a JSON
file, with no test failing anywhere else in the suite. That is what these tests
exist to prevent. A future author who adds `administration` here is reversing an
owner decision recorded on 2026-09-05 and silently removing the strongest property
in the GitHub design.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path(__file__).resolve().parents[2] / "config" / "github_app_manifest.json"

#: The exact Tier 1 set, from integration_inputs.md 2.4 Q2.4b.1 (APPROVED).
EXPECTED_PERMISSIONS = {"contents": "write", "metadata": "read"}

#: Any of these turns a structural impossibility back into something that needs a
#: runtime gate — or worse, into an ungated capability.
FORBIDDEN = {
    "administration",   # repo deletion, ruleset editing, collaborator changes
    "secrets",          # CI credential tampering
    "actions",          # workflow run / variable tampering
    "environments",     # environment secret tampering
    "workflows",        # workflow file writes
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_manifest_is_valid_json_and_present() -> None:
    assert MANIFEST.exists(), f"{MANIFEST} is missing; Tier 1 has no definition"
    json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_permissions_are_exactly_the_tier1_set(manifest: dict) -> None:
    """Exact equality, not a subset check.

    A subset check would pass if someone ADDED a permission, which is the whole
    failure mode being guarded. Widening requires a new 2.4 approval and a
    deliberate edit to this test.
    """
    assert manifest["default_permissions"] == EXPECTED_PERMISSIONS, (
        "the App manifest's permissions changed. This is integration_inputs.md "
        "2.4's Tier 1 guarantee. Widening it makes repository deletion or "
        "branch-protection editing POSSIBLE where they were previously "
        "impossible, and needs a new owner approval - not just a green test."
    )


@pytest.mark.parametrize("perm", sorted(FORBIDDEN))
def test_forbidden_permission_absent(manifest: dict, perm: str) -> None:
    assert perm not in manifest["default_permissions"], (
        f"manifest requests {perm!r}, which 2.4 Tier 1 forbids. "
        "'administration' in particular would let an agent delete a repository "
        "AND edit the branch-protection ruleset the connection-state probe "
        "depends on - collapsing Tier 1 and Tier 2 at once."
    )


def test_contents_is_write_and_that_is_deliberate(manifest: dict) -> None:
    """contents:write is unavoidable for PR-branch creation, and it is indivisible.

    Verified live 2026-09-05: creating a ref, pushing commits, DELETING a ref and
    merging a PR are all the same `contents: write` permission. There is no
    `write-except-delete`. This is precisely why 2.4 Q2.4b.2 chose verb-surface
    minimalism - Skylize never builds a delete-branch verb - rather than relying
    on scope to withhold deletion, which is impossible.
    """
    assert manifest["default_permissions"]["contents"] == "write"


def test_no_events_subscribed_yet(manifest: dict) -> None:
    """The uninstall webhook (Q2.4e) is approved but NOT built this pass.

    Subscribing to `installation` with no ingress to receive it would only produce
    failing deliveries on GitHub's side. The event is added in the same pass that
    adds the endpoint.
    """
    assert manifest["default_events"] == [], (
        "events are subscribed but no webhook ingress exists yet (2.4 Q2.4e is "
        "next pass). Add the event and the endpoint together."
    )


def test_app_is_not_public_by_default(manifest: dict) -> None:
    """A public App can be installed by anyone who finds it.

    Skylize's App is installed by customers Skylize has onboarded; there is no
    reason for it to be listed publicly at this stage, and a public App widens who
    can create installations that `github_app_installations` would then have to
    reject.
    """
    assert manifest["public"] is False


def test_description_states_what_the_app_cannot_do(manifest: dict) -> None:
    """The install screen is the one place a customer sees the guarantee.

    GitHub shows the description and the permission list at install time. Since
    Tier 1 is defined by ABSENCE, and absence is invisible, the description is
    where it becomes legible to the person clicking Install.
    """
    desc = manifest["description"].lower()
    assert "cannot delete" in desc
    assert "branch protection" in desc


def test_registration_script_shares_the_forbidden_list() -> None:
    """Both paths that could widen Tier 1 must refuse, not just this test.

    The script is what an operator actually runs; a check that lived only in the
    test suite would not stop a hand-edited manifest being registered.
    """
    from scripts.register_github_app import FORBIDDEN_PERMISSIONS

    assert set(FORBIDDEN_PERMISSIONS) <= FORBIDDEN
    assert "administration" in FORBIDDEN_PERMISSIONS
    assert "secrets" in FORBIDDEN_PERMISSIONS
