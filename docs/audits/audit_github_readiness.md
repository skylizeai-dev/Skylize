# Audit — GitHub connector readiness (Tier 0 final piece)

> **Type:** pure pre-`integration_inputs.md` audit. No code, no migration, no
> `integration_inputs.md` edit was made this pass. §2.4 needs correcting in two
> places (see A.4); those corrections are FLAGGED here, not applied.
> **Date:** 2026-09-05
> **Live-doc verification date:** all `[LIVE-VERIFIED]` claims below were fetched
> from docs.github.com on **2026-09-05**.
> **Live EMPIRICAL verification:** Q-NEW-2 was tested against a real GitHub
> repository and a real GitHub App installation token on **2026-09-05** — see
> **E.3.1**. Result: `[VERIFIED — Apps cannot bypass]`. The Tier 2 architecture
> stands.
> **Prior art followed:** `docs/audits/audit_gdrive_readiness.md`,
> `docs/audits/audit_notion_asana_readiness.md`.

## Hard exit gates — status

| Gate | Result |
|---|---|
| STOP if §2.4's `[APPROVED]` status rests on content live docs now contradict | **NOT TRIPPED.** §2.4 is not approved — see A.1. No stale approval exists to be honoured or revoked. |
| STOP if GitHub's credential model is incompatible with BOTH `oauth_credentials` and the WIF table | **PARTIALLY TRIPPED — presented as a third-shape question, not force-fitted.** See D. The shape is a genuine third one, but it is a *hybrid* that borrows from both, not an unprecedented one. Owner decision **Q-NEW-3** in F. |
| No code / migration / `integration_inputs.md` content written | **HELD.** This file is the only artifact. |
| *(2026-09-05 verification pass)* STOP if no GitHub account/App/test repo available for a real test | **PARTIALLY TRIPPED, REPORTED, THEN WORKED AROUND WITHOUT SIMULATING.** No bespoke App could be registered (E.3.2, a hard GitHub API limitation), and branch enforcement is paywalled on private repos for this free account. The test was run instead against a **genuine App installation token** (Actions' `GITHUB_TOKEN`, identity proven two ways in E.3.1) on a user-authorized public probe repo. Nothing was assumed or simulated. |
| *(2026-09-05 verification pass)* No Skylize connector code written regardless of outcome | **HELD.** Only this audit file changed. |

---

## (A) Current §2.4 content and its accuracy

### A.1 Approval status — NOT approved

`docs/06_integrations/integration_inputs.md:412` reads:

> **Section status: `[OWNER-DECISION-REQUIRED]`**

The sign-off block confirms it independently — `integration_inputs.md:1121`:

> `- 2.4 GitHub: __________________________________________  (owner, date)`

Blank. Compare the four providers that *are* signed off: Slack
(`integration_inputs.md:1120`, 2026-08-28), Drive (`:1122-1125`, 2026-08-31),
Asana (`:1126-1130`, 2026-09-03), Notion (`:1131-1137`, 2026-09-04).

**Consequence.** §2.4 is the shortest provider section in the file (24 lines,
`:410-433`) and consists of three open questions plus a code-state assertion. It
contains **no `[APPROVED]` content at all**, therefore nothing stale to
invalidate. Under the file's own rule (`integration_inputs.md:20`, "Connector
implementation is BLOCKED until the relevant section reads `[APPROVED]`") and
precondition 4 of §4.0 (`:1098`), GitHub connector code remains blocked. That is
the correct state and this audit does not change it.

Note the file banner (`integration_inputs.md:4`) scopes the document to "Stripe,
AWS, GCP, Slack, GitHub" — Drive, Asana and Notion were added later and the
banner was never updated. Cosmetic, but it means GitHub is one of the *original*
five and its §2.4 predates the three audited-and-approved connectors. §2.4 has
therefore never been through the audit discipline the later three received.

### A.2 What §2.4 currently claims

| Ref | Claim | Status per §2.4 |
|---|---|---|
| `:414-415` | No GitHub integration code, no Octokit, no App manifest; only `.github/` content is Skylize's own CI | `[CODE-VERIFIED]` |
| `:417-420` (Q2.4a) | Install scope: org-level App install vs per-repository. Suggests App, repo-selected at install time; installation tokens short-lived and per-installation, suits attenuation-only better than a PAT | `[OWNER-DECISION-REQUIRED]` + `[RESEARCH-SUGGESTED]` |
| `:421-426` (Q2.4b) | Per-verb decision for: push to protected branch; branch deletion; PR merge; release publication; secret/Actions-variable modification. Suggests merge + protected-branch push defer to a human; branch deletion + secret modification hard-denied to agents | `[OWNER-DECISION-REQUIRED]` + `[RESEARCH-SUGGESTED]` |
| `:427-432` (Q2.4c) | Agent must never be granted bypass on the customer's branch-protection rules; a GitHub refusal stands and surfaces as a clean tool error; connector must not hold an admin path around it | `[OWNER-DECISION-REQUIRED]` |

Separately, `integration_inputs.md:196` classifies GitHub as **org-level**, note
"GitHub App installed into the customer's org", under the `[RESEARCH-SUGGESTED]`
table at `:181`.

### A.3 Accuracy verdict — the `[CODE-VERIFIED]` claim STILL HOLDS

Re-verified this pass. §2.4's code-state assertion (`:414-415`) is accurate.

- No GitHub API client of any kind in `src/`. The only `github` matches in
  `src/skylize/` are prose references in `src/skylize/app/gcp/oidc.py:24,26,32,42,43,154`,
  which cite GitHub Actions' OIDC issuer as the *reference implementation Skylize
  copied its own discovery document from* — not an integration.
- No dependency: `grep -i -e github -e octokit pyproject.toml requirements*.txt`
  returns nothing. No `PyGithub`, no `githubkit`, no Octokit.
- `.github/` contains exactly `workflows/ci.yml` and `workflows/deploy-staging.yml`.
- No migration for GitHub. `migrations/versions/` ends at
  `0025_gcp_containment_claims.py`; the credential tables are
  `0007_org_credentials.py`, `0021_oauth_credentials.py`,
  `0023_oauth_credentials_nullable_expiry.py`, `0024_gcp_wif_connections.py`.
- No GitHub tool. `src/skylize/tools/builtin/` holds `asana_tools.py`,
  `datetime_tool.py`, `drive_tools.py`, `gcp_tools.py`, `hubspot_tools.py`,
  `memory_recall.py`, `notion_tools.py`, `web_search.py`.

**One wording nit, not an error:** "no Octokit" names GitHub's *JavaScript/Go*
client. In a Python codebase the load-bearing absence is `PyGithub` / `githubkit`
/ a raw `httpx` GitHub client. All are absent, so the claim is true as stated but
tests the wrong library name for this repo.

### A.4 Two substantive defects in §2.4 — FLAGGED, NOT FIXED

**Defect 1 — Q2.4b treats "branch deletion" as hard-deniable by policy, but
GitHub's permission model makes it inseparable from PR-branch creation.**

`integration_inputs.md:424-426` proposes "branch deletion and secret modification
are hard-denied to agents". Secret modification genuinely is hard-deniable (it is
a distinct fine-grained permission the App simply never requests — see C.3).
Branch deletion is **not**. `[LIVE-VERIFIED]` 2026-09-05,
https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps
(Contents section): `DELETE /repos/{owner}/{repo}/git/refs/{ref}` requires
**`contents: write`** — the *same single permission* as
`POST /repos/{owner}/{repo}/git/refs` (create a branch),
`PATCH /repos/{owner}/{repo}/git/refs/{ref}` (update a ref, i.e. push) and
`POST /repos/{owner}/{repo}/git/commits`.

There is no `contents: write-except-delete`. An agent that can create the head
branch a pull request requires can, with the identical token, delete branches.
§2.4 currently implies these are separable at the grant layer. They are not. See
E.3 for what this actually forces.

**Defect 2 — Q2.4b lists "PR merge" as gated by a defer-to-human rule without
noting that merge is also `contents: write`, not `pull_requests: write`.**

`[LIVE-VERIFIED]` 2026-09-05, same source:
`PUT /repos/{owner}/{repo}/pulls/{pull_number}/merge` is listed under **Contents**
write, alongside `POST /repos/{owner}/{repo}/merges`. So "the agent may open PRs
but a human merges them" is *not* achievable by withholding a merge permission —
the merge capability rides along with `contents: write`. It must be enforced by a
ruleset or a Skylize-side gate.

Neither defect makes §2.4 dangerous — it is unapproved and blocks code either
way. Both mean Q2.4b **cannot be answered in its current form**: it asks the
owner to make per-verb decisions across a boundary GitHub's permission model does
not offer. Q2.4b should be reframed before it goes to the owner (F, Q-NEW-1).

---

## (B) What exists in code vs nothing

**Nothing GitHub-specific exists.** Per A.3: zero client, zero dependency, zero
migration, zero tool, zero manifest. §2.4 is documentation-only.

What *does* exist is reusable substrate, and it is substantial:

| Substrate | Location | Relevance to GitHub |
|---|---|---|
| Four opt-in `ToolProxy` gate profiles | `src/skylize/tools/base.py:78` (`ToolSpendProfile`), `:102` (`ToolPermissionProfile`), `:137` (`ToolWifProfile`), `:179` (`ToolOAuthProfile`) | GitHub needs either a fifth or a reuse — see D.4 |
| Provider-agnostic OAuth refresh + revocation | `src/skylize/app/credentials/oauth.py:267` (`evaluate_grant`), `:304` (`OAuthCredentialService`) | **Largely inapplicable** — see D.2 |
| WIF per-call token minting | `src/skylize/app/gcp/tokens.py:112` (`mint_id_token`) | **Structurally the closer analogue** — see D.3 |
| Platform-level signing key resolution | `src/skylize/app/gcp/keys.py:159` (`load_wif_signing_key`), pattern set by `src/skylize/bootstrap.py:95` (`resolve_credential_encryption_key`) and `:160` (`resolve_slack_notifier_config`) | **Directly reusable** for the App private key — see D.3 |
| Non-secret per-tenant connection table, no encrypted column | `migrations/versions/0024_gcp_wif_connections.py:129-168` | **Directly analogous** — see D.3 |
| Stateless-agent structural tripwire | `tests/contract/test_stateless_agents_no_oauth_access.py:38-46`, `:48-57`, `:267-308` | GitHub must be added here — see H |

---

## (C) Correct auth model — GitHub App, and the alternatives are disqualified

### C.1 The three candidates

`[LIVE-VERIFIED]` 2026-09-05, https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
and .../generating-a-json-web-token-jwt-for-a-github-app:

**GitHub App.** Long-term credential is the App's **private key**. Flow: sign a
JWT with `RS256`; claims `iat` (recommended 60s in the past for clock drift),
`exp` (**"no more than 10 minutes into the future"**), `iss` = the App's client ID
or app ID. `POST /app/installations/{installation_id}/access_tokens` with
`Authorization: Bearer <jwt>` returns an installation access token which
**"will expire after 1 hour."** **No refresh token exists** — the docs describe
none; you re-mint from the private key.

**The decisive property — down-scoping at mint time.** Same source: the
`repositories` or `repository_ids` body parameters restrict which repositories
the token can reach (up to **500** repositories), and the `permissions` body
parameter restricts which permissions the token carries. Omitting both yields
"all repositories" and "all of the permissions that were granted to the app."

**OAuth App / user-delegated.** Acts *as a user*. Its ceiling is the union of
that user's own access — for an engineer with admin on the org, that includes
repo deletion and branch-protection bypass. It cannot be attenuated per-call the
way an installation token can.

**Personal access token.** Same defect as the OAuth App plus no org-visible
install/uninstall boundary, no per-repo selection UI, and a secret that must be
stored long-term per tenant.

### C.2 Verdict — GitHub App, and the reasoning is stronger than §2.4 states

§2.4's `[RESEARCH-SUGGESTED]` answer (App, repo-selected at install) is
**correct** and this audit confirms it against live docs. But §2.4 undersells the
argument. Its stated reason is only that "installation tokens are short-lived and
per-installation, which suits attenuation-only far better than a PAT"
(`:419-420`). The stronger reason is the mint-time `permissions` + `repositories`
parameters: they make Skylize's **attenuation-only** invariant
(`integration_inputs.md:33-36`, "A connector may never widen authority")
enforceable *by the provider*, not merely by Skylize's own discipline. Skylize can
mint a token narrower than the installation grant, per call, and GitHub enforces
the narrowing. No other GitHub auth model offers this. Neither Drive, Asana nor
Notion offers it either — this is a capability GitHub has that the three approved
connectors do not.

### C.3 What repo deletion requires — and why that settles one verb entirely

`[LIVE-VERIFIED]` 2026-09-05, https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps:
`DELETE /repos/{owner}/{repo}` requires the **`administration`** permission at
write level (write also covers branch-protection configuration and collaborator
management). It is a *different* permission from `contents`.

**Therefore repository deletion is not a verb to gate — it is a permission never
to request.** An App whose manifest omits `administration` cannot delete a
repository, cannot edit branch protection, and cannot add collaborators, with no
Skylize runtime code involved and no possibility of a gate being bypassed by a
refactor. This is the single cleanest result in this audit and it directly
satisfies the CFO Test's "delete repos" half.

Note this also structurally satisfies §2.4's Q2.4c requirement
(`:427-432`, "must never be granted bypass on the customer's branch-protection
rules"): editing branch protection *is* `administration: write`. Withholding that
one permission makes the "admin path around it" that Q2.4c forbids non-existent
rather than merely prohibited.

---

## (D) Credential shape — a third shape, structurally nearest WIF

### D.1 The question

Does GitHub reuse `oauth_credentials` (migration 0021), the WIF-style
`gcp_wif_connections` (0024), or need a third shape?

### D.2 `oauth_credentials` is the WRONG shape — the same argument 0024 already made

`migrations/versions/0021_oauth_credentials.py:84-99` defines
`encrypted_access_token TEXT NOT NULL` (`:91`), `encrypted_refresh_token TEXT`,
`expires_at TIMESTAMPTZ NOT NULL` (relaxed to nullable by 0023), and
`connection_state` (`:95`) with three values.

Test each against a GitHub App installation:

- `encrypted_access_token NOT NULL` — an installation token lives 1 hour and is
  re-mintable at will from the private key. Persisting it is unnecessary and
  strictly worsens the blast radius. `migrations/versions/0024_gcp_wif_connections.py:20-22`
  already rejected `oauth_credentials` for exactly this: "There is no honest value
  for that column."
- `encrypted_refresh_token` — **no refresh token exists in the GitHub App model**
  (C.1). Permanently NULL, and the entire refresh machinery
  (`src/skylize/app/credentials/oauth.py:267` `evaluate_grant`, and `ensure_fresh`),
  which `0024:23-27` notes "runs on EVERY governed tool call", would be answering
  a question that does not apply. Verbatim the same defect 0024 identified.
- `expires_at` — a 1-hour token that is never persisted has no row-level expiry to
  record. As `0024:28-32` puts it, NULL here would assert "does not expire by
  time", the opposite of true.

So the exclusion argument that `0021:10-20` made against widening
`org_credentials`, and that `0024:14-32` made against widening
`oauth_credentials`, applies to GitHub **with the same force and for the same
reasons**. Reusing `oauth_credentials` for GitHub would reverse two documented
decisions.

### D.3 The WIF shape is close, and the resemblance is deep — but not identical

The mechanism genuinely rhymes with WIF. Both mint a short-lived credential
per-use by signing a JWT with a platform-held private key, and both persist no
token:

| | GCP WIF (built) | GitHub App (proposed) |
|---|---|---|
| Platform secret | P-256 signing key, `src/skylize/app/gcp/keys.py:159` | App private key (RS256) |
| Assertion | signed JWT, `src/skylize/app/gcp/tokens.py:112` | signed JWT, `iss` = app id, `exp` <= 10 min |
| Exchange | Google STS | `POST /app/installations/{id}/access_tokens` |
| Result TTL | 5 min (chosen; Google caps at 60) | 1 hour (GitHub-fixed) |
| Token persisted? | No | No |
| Refresh token? | None | None |
| Per-tenant row holds | non-secret config only, no encrypted column, no `key_id` (`0024:34-47`) | non-secret `installation_id` + account login + selected repos |

The **`installation_id` is not a secret** — it is an opaque integer that grants
nothing without the App private key, exactly as `0024:48-53` argues for
`issuer_slug`: "possession of the URL grants nothing — only the signing key mints
tokens." So a GitHub table would, like `gcp_wif_connections`, have **no encrypted
column and no `key_id`**.

`[LIVE-VERIFIED]` 2026-09-05, https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps:
private keys are managed **at the App level**, an App may hold **up to 25** at
once, and rotation is generate-new → switch → delete-old. This confirms the key
is **one platform-level secret shared across every tenant's installation**, not a
per-tenant credential — so it belongs in the secrets manager per
`integration_inputs.md:189-191` (platform-level: "belongs in the secrets manager,
not the tenant vault"), resolved at composition time in the shape of
`src/skylize/bootstrap.py:95` / `:160`. The 25-key allowance maps onto 0024's
`signing_key_id` rotation-anchor reasoning (`0024:82-91`) — and unlike GCP's
`jwks_delivery='uploaded'` case, **GitHub rotation is entirely a Skylize-side
action requiring nothing from the customer**, which is strictly simpler.

### D.4 Where GitHub departs from WIF — three real differences

1. **No customer-side trust configuration.** `gcp_wif_connections` carries nine
   columns of customer-typed IAM config (`0024:131-142`: project, pool, provider,
   audience, service account). GitHub has **none** — the customer clicks
   "Install" and picks repositories in GitHub's own UI. The trust is established
   by GitHub, not assembled from customer input. This makes the GitHub table
   **much smaller** than 0024's, and makes 0024's `'misconfigured'` state (trust
   works, IAM binding removed — `0024:70-76`) largely inapplicable: there is no
   separate binding layer to break.
2. **Uninstall is the revocation signal, and it is a webhook.** A customer removes
   access by uninstalling the App. GitHub emits an `installation` webhook. Neither
   `oauth_credentials`' refresh-failure path nor WIF's probe path is the natural
   detector. This resembles Notion's problem
   (`src/skylize/tools/builtin/notion_tools.py:30-46`: a never-expiring grant whose
   death is only observable on a live call) but with a *better* available answer —
   a push notification rather than polling. **No webhook ingress exists in the repo
   today**; that is new surface, and it is the largest piece of genuinely new
   infrastructure GitHub would need.
3. **Per-call down-scoping has no analogue.** C.1's mint-time `permissions` +
   `repositories` parameters are a capability neither existing shape models. The
   WIF equivalent is `gcp_wif_targets` (`0024:185-195`) — an allow-list of
   permitted resources checked at the gate (`src/skylize/tools/base.py:154`,
   check 3: "the requested resource is an ENABLED row in `gcp_wif_targets`").
   GitHub could do better than an allow-list check: it can pass the restriction
   *to GitHub* at mint time and have GitHub enforce it. That is a stronger
   property than any gate Skylize currently implements.

### D.5 Verdict — third shape, WIF-derived. Owner decision required.

Not force-fitted either way. The honest statement: **GitHub needs its own table,
which will look like a trimmed `gcp_wif_connections` plus a repository-selection
child table.** It reuses 0024's *structural* decisions verbatim — `(org_id, label)`
identity cardinality, `tenant_isolation` RLS ENABLE + FORCE, the `skylize_app`
grant, exclusion from 0002's cross-tenant carve-out, no encrypted column, no
`key_id` — and reuses none of `oauth_credentials`' payload columns. It adds one
thing neither has: a per-call permission/repository restriction passed to the
provider.

This is the third shape the gate anticipated, and it is a *narrower* table than
either existing one. Recorded as **Q-NEW-3** in F for the owner, not decided here.

---

## (E) The "prevention" framing — it DOES change the architecture

This is the most consequential finding in the audit.

### E.1 Every prior connector was "enable + gate". GitHub is not.

Drive, Asana, Notion, Stripe and the GCP kill-switch all answer "the agent needs
to do X; interpose governance before X happens." The CFO Test's GitHub
requirement is the inverse: the agent must be **technically prevented**, and a
runtime gate is a weaker property than prevention. A gate is code that can be
refactored around — the concern `src/skylize/tools/base.py:159-160` states
explicitly ("A gate that only exists inside a function body is one refactor from
being skipped"). Prevention by *absent permission* cannot be refactored around,
because the capability was never minted.

So GitHub's correct architecture is **three tiers**, and Skylize-side gating is
the last resort, not the primary mechanism:

### E.2 Tier 1 — prevention by permission omission (strongest; no Skylize code)

Verbs eliminated outright by never requesting the permission:

| Verb | Permission withheld | Result |
|---|---|---|
| Delete a repository | `administration` | Impossible. `[LIVE-VERIFIED]` C.3 |
| Edit/weaken branch protection or rulesets | `administration` | Impossible — this is what makes Q2.4c structural |
| Add a collaborator | `administration` | Impossible |
| Modify repository secrets | `secrets` | Impossible |
| Modify Actions variables / workflow runs | `actions` / `environments` | Impossible |
| Publish a release | releases sit under `contents` write | **Not separable** — see E.3 |

Four of the five verb classes §2.4's Q2.4b asks the owner to rule on
(`integration_inputs.md:421-426`) collapse into "a permission the App manifest
omits." **They need no per-verb owner decision and no gate.** That is a large
simplification relative to §2.4's framing.

### E.3 Tier 2 — prevention by GitHub's own rulesets (strong; Skylize must not bypass)

`contents: write` is indivisible (A.4, Defect 1). If the agent is to open pull
requests, it needs `contents: write` to create the head branch and commits — and
that same permission carries push-to-any-branch, ref deletion, PR merge, and
release publication.

**Therefore "PR-creation-only" is NOT achievable by permission scoping.** This
refutes the hypothesis in the brief's item 6 that the agent could "only ever get
PR-creation access": a PR needs a head branch, a head branch needs
`contents: write`, and `contents: write` includes push. The narrowest useful grant
is `contents: write` + `pull_requests: write`, and it is *not* narrow enough on
its own.

What closes the gap is GitHub's ruleset layer. `[LIVE-VERIFIED]` 2026-09-05,
https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets:

| Rule | Blocks | Doc wording |
|---|---|---|
| Restrict updates | pushing to matching branches | "Only users with bypass permissions can push to branches or tags whose name matches the pattern you specify" |
| Restrict deletions | deleting matching branches/tags; **enabled by default** | "Only users with bypass permissions can delete branches or tags whose name matches the pattern" |
| Restrict creations | creating matching branches/tags | "Only users with bypass permissions can create branches or tags whose name matches the pattern" |
| Block force pushes | force pushes / history rewrites | applies to "All users except those with bypass permissions" |
| Require a PR before merging | direct merges without a PR | "All changes to the target branch be associated with a pull request" |

And `[LIVE-VERIFIED]` 2026-09-05,
https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets:
bypass may be granted to "users with a **certain role, such as repository
administrator, or it can be specific teams or GitHub Apps.**"

**Originally an inference — NOW EMPIRICALLY VERIFIED.** Neither doc page states in
so many words that rulesets apply to a GitHub App that has *not* been granted
bypass. The first version of this audit therefore recorded the claim as a flagged
inference and refused to build on it. **It has since been tested live and it holds
— see E.3.1 below for the full method and results.**

**Now verified, the property is exactly what the CFO Test needs**, and it is
enforced by GitHub, not by Skylize: with `main` covered by a ruleset (restrict
updates + block force pushes + require a PR) and the Skylize App **not** a bypass
actor, a push to `main` is refused at GitHub no matter what the agent, the model,
or a bug in Skylize's gate attempts. Per E.3.1 the observed refusals are `GH013`
at the git layer and `HTTP 422 "Repository rule violations found"` at the REST
layer — not the `403` this audit's first draft guessed at, and the real status
codes are worth knowing for whoever writes the error-surfacing path Q2.4c
requires. §2.4's Q2.4c (`:427-432`) already states that requirement correctly and
this audit endorses it unchanged — and E.2 makes it structural, since weakening
the ruleset needs `administration`.

**The residue.** `contents: write` still permits deleting *unprotected* branches
and merging PRs on unprotected branches. "Restrict deletions" is per-pattern, so
it only covers branches matching a configured pattern. Closing the residue means
either a broad-pattern ruleset in the customer's repo (a customer configuration
dependency Skylize cannot guarantee) or Tier 3.

### E.3.1 `[EMPIRICALLY VERIFIED 2026-09-05]` Live test — App installation token vs a zero-bypass-actor ruleset

**Verdict: a GitHub App installation token with `contents: write` CANNOT bypass a
ruleset that lists no bypass actors. Blocked at both the git layer and the REST
API layer, for normal push, force-push, and branch deletion. The Tier 2
architecture stands.**

**Environment.** Account `skylizeai-dev` (GitHub free, type `User`, no orgs).
Disposable public repository `skylizeai-dev/ruleset-bypass-probe`, created for
this test and containing nothing else.

**Note on why the repo is public.** Both enforcement mechanisms are paywalled on
private repositories for a free User account. Verified empirically — ruleset
creation and classic branch protection each returned the identical error:

```
HTTP 403 {"message":"Upgrade to GitHub Pro or make this repository public
to enable this feature."}
```

So a public repo was the only way to exercise *any* branch enforcement on this
account. This is a limitation of the test account, not of GitHub's model.

**Ruleset under test** (id `22342290`), as stored and read back from GitHub:

```json
{"id":22342290,"name":"probe-zero-bypass","target":"branch",
 "enforcement":"active","bypass_actors":[],
 "current_user_can_bypass":"never",
 "rules":["update","deletion","non_fast_forward"],
 "conditions":{"ref_name":{"include":["refs/heads/protected-probe"],"exclude":[]}}}
```

`bypass_actors` is empty — the strictest possible configuration. Note GitHub's own
computed field `current_user_can_bypass: "never"`, i.e. GitHub agrees the
repository owner has no bypass path.

**PROOF the token was a genuine GitHub App installation token, not a PAT or a user
token.** The test used GitHub Actions' `GITHUB_TOKEN`, which is an installation
access token for the first-party `github-actions` App. Two probes inside the run
establish this positively rather than by assertion:

| Probe | Result | What it proves |
|---|---|---|
| `GET /installation/repositories` | **HTTP 200**, `total_count: 1`, `repository_selection: "selected"` | This endpoint is valid **only** for an App installation access token. A PAT or OAuth user token cannot call it. |
| `GET /user` | **HTTP 403** `"Resource not accessible by integration"` | The token is an *integration* (App), not a user. GitHub's own error wording. |

The runner also logged the token's permission set for the job:
`Contents: write`, `Metadata: read` — precisely the grant C.2/E.3 contemplates for
a Skylize GitHub App, and deliberately no `administration`.

**Results — four attempts, all against `refs/heads/protected-probe`:**

| # | Actor | Attempt | Result |
|---|---|---|---|
| **B1** | App installation token | `git push` (normal, fast-forward) | **BLOCKED.** `remote: error: GH013: Repository rule violations found` / `- Cannot update this protected ref.` exit 1 |
| **B2** | App installation token | `git push --force` | **BLOCKED.** Same `GH013` / `Cannot update this protected ref.` exit 1 |
| **B3** | App installation token | `PATCH /repos/.../git/refs/heads/protected-probe` with `{"force":true}` | **BLOCKED.** `HTTP 422` `"Repository rule violations found\n\nCannot update this protected ref."` |
| **B4** | App installation token | `DELETE /repos/.../git/refs/heads/protected-probe` | **BLOCKED.** `HTTP 422` `"Repository rule violations found\n\nCannot delete this branch"` |

**B3 is the most important row for Skylize.** A connector would never shell out to
`git`; it would call the REST API. B3 and B4 prove the ruleset is enforced at the
REST layer too, with `force: true` explicitly set — so enforcement is a property of
the ref-update path itself, not of the git transport.

**Control group — the same ruleset against a repository ADMIN (user actor):**

| # | Actor | Attempt | Result |
|---|---|---|---|
| **A1** | Repo owner/admin (`skylizeai-dev`) | `git push` | **BLOCKED.** `GH013` / `Cannot update this protected ref.` |
| **A2** | Repo owner/admin | `git push --force` | **BLOCKED.** `GH013` / `Cannot update this protected ref.` |
| **A3** | Repo owner/admin | `git push --delete` | **BLOCKED.** `GH013` / `- Cannot delete this branch` |

So a zero-bypass-actor ruleset binds the repository owner too. Unlike classic
branch protection's `enforce_admins` toggle, rulesets grant admins **no implicit
bypass** — bypass exists only for explicitly listed actors.

**Integrity check.** After all seven attempts, `protected-probe` remained at its
baseline commit `9742bf8e0ed06f5bc27c6a5a1da9926015ed0c73`, and the ruleset
remained `enforcement: active` with `bypass_actors: []`. Nothing got through.

**Honest caveat, stated rather than buried.** The App exercised was
`github-actions`, GitHub's own first-party App, because a bespoke App could not be
created in this environment (see E.3.2). It is nonetheless a genuine App
installation token by the two positive proofs above, and `github-actions` is
itself a selectable ruleset bypass actor — so it sits in exactly the actor
category under test. The residual risk is that GitHub special-cases its own App;
that risk runs in the **safe direction**, since any first-party special-casing
would grant *more* privilege, and it was blocked anyway. A bespoke-App re-test
remains a cheap confirmation but is no longer load-bearing.

### E.3.2 `[VERIFIED LIMITATION]` Why a bespoke GitHub App was not used

A purpose-built App could not be registered in this environment, and this is a
hard property of GitHub's API rather than a tooling gap. `[LIVE-VERIFIED]`
2026-09-05, https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest:
the only programmatic registration path is the App Manifest flow, which
**mandates a browser redirect and human approval** — "You redirect people to
GitHub to register a new GitHub App", the person "will be redirected to a GitHub
page with an input field where they can edit the name", and GitHub then "redirects
back to the `redirect_url` with a temporary `code`" which must be exchanged within
one hour. Probing the exchange endpoint confirms it is the only one that exists:
`POST /app-manifests/{code}/conversions` returns `HTTP 404` with
`documentation_url` pointing at "create a GitHub App from a manifest". There is no
non-interactive REST endpoint that creates a GitHub App.

Consequence for future passes: **any test requiring a bespoke Skylize GitHub App
needs a human to register it in the web UI and hand over the app id + private
key.** Automation cannot self-provision one. This is also a real onboarding fact
for the eventual product: Skylize registers its App once, manually, and customers
only *install* it.

### E.4 Tier 3 — Skylize-side gate (weakest; the true remaining scope)

After Tiers 1 and 2, what genuinely requires a Skylize gate is small:

- **Branch deletion on unprotected branches** — the one verb §2.4 wants hard-denied
  that neither permission omission (Defect 1) nor a pattern-scoped ruleset
  reliably prevents.
- **PR merge**, if the owner wants merge deferred to a human even where the
  customer has no "require a PR" ruleset (Defect 2).
- **Spend/HITL narrative**, if any GitHub verb is to escalate through HITL the way
  the GCP stop verb does (`src/skylize/tools/builtin/gcp_tools.py:126-130`).

A defensible alternative to gating branch deletion is to **not register a
branch-deletion verb at all** — the precedent is
`tests/contract/test_stateless_agents_no_oauth_access.py:270-292`, which asserts
the GCP verb surface is exactly `["integration.gcp_stop_instance"]` and warns
against "adding an irreversible verb". If Skylize never exposes a delete-branch
tool, the agent has no path to invoke one even though the token would permit it.
That is verb-surface minimalism rather than gating, and it is cheaper and more
auditable than a gate. Owner's call — **Q-NEW-1** in F.

### E.5 Net effect on design cost

GitHub is plausibly the **simplest** Tier 0 connector on the governance side and
the most complex on the credential/ingress side:

- *Simpler:* no `ToolPermissionProfile` analogue (no "hand data to a party the
  agent picks" verb, same conclusion Notion reached at
  `src/skylize/tools/builtin/notion_tools.py:8-22`); four of five Q2.4b verb
  classes vanish into permission omission; the headline prevention property is
  enforced by GitHub.
- *More complex:* a third credential table, a new platform key custody path, a
  per-call mint with down-scoping, and — new to this repo — **webhook ingress**
  for uninstall detection.

---

## (F) Open questions for owner decision

None of these is resolved by this audit.

**Q-NEW-1 `[OWNER-DECISION-REQUIRED]` — Reframe Q2.4b.** Q2.4b as written
(`integration_inputs.md:421-426`) asks for per-verb rulings across a boundary
GitHub does not offer (A.4). It should be replaced with three separable
questions: (a) which permissions the App manifest requests — the Tier 1 list;
(b) which verbs Skylize registers as tools at all — the verb-surface question,
E.4; (c) which registered verbs carry a gate. Recommendation: request
`contents: write` + `pull_requests: write` + `metadata: read` only, and register
no delete-branch and no force-push verb.

**Q-NEW-2 — ~~`[OWNER-DECISION-REQUIRED / EMPIRICAL]`~~ → `[VERIFIED — Apps cannot
bypass]` 2026-09-05. CLOSED, no owner decision needed.** Rulesets **do** bind a
GitHub App installation token that is not a listed bypass actor. Proven live
against a real repository and a real App installation token (`contents: write`,
no `administration`): normal push, force-push and branch deletion were all
refused, at both the git layer (`GH013`) and the REST layer (`HTTP 422`), with
`bypass_actors: []`. A repository admin was refused identically. Full method,
token-identity proof and raw results in **E.3.1**; the reason a first-party App
was used instead of a bespoke one, and the hard GitHub API limitation behind
that, in **E.3.2**.

**Consequence: the Tier 2 architecture stands and the audit's recommended design
is unchanged.** GitHub does not need a Drive/Asana-style
`ToolPermissionProfile` runtime gate for the protected-branch case. The
prevention property is provider-enforced. Residual Skylize-side scope stays as
E.4 describes it — unprotected-branch deletion and PR merge only.

One follow-on worth recording, not blocking: because enforcement depends on the
**customer** having a ruleset (or classic protection) on their protected
branches, Skylize should *verify* that at connection time rather than assume it.
A customer with no ruleset on `main` gets no Tier 2 protection at all, and
`contents: write` then permits a direct push to `main` — not a defect in this
design, but a precondition the onboarding path should probe and surface. The
`gcp_wif_connections` health-probe precedent (`connection_state`,
`last_probe_result`, `0024:56-80`) is the natural model.

**Q-NEW-3 `[OWNER-DECISION-REQUIRED]` — Ratify the third credential shape.**
Per D.5: a new table modelled on `gcp_wif_connections`' structural decisions,
holding non-secret `installation_id` + account + repo selection, with the App
private key as a platform-level secret in the `bootstrap.py:95`/`:160` shape.
Confirm this rather than extending `oauth_credentials`.

**Q-NEW-4 `[OWNER-DECISION-REQUIRED]` — Webhook ingress for uninstall.** No
webhook surface exists in the repo. Decide: accept `installation` webhooks (new
ingress, needs signature verification and a tenant-resolution path), or detect
death on live-call failure only (the Notion pattern,
`src/skylize/tools/builtin/notion_tools.py:30-46`, which that file itself
describes as silent degradation), or a periodic probe (the WIF pattern,
`src/skylize/app/gcp/probe.py`).

**Q-NEW-5 `[OWNER-DECISION-REQUIRED]` — Install scope (Q2.4a, restated).** §2.4's
`[RESEARCH-SUGGESTED]` answer is confirmed correct by C.2, but Q2.4a remains
formally open and this audit does not close an owner decision. Note it is **less
ambiguous than Drive's org-vs-platform question was**: a GitHub App installation
is inherently org-or-account scoped with repository selection built into GitHub's
own install UI, so `integration_inputs.md:196`'s org-level classification is
sound. Recommend ratifying as org-level, repo-selected at install.

**Q-NEW-6 `[OWNER-DECISION-REQUIRED]` — Per-call down-scoping policy.** Given
C.1, should every mint pass explicit `permissions` + `repository_ids`, narrowing
to the single repository the current tool call names? Recommendation: yes — it
makes attenuation-only provider-enforced (C.2) and is the closest GitHub analogue
to `gcp_wif_targets`' enabled-resource check (`src/skylize/tools/base.py:154`).

**Still inherited and unresolved:** §4.0's preconditions
(`integration_inputs.md:1092-1102`) — notably Q3.0a on grant storage, which
Q-NEW-3 above bears on — and Section 1.1's spend ceiling. Precondition 1 is
addressed by the ToolProxy spend-ceiling work at `928c3d5`/`b8a20e3`, but this
audit did not re-verify §1.1's closure and does not claim it.

---

## (G) Rate limits

`[LIVE-VERIFIED]` 2026-09-05,
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api:

| Auth method | Primary limit |
|---|---|
| Personal access token / authenticated user | 5,000 req/hour |
| **GitHub App installation token** | **5,000 req/hour minimum**; scales by +50/hour per repository beyond 20 repos and +50/hour per user in orgs beyond 20 users, **capped at 12,500 req/hour** |
| GitHub App installation on an Enterprise Cloud org | 15,000 req/hour |
| Actions `GITHUB_TOKEN` | 1,000 req/hour per repository (15,000 for Enterprise Cloud) |

Secondary limits, same source: no more than **100 concurrent requests**; no more
than **900 points/minute** on REST, where `GET`/`HEAD`/`OPTIONS` = 1 point and
`POST`/`PATCH`/`PUT`/`DELETE` = **5 points**; no more than **80
content-generating requests/minute** and **500/hour**.

**Notes.** (i) The doc states "requests made by a higher-limit app reduce the
remaining budget available for lower-limit authentication methods" — limits are
not fully independent across auth methods on the same account. (ii) A GitHub App
installation therefore starts at parity with a PAT and only *exceeds* it on larger
orgs; the App's advantage over a PAT is the permission model (C.2), **not**
throughput. §2.4 does not currently claim otherwise. (iii) The 5-point write cost
plus the 80/minute content-generating ceiling is the binding constraint for a
PR-opening agent, not the hourly limit — a PR creation is several writes (blob,
tree, commit, ref, pull). (iv) This mirrors Notion's Q2.7g, which was
`[DEFERRED]` as a non-blocking follow-up (`integration_inputs.md:1131-1137`);
pacing can be treated the same way here.

---

## (H) Stateless invariant — confirmed, and GitHub must be added to the tripwire

**The invariant is real and enforced in code.**
`tests/contract/test_stateless_agents_no_oauth_access.py:38-46` names the five:

```
STATELESS_AGENT_IDS = {
    "cfo_agent", "chief_security_officer", "director_ai_safety",
    "llm_safety_agent", "prompt_injection_agent",
}
```

Corroborated by contract source: `src/skylize/contracts/mvp/finance.py:3`
("CFO is stateless — no memory read or write access"),
`src/skylize/contracts/mvp/safety.py:4` ("All safety agents are stateless
(`memory_read_access=[]`, `memory_write_access=[]`)"), and
`src/skylize/contracts/definitions/security.py:16`. Enforcement is structural:
`src/skylize/memory/gateway.py:6` treats an empty list as "stateless = denied",
and `src/skylize/memory/service.py:372` / `src/skylize/memory/ports.py:37` expose
`is_stateless`.

**Confirmed: nothing in a GitHub connector's design intent requires memory
access.** Every fact a GitHub verb needs is either on the validated tool input
(repo, branch, title, body) or in the connection row. This matches all three
approved connectors — none of Drive, Asana or Notion appears in a stateless
agent's grantable set.

**Required future action, flagged not performed.** The tripwire is an
explicit-enumeration test, and its comment at
`tests/contract/test_stateless_agents_no_oauth_access.py:46-47` says so: "Named
explicitly so a new connector has to be added here consciously."
`EXPECTED_OAUTH_TOOL_IDS` (`:48-57`) lists eight Drive/Asana/Notion tool ids and
`test_oauth_capable_tools_are_exactly_the_expected_set` (`:177-189`) asserts
**exact set equality**. So the moment a GitHub tool declares whatever gate profile
it ends up carrying, that test fails until GitHub's ids are added — by design. Any
GitHub pass must also extend `_full_registry` (`:86-95`) to wire the GitHub
builder, or the assertions would pass by absence — the precise trap documented at
`:11-18`.

Note the GCP verb is covered by *separate* assertions (`:267-308`) rather than by
`EXPECTED_OAUTH_TOOL_IDS`, because it carries `ToolWifProfile` rather than
`ToolOAuthProfile`. A GitHub connector on a third credential shape (D.5) will need
the same treatment: its own named assertions, including a verb-surface assertion
in the shape of `:270-292`.

---

## Summary

1. **§2.4 is `[OWNER-DECISION-REQUIRED]`, not approved** (`integration_inputs.md:412`,
   `:1121`). No stale approval; no gate tripped. GitHub code remains correctly
   blocked.
2. **Nothing is built.** §2.4's `[CODE-VERIFIED]` claim still holds (A.3).
3. **§2.4 has two substantive defects** (A.4): Q2.4b assumes branch deletion and
   PR merge are separable from PR-branch creation. Both are `contents: write`.
   Q2.4b cannot be answered as written. **Flagged, not fixed.**
4. **GitHub App is correct**, and for a better reason than §2.4 gives: mint-time
   `permissions` + `repositories` down-scoping makes attenuation-only
   provider-enforced (C.2).
5. **The credential model is a third shape** — a trimmed `gcp_wif_connections`,
   no encrypted column, plus a platform-level App private key. Reusing
   `oauth_credentials` would reverse the documented decisions at `0021:10-20` and
   `0024:14-32` (D).
6. **The prevention framing materially changes the architecture.** Repo deletion,
   branch-protection editing and secret modification are prevented by *omitting
   `administration` and `secrets`* — no gate, unrefactorable-around (E.2).
   Protected-branch push and force-push are prevented by the customer's rulesets
   with the App as a non-bypass actor (E.3). Only unprotected-branch deletion and
   PR merge plausibly need Skylize-side treatment, and verb-surface minimalism may
   beat a gate there (E.4).
7. **The one load-bearing inference is now PROVEN** (Q-NEW-2, closed 2026-09-05).
   A real GitHub App installation token with `contents: write` was refused on
   normal push, force-push and branch deletion against a ruleset with
   `bypass_actors: []` — blocked at the git layer (`GH013`) *and* the REST layer
   (`HTTP 422`), the latter with `force: true` explicitly set. A repository admin
   was refused identically; rulesets grant admins no implicit bypass. Method and
   raw evidence in E.3.1. **Tier 2 stands; GitHub does not need a Drive/Asana-style
   runtime gate for protected-branch push.** Caveat stated in E.3.1: the App was
   GitHub's first-party `github-actions`, since a bespoke App cannot be registered
   without a browser and human approval (E.3.2) — a risk that runs in the safe
   direction.
8. **Rate limits verified** (G): installation tokens start at PAT parity
   (5,000/hr), cap at 12,500, 15,000 on Enterprise Cloud. The binding constraint
   is the 80/min content-generating ceiling, not the hourly limit.
9. **Stateless invariant confirmed** (H): five agents, structurally enforced, no
   GitHub memory dependency. The exact-set tripwire at `:177-189` will fail until
   GitHub is added deliberately — as intended.
