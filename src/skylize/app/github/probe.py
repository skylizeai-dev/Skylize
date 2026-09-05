"""Connection-state probe: is the customer's branch protection actually protecting?

THE PROBLEM THIS SOLVES
-----------------------
`integration_inputs.md` §2.4's Tier 2 guarantee — GitHub itself refuses a push to a
protected branch, so Skylize needs no runtime gate for it — is CONDITIONAL on
something Skylize does not control: the customer having configured a ruleset, and
not having granted this App a bypass on it. §2.4 Q2.4c states the consequence
plainly: "A customer with no ruleset on `main` gets zero Tier 2 protection, and
`contents: write` alone then permits a direct push to `main`."

Left unprobed, that is a guarantee asserted and never checked — the same failure
mode `app/gcp/probe.py` exists to prevent for the kill switch ("A kill switch that
discovers it has lost its authority at the instant of use is worse than no kill
switch, because it was relied upon"). Here the equivalent is worse in one respect:
nothing fails at the moment of use. The push simply succeeds, and nobody learns
that the protection everyone believed in was absent.

=============================================================================
WHY THIS READS `current_user_can_bypass` AND NEVER `bypass_actors`
=============================================================================
This is the most important fact in this module and it was established
empirically, not from documentation. It is stated at length because the obvious
implementation is the wrong one and would fail silently.

§2.4 Q2.4c's own table describes the degraded state as a ruleset that "lists the
Skylize App (or a team/role the App inherits) as a bypass actor", which reads like
an instruction to fetch the ruleset and look for the App in `bypass_actors`. That
implementation is broken:

    `[EMPIRICALLY VERIFIED]` 2026-09-05 against a live repository. The SAME
    ruleset, at the same moment, with one bypass actor configured
    (`RepositoryRole:5`, `bypass_mode: always`), read two ways:

      read by a USER token (repo admin, IS the bypass actor):
          "bypass_actors": [{"actor_id":5,"actor_type":"RepositoryRole",
                             "bypass_mode":"always"}]
          "current_user_can_bypass": "always"

      read by a GITHUB APP INSTALLATION TOKEN (contents:write + metadata:read,
      NOT the bypass actor):
          "bypass_actors": null            <-- REDACTED, not empty
          "current_user_can_bypass": "never"

`bypass_actors` is not visible to an installation token. A probe that matched app
ids against it would read `null` every single time, conclude "the App is not a
bypass actor", and record `protected` for a repository where the App may well be
able to bypass. That is a FALSE ASSERTION OF PROTECTION — precisely the outcome
this module exists to prevent — and it would have been invisible in testing
against any repo whose bypass list happened to be empty.

`current_user_can_bypass` is the correct signal, and it is strictly better than
matching ids even if `bypass_actors` were visible:

  * It is computed by GitHub FOR THE CALLING IDENTITY, so it is the answer to the
    question actually being asked ("can *this* credential bypass?") rather than an
    inference from configuration.
  * It resolves inheritance automatically. §2.4's parenthetical "or a team/role the
    App inherits" is a real hazard for id-matching — an App can bypass via a role
    or team without appearing by id — and this field accounts for it with no logic
    here.
  * The two-way read above proves it is actor-differentiated: identical
    configuration, different answer per caller. That is the property the probe
    needs.

Both `GET /rulesets` and `GET /rulesets/{id}` are readable under the Tier 1
permission set. `[LIVE-VERIFIED]` 2026-09-05,
docs.github.com/en/rest/authentication/permissions-required-for-github-apps: the
ruleset READ endpoints sit under **Metadata: read**, NOT `administration` — and
confirmed empirically the same day, HTTP 200 from an installation token holding
only `contents: write` + `metadata: read`. This is what makes the probe possible
without violating Tier 1; had reading rulesets required `administration: read`,
the probe and Tier 1 would have been in direct conflict and the design would have
needed to go back to the owner.

=============================================================================
WHY CLASSIC BRANCH PROTECTION IS RECORDED AS `unprotected` + 'classic_only'
=============================================================================
§2.4 Q2.4c says Tier 2 holds if the customer has "a ruleset (or classic
protection)". Rulesets are fully verifiable here. Classic protection is NOT:
reading `GET /repos/{owner}/{repo}/branches/{branch}/protection` — the endpoint
carrying the enforcement and bypass detail — requires `administration: read`,
which Tier 1 forbids requesting.

What IS readable is the `protected` boolean on `GET /repos/{owner}/{repo}/branches/
{branch}`. That tells us protection of SOME kind exists; it does not tell us
whether this App can bypass it, which is the question that decides between
`protected` and `bypass_granted`.

So such a repository is recorded `connection_state='unprotected'` with
`last_probe_result='classic_only'`. This is deliberately pessimistic. Recording
`protected` would assert a guarantee that was never verified, and this module's
entire reason for existing is that an unverified guarantee is worse than a known
gap. The distinction is not lost: `last_probe_result` preserves it, exactly as
migration 0024:78-86 argues its own probe-result column must
("records WHICH LAYER failed, which connection_state alone cannot express"), and
`state_reason` names the limitation in words an operator can act on.

A follow-up is flagged in the return value rather than silently swallowed: a
customer on classic-only protection cannot be given the Tier 2 assurance under the
approved Tier 1 permission set, and the owner may want to revisit whether
`administration: read` (read, not write — it cannot delete a repo or edit a rule)
is worth requesting to close that gap. That is an owner decision, not one this
module may make.

=============================================================================
A TRANSIENT FAILURE NEVER OVERWRITES A GOOD STATE
=============================================================================
Network faults, timeouts, 429 and 5xx record a probe result and a reason but carry
the existing `connection_state` forward untouched. "We could not check" must never
collapse into "there was nothing to find" — the rule
`app/credentials/oauth.py` draws between `RefreshUnavailable` and `GrantRevoked`
and `app/gcp/probe.py` restates verbatim. Only two signals here are terminal: a
404/410 on the installation (genuinely uninstalled -> 'revoked') and a successful
read of the customer's actual ruleset configuration.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ...dal.github_app import ConnectionState, GithubProbeResult
from .tokens import (
    GithubAppTokenMinter,
    GithubInstallationGone,
    GithubTokenError,
    GithubTransientError,
)

log = logging.getLogger(__name__)

#: Rule types that actually restrict pushing to a branch. A ruleset can exist and
#: be active while restricting nothing relevant — for instance one that only
#: requires a linear history or a signed commit. Such a ruleset does NOT deliver
#: the Tier 2 guarantee (§2.4 Tier 2 is specifically about push and force-push
#: being refused), so its presence must not be mistaken for protection.
#:
#: 'update'          - restricts pushes to matching refs
#: 'non_fast_forward'- blocks force pushes
#: 'pull_request'    - requires a PR, so direct pushes are refused
PUSH_RESTRICTING_RULES: frozenset[str] = frozenset({
    "update",
    "non_fast_forward",
    "pull_request",
})

#: Values of `current_user_can_bypass` meaning the caller CAN bypass. GitHub also
#: emits 'pull_requests_only', which is a partial bypass — treated as bypass here
#: because a partial bypass still means the customer's belief "nothing can push
#: directly" is false for this App.
_BYPASS_CAPABLE: frozenset[str] = frozenset({"always", "pull_requests_only"})


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """What one probe of one repository concluded.

    `state` is what should be written to `connection_state`; `terminal` says
    whether it may be written at all. A transient failure returns
    `terminal=False`, and the caller must then leave the stored state alone —
    the dataclass carries the distinction so a caller cannot lose it by only
    looking at `state`.
    """

    state: ConnectionState
    result: GithubProbeResult
    reason: str | None
    #: False for transient failures: record the result, do NOT change the state.
    terminal: bool = True
    #: Which ruleset ids were found to cover the branch. Diagnostic only.
    covering_ruleset_ids: tuple[int, ...] = ()


class GithubConnectionProbe:
    """Determines whether §2.4's Tier 2 guarantee actually holds for a repository.

    Stateless apart from its injected collaborators, so one instance is safe to
    share. It reads GitHub and returns a conclusion; it does not write the
    database — the caller does, inside a tenant session, which keeps this class
    free of any tenancy responsibility it could get wrong.
    """

    def __init__(
        self,
        *,
        minter: GithubAppTokenMinter,
        client: httpx.AsyncClient,
        api_base_url: str = "https://api.github.com",
    ) -> None:
        self._minter = minter
        self._client = client
        self._base = api_base_url.rstrip("/")

    async def probe_repo(
        self,
        *,
        installation_id: int,
        owner: str,
        repo: str,
        branch: str,
    ) -> ProbeOutcome:
        """Probe one repository's protection of one branch."""
        full = f"{owner}/{repo}"
        try:
            tok = await self._minter.mint_installation_token(
                installation_id=installation_id,
                repositories=[repo],
            )
        except GithubInstallationGone as exc:
            return ProbeOutcome(
                state="revoked",
                result="installation_gone",
                reason=str(exc),
                terminal=True,
            )
        except GithubTransientError as exc:
            return ProbeOutcome(
                state="unverified",
                result="unreachable",
                reason=f"transient failure minting a token: {exc}",
                terminal=False,
            )
        except GithubTokenError as exc:
            # Includes the 401 platform-key case, which is explicitly NOT the
            # tenant's fault and must not mark the tenant revoked.
            return ProbeOutcome(
                state="unverified",
                result="auth_failed",
                reason=str(exc),
                terminal=False,
            )

        headers = {
            "Authorization": f"token {tok.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # ---- Step 1: list the repository's rulesets. -----------------------
        # Deliberately the LIST endpoint rather than GET /rules/branches/{branch}.
        # The latter returns the rules "in effect", and whether it accounts for the
        # caller's bypass could not be verified for a bypassing App on the test
        # account available (a User account cannot add the Actions integration as
        # a bypass actor). If it DOES omit rules for a bypassing caller, a probe
        # built on it would see an empty list and report 'unprotected' for a
        # repository that is really 'bypass_granted' — collapsing exactly the two
        # states §2.4 requires be kept apart. Reading the rulesets themselves and
        # their own conditions avoids depending on that unverified semantics.
        try:
            resp = await self._client.get(
                f"{self._base}/repos/{full}/rulesets", headers=headers
            )
        except httpx.HTTPError as exc:
            return ProbeOutcome(
                state="unverified",
                result="unreachable",
                reason=f"could not list rulesets for {full}: {exc}",
                terminal=False,
            )

        if resp.status_code == 404:
            # The repo is gone, or the narrowed token cannot see it. Either way this
            # is not a statement about branch protection.
            return ProbeOutcome(
                state="unverified",
                result="rulesets_unreadable",
                reason=(
                    f"{full} returned 404 listing rulesets. The repository may "
                    "have been deleted or removed from the installation's "
                    "selected repositories."
                ),
                terminal=False,
            )
        if resp.status_code == 403:
            return ProbeOutcome(
                state="unverified",
                result="rulesets_unreadable",
                reason=(
                    f"HTTP 403 listing rulesets for {full}. Ruleset reads require "
                    "only metadata:read (verified 2026-09-05), so a 403 here means "
                    "the installation is missing even that, or the repository is "
                    "not in its selected set."
                ),
                terminal=False,
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            return ProbeOutcome(
                state="unverified",
                result="unreachable",
                reason=f"HTTP {resp.status_code} listing rulesets for {full}",
                terminal=False,
            )
        if resp.status_code != 200:
            return ProbeOutcome(
                state="unverified",
                result="rulesets_unreadable",
                reason=(
                    f"unexpected HTTP {resp.status_code} listing rulesets for "
                    f"{full}: {resp.text[:300]}"
                ),
                terminal=False,
            )

        try:
            listing = resp.json()
        except ValueError as exc:
            return ProbeOutcome(
                state="unverified",
                result="rulesets_unreadable",
                reason=f"unparseable ruleset listing for {full}: {exc}",
                terminal=False,
            )
        if not isinstance(listing, list):
            return ProbeOutcome(
                state="unverified",
                result="rulesets_unreadable",
                reason=f"ruleset listing for {full} was not a JSON array",
                terminal=False,
            )

        # ---- Step 2: inspect each ruleset's conditions and bypass verdict. ---
        covering: list[int] = []
        bypassable: list[int] = []
        for entry in listing:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("id")
            if not isinstance(rid, int):
                continue
            detail = await self._fetch_ruleset(full, rid, headers)
            if detail is None:
                # A single unreadable ruleset makes the whole verdict unsafe:
                # the one we could not read might be the one granting bypass.
                return ProbeOutcome(
                    state="unverified",
                    result="rulesets_unreadable",
                    reason=(
                        f"ruleset {rid} on {full} could not be read; refusing to "
                        "conclude a protection state from an incomplete view."
                    ),
                    terminal=False,
                )
            if detail.get("enforcement") != "active":
                # 'evaluate' and 'disabled' rulesets do not block anything.
                continue
            if not _covers_branch(detail, branch):
                continue
            if not _restricts_pushes(detail):
                continue
            covering.append(rid)
            if str(detail.get("current_user_can_bypass") or "") in _BYPASS_CAPABLE:
                bypassable.append(rid)

        if bypassable:
            return ProbeOutcome(
                state="bypass_granted",
                result="bypass_granted",
                reason=(
                    f"ruleset(s) {sorted(bypassable)} cover {full}@{branch} but "
                    "GitHub reports this App CAN bypass them "
                    "(current_user_can_bypass). The customer believes this branch "
                    "is protected and, for Skylize's App, it is not. Remove the "
                    "Skylize App from the ruleset's bypass actors."
                ),
                terminal=True,
                covering_ruleset_ids=tuple(sorted(covering)),
            )

        if covering:
            return ProbeOutcome(
                state="protected",
                result="ok",
                reason=None,
                terminal=True,
                covering_ruleset_ids=tuple(sorted(covering)),
            )

        # ---- Step 3: no covering ruleset. Is there CLASSIC protection? ------
        classic = await self._branch_is_classically_protected(full, branch, headers)
        if classic is True:
            return ProbeOutcome(
                state="unprotected",
                result="classic_only",
                reason=(
                    f"{full}@{branch} reports protected=true but no active "
                    "ruleset covers it, so protection is CLASSIC branch "
                    "protection. Its bypass configuration requires "
                    "administration:read to inspect, which integration_inputs.md "
                    "2.4 Tier 1 forbids requesting — so Skylize cannot verify "
                    "that this App is unable to bypass it. Recorded as "
                    "unprotected deliberately: an unverified guarantee is worse "
                    "than a known gap. Migrating this branch to a repository "
                    "ruleset makes it verifiable."
                ),
                terminal=True,
            )

        return ProbeOutcome(
            state="unprotected",
            result="no_ruleset",
            reason=(
                f"no active ruleset restricts pushes to {full}@{branch}, and the "
                "branch does not report classic protection. contents:write alone "
                "therefore permits a direct push to it: Tier 2 gives this "
                "repository nothing and only Tier 1 (permissions never requested) "
                "applies. Add a repository ruleset restricting updates and "
                "blocking force pushes, and do not list the Skylize App as a "
                "bypass actor."
            ),
            terminal=True,
        )

    async def _fetch_ruleset(
        self, full: str, ruleset_id: int, headers: dict[str, str]
    ) -> dict[str, Any] | None:
        """Fetch one ruleset's detail, or None if it could not be read.

        The detail endpoint is the one that carries BOTH `conditions` (needed to
        know whether it covers the branch) and `current_user_can_bypass` (needed
        to know whether this App is bound by it). The list endpoint carries
        neither, which is why a second call per ruleset is unavoidable.
        """
        try:
            resp = await self._client.get(
                f"{self._base}/repos/{full}/rulesets/{ruleset_id}", headers=headers
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def _branch_is_classically_protected(
        self, full: str, branch: str, headers: dict[str, str]
    ) -> bool | None:
        """Read the `protected` flag on a branch. None when it cannot be read.

        This is the ONLY classic-protection signal available under Tier 1. It is a
        boolean with no bypass detail, which is exactly why a true result maps to
        `unprotected`/'classic_only' rather than `protected` — see the module
        docstring.
        """
        try:
            resp = await self._client.get(
                f"{self._base}/repos/{full}/branches/{branch}", headers=headers
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        flag = body.get("protected")
        return flag if isinstance(flag, bool) else None


def _covers_branch(detail: dict[str, Any], branch: str) -> bool:
    """Does this ruleset's ref_name condition match `branch`?

    GitHub's include/exclude entries are ref patterns plus two special tokens:
      * '~ALL'     - every branch
      * '~DEFAULT_BRANCH' - the repository's default branch
    Patterns are `fnmatch`-style over the full ref name, so 'refs/heads/releases/*'
    matches 'refs/heads/releases/1.2'.

    `~DEFAULT_BRANCH` is treated as matching, because this probe is only ever
    pointed at the branch a customer nominated as the one to govern
    (`github_app_repos.protected_branch`), and treating the token as a non-match
    would report a correctly-protected default branch as unprotected. The
    conservative direction differs from the classic-protection case above: there,
    being permissive would assert unverified protection; here, being restrictive
    would deny verified protection.
    """
    cond = detail.get("conditions")
    if not isinstance(cond, dict):
        # A repository-level ruleset may legitimately carry no ref_name condition,
        # in which case it applies to all branches.
        return True
    ref = cond.get("ref_name")
    if not isinstance(ref, dict):
        return True

    full_ref = f"refs/heads/{branch}"
    includes = ref.get("include") or []
    excludes = ref.get("exclude") or []

    def _matches(patterns: Any) -> bool:
        if not isinstance(patterns, list):
            return False
        for p in patterns:
            if not isinstance(p, str):
                continue
            if p in ("~ALL", "~DEFAULT_BRANCH"):
                return True
            if p == full_ref or p == branch:
                return True
            if fnmatch.fnmatch(full_ref, p) or fnmatch.fnmatch(branch, p):
                return True
        return False

    if _matches(excludes):
        return False
    return _matches(includes)


def _restricts_pushes(detail: dict[str, Any]) -> bool:
    """Does this ruleset contain at least one rule that actually blocks a push?

    A ruleset that only requires signed commits or linear history is active and
    covering but delivers none of §2.4's Tier 2 guarantee, so counting it as
    protection would overstate what the customer has.
    """
    rules = detail.get("rules")
    if not isinstance(rules, list):
        return False
    for r in rules:
        if isinstance(r, dict) and str(r.get("type") or "") in PUSH_RESTRICTING_RULES:
            return True
    return False


def utc_now() -> datetime:
    """Injectable clock seam, matching the convention in the WIF probe."""
    return datetime.now(timezone.utc)


__all__ = [
    "GithubConnectionProbe",
    "ProbeOutcome",
    "PUSH_RESTRICTING_RULES",
    "utc_now",
]
