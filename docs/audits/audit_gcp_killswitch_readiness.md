# Audit - GCP kill-switch capability readiness

> **Type:** pre-`integration_inputs.md` audit. **Pass discipline: AUDIT ONLY.**
> No code, no schema, no migration, no IAM policy, and no `integration_inputs.md`
> content was written in this pass.
>
> **Commit audited:** `008827b` (branch `main`, clean tree).
> **Date:** 2026-09-04. **Live sources** are cited with URL + fetch date; every
> codebase claim is cited `file:line`.
>
> **Scope note:** this audit covers **GCP on its own terms**. AWS is deliberately
> NOT audited here and its answers must not be assumed to transfer - the two
> differ materially in exactly the place that matters (GCP billing accounts span
> projects; see D.2 and the Q2.2b hazard restated there).

---

## 0. Hard exit gates - both tripped, reported rather than worked around

The instructions for this pass defined two conditions that require stopping and
reporting instead of proceeding on an assumption. **Both fired.**

### Gate 1 (TRIPPED): no concrete demo-script definition of a GCP kill-switch exists

There is **no artifact in this repository** that says what a GCP kill-switch
does. Not a VM stop, not a billing disable, not an IAM revocation, not a Cloud
Run pause. See section C for the full evidence. No action was invented; section D
describes both architectures generically, and section C.4 lists candidate actions
**explicitly marked as not-found-in-repo research**, not as findings.

### Gate 2 (TRIPPED): `oauth_credentials` is structurally incompatible with GCP's WIF model

`oauth_credentials.encrypted_access_token` is `TEXT NOT NULL`
(`migrations/versions/0021_oauth_credentials.py:91`). Workload Identity
Federation stores **no access token at all** - a short-lived token is minted per
call at Google's Security Token Service from a trust relationship
(docs.cloud.google.com/iam/docs/workload-identity-federation, fetched
2026-09-04). There is no honest value for that column, and there is no refresh
token either. This is presented in section B as a **schema decision for the
owner**; nothing was silently redesigned.

### One further gate, self-imposed and worth stating

`Skylize_Design_Specification.md` was named in the review brief as a place to look
for demo intent. **It does not exist in this repository** - `git ls-files` matches
nothing for `specification` or `Skylize_*`. If that document exists outside the
tree, this audit could not read it and its contents are UNVERIFIED here. That is
the single largest evidence gap in section C.

---

## A. What exists today and is genuinely reusable

### A.1 The provider-agnostic OAuth core (f6360b4, extended 8f147d4)

The infrastructure is real, wired, and honestly provider-agnostic. It is worth
being precise about which half of it is reusable for GCP, because the answer
differs sharply between the two architectures in section D.

| Component | Location | Reusable for GCP? |
|---|---|---|
| `OAuthProviderConfig` (endpoint, client creds, 2 parse hooks, `auth_style`) | `src/skylize/app/credentials/oauth.py:167-215` | Only on the user-delegated-OAuth variant (B.3) |
| `auth_style` body/header switch (added 8f147d4) | `oauth.py:191-213`, `oauth.py:218-256` | Yes if OAuth at all; Google uses `body` |
| `evaluate_grant` freshness classifier (pure) | `oauth.py:267-297` | No under WIF - nothing stored to classify |
| `ensure_fresh` / `access_token` | `oauth.py:340-378`, `:381-397` | No under WIF - no stored grant to refresh |
| `mark_revoked_by_provider` (added 8f147d4) | `oauth.py:399+` | Conceptually yes; see B.5 |
| Fernet encryption + `key_id` rotation column | `oauth.py:29-31`, `0021:90`, `:117-120` | Only if there is a secret to encrypt |
| RLS `tenant_isolation` policy pattern | `0021:128-136` | **Yes - reusable on any new table** |
| `(org_id, provider, label)` identity cardinality | `0021:107-110` | **Yes - the tenancy shape fits GCP** |

The tenancy and isolation half of f6360b4 is reusable under **both** section D
paths. The credential-payload half is reusable under **neither** WIF variant.

### A.2 Google-specific surface that exists

`src/skylize/app/credentials/google_provider.py` is the entire Google-specific
surface: token URL (`:27`), the single `drive.file` scope (`:30`), the provider
key `"google_drive"` (`:32`), and a builder (`:35-52`). Google's RFC-6749
conformance means both parser hooks are left at their defaults (`:50-51`).

**A real coupling hazard, previously unrecorded.** The platform client
credentials are named generically - `google_oauth_client_id` /
`google_oauth_client_secret` (`src/skylize/config.py:140-141`), resolved by
`resolve_google_drive_config` (`src/skylize/bootstrap.py:185-216`), which states
"Skylize registers ONE Google OAuth application and customers authorize into it"
(`bootstrap.py:190-193`). If a GCP connector reused that same OAuth app to
request Cloud scopes, it would move **one shared app** into a heavier
verification tier - and `google_provider.py:14-19` already records that widening
Drive's scope "is a cost and compliance decision, not a code change". See B.3.

### A.3 The three ToolProxy gates

Three opt-in, declarative pre-dispatch gates exist and would all be relevant to a
GCP tool:

- `ToolSpendProfile` - moves real money (`src/skylize/tools/base.py:65-87`)
- `ToolPermissionProfile` - elevated action, org allow-list keyed by
  `action_class`, **deny by default** (`tools/base.py:89-122`;
  `migrations/versions/0022_org_permission_grants.py:33-38`)
- `ToolOAuthProfile` - depends on a live grant (`tools/base.py:124-141`)

Dispatch order is OAuth, then permission, then spend reservation
(`src/skylize/tools/proxy.py:245-248`, `:265-267`), each with recorded reasoning.
All three refuse callable predicates on purpose, so a gate stays inspectable in a
registry entry (`tools/base.py:78-81`, `:107-110`).

`ToolPermissionProfile`'s deny-by-default allow-list is the closest existing
analogue to "which GCP resources may this org's agents touch", and it is the one
piece of the connector stack that transfers to GCP essentially unchanged.

### A.4 The existing kill-switch - and what it actually is

This is the finding that reframes the whole question.

The kill switch in this codebase **stops Skylize's own agents**. It does not, and
has never been specified to, act on anything outside the platform:

- Scopes are `{agent, department, tenant, platform}`
  (`src/skylize/edge/routes/kill_switch.py:16`;
  `docs/04_decision_engine/kill_switch_protocol.md:29-38`).
- Engaging revokes all governance tokens in scope, sets scope live-state to
  `killed`, and stops the Orchestrator minting new tokens
  (`kill_switch_protocol.md:49-63`;
  `src/skylize/app/governance/authority.py:619-649`).
- Enforcement is decentralised: the tool proxy and every adapter reject at
  token-validation step 3 (`kill_switch_protocol.md:18-27`).
- Restart safety via `rehydrate()` and cross-replica propagation over Redis
  (`docs/10_investor_materials/thiel_fellowship_technical_brief.md:182-194`).

So "GCP kill-switch" is a **category shift**, not an extension. The existing
mechanism guarantees *Skylize stops acting*. A GCP kill-switch would guarantee
*a customer's infrastructure stops running*. Those are different products with
different failure modes, and the repo currently specifies only the first.

### A.5 The budget-ceiling machinery that a "CFO Test" would actually exercise

`org_spend_ceiling` (migration 0014) is the org-wide **LLM money** ceiling in
micro-USD, read only by the pre-call spend gate, deny-on-absence
(`0014:1-40`). `budget_ledger` is business spend and is explicitly **UNFED in
production** (`0014:7-12`). ADR-0006 forbids conflating them.

This matters for the demo framing: real-time budget-ceiling enforcement is built
for **Skylize's own LLM spend**, not for a customer's cloud bill. A GCP kill
switch triggered by a cloud-spend ceiling would need a fourth spend concept, or
an explicit decision to feed one of the existing three from GCP billing data.
Neither exists. ADR-0006's rule against conflating ledgers makes this a
deliberate decision, not an implementation detail.

### A.6 What is NOT built and is often assumed to be

**There is no OAuth broker.** No authorization-code callback route exists
anywhere: `src/skylize/edge/routes/` contains no OAuth/connect/callback module,
and a repo-wide grep for `authorization_code`, `redirect_uri`, or `/callback`
across `src/` returns **nothing**. What f6360b4 built is the *storage, refresh,
freshness, revocation-state and use* half. The *grant acquisition* half - the
flow by which a customer authorizes Skylize in the first place - is unbuilt for
every provider shipped so far.

This is load-bearing for GCP: under section D path A there is no consent screen
to build (WIF has none), but under the user-delegated OAuth variant (B.3) the
missing broker is a hard prerequisite, not a detail.

### A.7 Absence of GCP, positively verified

Repo-wide greps over tracked files only (`git ls-files`, vendored bundles
excluded) at `008827b`:

- `gcp|google[-_ ]?cloud|gcloud`: **12 hits, all documentation**, all in
  `integration_inputs.md`, `stripe_connector_design.md`, or prior audits. **Zero
  in `src/`, `tests/`, `migrations/`, `infra/`, `policy/`.**
- `service_account|workload identity|assume_role|AssumeRole|STS`: **zero
  substantive hits.** Only incidental substring matches on unrelated words.
- `pyproject.toml` dependencies (`:15-91`): **no `google-cloud-*` package**, and
  no `boto3`/`aioboto3` either - `aioboto3` is named only in a forward-looking
  comment (`pyproject.toml:11-13`).
- `infra/terraform/staging/` is entirely **AWS** (ecs, rds, elasticache, alb,
  ecr, iam, secrets, vpc modules) for Skylize's own hosting.

There is **no dead or partial GCP code to clean up**. This is a greenfield
decision, which is the good news in this audit.

### A.8 Section 2.2's recorded status

`docs/06_integrations/integration_inputs.md:282-284` marks section 2.2 (AWS/GCP)
`[OWNER-DECISION-REQUIRED]` - **HARD BLOCK**, "Not resolvable from existing
docs." The 2.0 classification table cannot even classify GCP as org-level or
platform-level: `| GCP | UNRESOLVED - see 2.2 |` (`:197`). The sign-off block at
`:1115` is unsigned. Three open questions are already recorded there - Q2.2a
target account (`:295-305`), Q2.2b resource/verb allowlist (`:307-312`), Q2.2c
kill-switch trigger source (`:313-321`).

**This audit does not resolve any of them.** It supplies the GCP-specific
evidence Q2.2a was blocked for.

---

## B. What is fundamentally incompatible, and why

### B.1 The credential-model mismatch, stated precisely

`oauth_credentials` was designed for a **user-delegated OAuth 2.0
authorization-code grant**: a customer clicks consent, Skylize receives and
stores an access token plus a refresh token, and refreshes on demand inside the
tenant session. Every column encodes that assumption (`0021:84-102`).

GCP infrastructure control in Google's recommended pattern is a **federated trust
relationship**, not a stored grant. The vendor presents an OIDC ID token it
signs itself; Google's STS verifies it against the customer's configured pool and
returns a short-lived token (docs.cloud.google.com/iam/docs/workload-identity-federation,
fetched 2026-09-04: "You provide a credential from your IdP to the Security Token
Service, which verifies the identity on the credential, and then returns a
federated token in exchange").

### B.2 Column-by-column, against WIF

| Column (`0021`) | WIF reality |
|---|---|
| `encrypted_access_token TEXT NOT NULL` (`:91`) | **No token is ever stored.** Minted per call at STS. `NOT NULL` has no honest value. |
| `encrypted_refresh_token TEXT` (`:92`) | **No refresh-token concept exists.** Always NULL, permanently. |
| `expires_at TIMESTAMPTZ` (`:93`, nullable since `0023`) | Wrong direction. `0023` relaxed this for grants that *never* expire (Notion); WIF's tokens expire in **~an hour** and are *never persisted*. NULL here would assert "does not expire by time" (`0023:26-30`) - the opposite of true. |
| `scopes TEXT[]` (`:94`) | Authorization is **IAM role bindings in the customer's project**, not scopes Skylize holds. |
| `connection_state` (`:95`, `:100-101`) | No token to mark dead. Revocation = the customer deletes the pool/provider or removes the binding. See B.5. |
| `key_id` (`:90`) | Nothing to encrypt. WIF config (pool resource name, audience, service-account email, project id) is **non-secret**. |
| `(org_id, provider, label)` unique (`:107-110`) | **Fits.** One GCP connection per org is the right cardinality. |
| RLS `tenant_isolation` (`:128-136`) | **Fits.** Reusable verbatim. |

**Conclusion, stated as a decision and not taken:** storing WIF configuration in
`oauth_credentials` would require making the table's central column meaningless,
would put non-secret configuration through an encryption path built for secrets,
and would make `evaluate_grant` (`oauth.py:267-297`) - which runs on every
governed tool call - answer a question that does not apply. **A separate table is
the structurally honest option, but that is the owner's schema decision to make,
not this audit's.** The precedent for exactly this reasoning already exists in
this repo: `0021:10-20` refused to widen `org_credentials` for OAuth on the same
grounds, in nearly the same words.

### B.3 The other half of the question: does GCP's OAuth2 make it compatible as-is?

The review brief asked this explicitly, and the honest answer is **technically
yes, architecturally no**.

Google **does** publish user-delegated OAuth scopes that grant cloud resource
control (developers.google.com/identity/protocols/oauth2/scopes, fetched
2026-09-04):

- `https://www.googleapis.com/auth/cloud-platform` - "See, edit, configure, and
  delete your Google Cloud data..."
- `https://www.googleapis.com/auth/compute` - "View and manage your Google
  Compute Engine resources"
- `https://www.googleapis.com/auth/cloud-billing` - "View and manage your Google
  Cloud Platform billing accounts"

A grant with these scopes **would** fit `oauth_credentials` as-is: access token,
refresh token, `expires_in`, standard RFC-6749 refresh, `invalid_grant` on
revocation - identical to Drive. `google_provider.py` would need one more builder
function and a distinct provider key. That path is real and should not be
dismissed.

Four reasons it is nonetheless a poor fit for a governance product:

1. **Attribution.** A user-delegated token makes every agent action appear in
   Google Cloud Audit Logs as *the consenting human*. This repo already rejected
   that exact trade for Slack: a user token "makes agent actions
   indistinguishable from a human's in Slack's own audit trail, which contradicts
   the platform's audit posture" (`integration_inputs.md`, Q2.3b). The argument
   is stronger, not weaker, when the action is stopping production infrastructure.
2. **Scope granularity is coarse.** The narrowest useful scope, `compute`, is
   "view and manage **your** Compute Engine resources" - project-wide, all
   instances. There is no `drive.file`-style narrow tier. GCP expresses least
   privilege through **IAM role bindings and IAM Conditions**, which a
   user-delegated OAuth token bypasses by inheriting the consenting user's full
   IAM.
3. **Verification cost lands on the shared Google app.** Sensitive/restricted
   scopes require Google OAuth app verification, justification, and a demo video;
   restricted scopes may require a security assessment, and unverified apps are
   capped at 100 new users
   (developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification,
   via search 2026-09-04). Because `config.py:140-141` holds **one** Google client
   id/secret pair, this cost would land on the same OAuth app Drive uses -
   precisely the compliance escalation `google_provider.py:14-19` was written to
   prevent. A second, separate Google OAuth app would be required to isolate it.
4. **Grant durability.** A user grant dies when that employee leaves the customer
   org. A kill switch whose credential can vanish because someone changed jobs is
   not a kill switch.

**This is a genuine third option** (call it A-prime) and is recorded as such in
D.3, not folded into either path.

### B.4 What WIF actually costs Skylize - the finding most likely to be underestimated

Google's own SaaS-vendor guide
(docs.cloud.google.com/iam/docs/use-workload-identity-federation-to-let-customers-access-their-cloud-resources,
fetched 2026-09-04) puts the burden on the **vendor**, not the customer:

- The vendor must "implement OpenID Connect functionality to issue ID tokens".
- The vendor must "provide publicly accessible OpenID provider metadata at
  `/.well-known/openid-configuration`" and publish a **JWKS** for signature
  verification.
- The vendor must "use tenant-specific issuer URLs" to prevent cross-tenant
  spoofing attacks.
- The vendor must "limit ID token lifetime to 60 minutes" and let customers
  specify the token audience.

The **customer** creates the workload identity pool and provider in their own
project, points it at Skylize's issuer and JWKS, and creates the IAM role
bindings.

**Skylize would have to become a public OIDC identity provider.** No such surface
exists: `src/skylize/edge/routes/` has no discovery or JWKS route, and per-tenant
issuer URLs imply per-tenant public endpoints. That is a new externally-reachable
trust boundary on the edge, and it is a materially larger piece of work than any
connector shipped in Tier 0 so far.

**An adjacency that must not be mistaken for a shortcut.** Skylize already
operates an ECDSA P-384 signing key for governance tokens
(`app/governance/authority.py`; `thiel_fellowship_technical_brief.md:172-176`).
It is tempting to reuse it. **Do not treat that as settled reuse:** governance
tokens are an internal authority artifact validated inside the platform, whereas
an OIDC JWKS publishes a verification key on a public trust boundary and invites
Google's STS to accept anything that key signs. Whether these share a key, a
keyring, or nothing at all is a security decision for the owner
(`chief_security_officer` is the named owner of the kill-switch protocol,
`kill_switch_protocol.md:4`).

### B.5 The revocation-detection gap, inherited and widened

`0023:32-39` already records a real gap: for a grant with no expiry, no refresh
is ever attempted, so the refresh-failure revocation path is unreachable and
death is observable **only when a live API call fails**;
`mark_revoked_by_provider` exists as the primitive a connector must call, and "a
connector that never calls it will keep a dead grant marked 'valid' forever".

Under WIF this gap is **structural rather than incidental**. There is no stored
credential at all, so there is nothing to mark. A customer can silently delete
the pool, delete the provider, or remove the role binding, and Skylize learns
only on the next call - which, for a kill switch, is *the moment it is needed*.

**This is the sharpest safety finding in this audit.** A kill switch that
discovers it has lost its authority at the instant of use is worse than no kill
switch, because it was relied upon. Any GCP design must answer: how is
kill-switch authority **proactively** health-checked, and what happens to the
governance posture when the probe fails? Nothing in the current infrastructure
does proactive credential health-checking for any provider.

---

## C. The concrete kill-switch action - GAP (Gate 1)

### C.1 What was searched

Repo-wide over tracked files: `kill[-_ ]?switch|killswitch` (436 matching lines,
reviewed by file), `CFO Test`, plus targeted reads of
`docs/04_decision_engine/kill_switch_protocol.md`, `docs/01_vision/*`,
`docs/10_investor_materials/*`, `docs/11_product/*`, and the two demo components
under `website/src/components/`.

### C.2 What "kill-switch" concretely means in this repo

Every one of the 436 hits, without exception, refers to the internal governance
control described in A.4: revoke tokens in scope, flip live-state, stop minting,
quarantine in-flight events (`kill_switch_protocol.md:49-63`). Its permanent
invariants are "human can always stop, kill overrides all authority, never
self-clears, fully audited" (`:117-119`). **Not one refers to an external cloud
resource.**

### C.3 What the "CFO Test" is, as recorded

`CFO Test` appears **exactly once** in the entire tracked tree:

> `docs/09_development/audit_feed_endpoint.md:43-44` - "`governance_token_id` is
> a real minted P-384 token id on governed runs - linking it in the UI is the
> 'CFO Test' money shot."

As recorded in this repo, the CFO Test is an **audit-provenance** demonstration:
a CFO can click from an action to the cryptographic token that authorized it. It
is not a cloud-infrastructure demonstration.

The only wired demo agrees. `website/src/components/console/governance-demo.tsx:19-23`
documents "the two-minute governance story": run an agent, the decision gate's
verdict is the headline, deferred work lands in the pending panel, a human
approval executes it, the deliverable appears with its provenance. Greps for
`kill|gcp|vm|instance|budget|ceiling` across both demo components return
**nothing**.

**Finding:** the repository's demo intent, as written, is *governance provenance*
- token, gate, HITL, audit trail. The GCP kill-switch framing in the review brief
is **not corroborated by any artifact in this tree**. It may well exist in the
owner's head or in an out-of-tree document (see Gate 3 on the missing
`Skylize_Design_Specification.md`); this audit simply cannot confirm it, and
records the divergence rather than papering over it.

### C.4 Candidate actions - RESEARCH ONLY, not found in repo

Presented so the owner can choose, with blast radius evidenced. **None of these
is a repo finding.** Live sources fetched 2026-09-04.

**(i) Stop a Compute Engine instance** - permission `compute.instances.stop` on
the instance; predefined role `roles/compute.instanceAdmin.v1`
(docs.cloud.google.com/compute/docs/access/iam;
/compute/docs/instances/stop-start-instance). Can be bound **at the instance
resource level** rather than project-wide, and further narrowed with **IAM
Conditions** (docs.cloud.google.com/iam/docs/resource-hierarchy-access-control).
**Reversible** - the instance restarts. Smallest credible least-privilege ask.

**(ii) Disable project billing** - `projects.updateBillingInfo` with
`billingAccountName` set empty
(docs.cloud.google.com/billing/docs/reference/rest/v1/projects/updateBillingInfo).
This is Google's own documented budget-enforcement pattern
(docs.cloud.google.com/billing/docs/how-to/disable-billing-with-notifications),
so it maps most directly onto a "budget-ceiling enforcement" story. **Its blast
radius is severe and Google says so in its own words:**

> "This tutorial removes Cloud Billing from your project, shutting down all
> resources. **Resources might be irretrievably deleted.**"

> "There's a delay between incurring costs and receiving budget notifications, so
> you might incur additional costs for usage that hasn't arrived at the time that
> all services are stopped."

Recovery is manual with "no guarantee of service recovery". Critically for the
least-privilege question: the documented pattern grants **`roles/billing.admin`
on the Cloud Billing account** - which is administrative authority over *every
project attached to that billing account*, not over one project. **That is the
opposite of a scoped ask**, and it is a far larger grant than option (i).

**(iii) Revoke an IAM role binding / pause a Cloud Run service** - not researched
in depth this pass. Flagged as unexplored rather than dismissed.

**The trade-off the owner is actually choosing between:** (ii) is the most
*rhetorically* convincing to a CFO and the most *aligned* with a budget-ceiling
narrative, and it is simultaneously the one Google warns may irretrievably
destroy data and the one requiring the broadest possible IAM grant. (i) is
contained, reversible, and bindable to a single instance, and is a visibly
smaller demonstration. There is no option that is both maximally dramatic and
minimally privileged.

---

## D. The two architectures

Presented as genuinely separate designs. **This audit does not recommend one.**
Q2.2a (`integration_inputs.md:295-305`) remains the owner's decision; the table
there already carries a `[RESEARCH-SUGGESTED]` lean toward B, recorded before any
GCP-specific evidence existed - the evidence below is offered to test that lean,
not to confirm it.

### D.1 Path A - act on the customer's own GCP project (Workload Identity Federation)

**Mechanics.** Skylize publishes tenant-specific OIDC issuer URLs, discovery
documents and JWKS. The customer creates a workload identity pool and provider in
their project pointed at Skylize's issuer, and creates IAM role bindings - either
**direct resource access** ("grant to your external identity access directly on a
Google Cloud resource using resource-specific roles") or **service account
impersonation**. At call time Skylize signs an ID token (<=60 min lifetime),
exchanges it at STS for a short-lived federated token, and calls the API. (Both
Google docs, fetched 2026-09-04.)

**Schema.** Needs its own table. Content is **non-secret configuration**: project
id, pool/provider resource names, audience, impersonated service-account email or
a direct-binding marker, and the allowlisted resource/verb set (Q2.2b). Reuses
`0021`'s `(org_id, provider, label)` cardinality and its RLS policy; reuses none
of its credential columns. Nothing to encrypt, nothing to refresh, nothing to
mark revoked.

**Security posture.** Strongest available: **no long-lived downloadable
credential exists at all**, so there is no service-account JSON key to leak - the
explicit reason Google recommends WIF over keys
(docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys).
Least privilege is expressible precisely, down to a single instance with IAM
Conditions (C.4.i). Actions appear in the **customer's** Cloud Audit Logs under a
distinct federated identity - excellent attribution, and a genuine selling point
for a governance product.

**Cost to Skylize.** Becoming a public OIDC IdP (B.4). Per-tenant issuer
endpoints. A key-management decision on the edge that touches the governance
signing key question. Plus customer-facing onboarding documentation and a
Terraform/gcloud setup path the customer must run.

**Blast radius.** Customer production. Under action (ii) this includes an action
Google itself says may irretrievably delete resources.

**Demo credibility.** High. The CFO watches their own infrastructure stop.

**Customer trust ask.** High, and honestly so. The customer establishes a
federation trust to Skylize's identity provider and grants IAM in production. For
action (i) that ask is defensible and narrow. For action (ii) the ask is
`roles/billing.admin` on their billing account, which many customers' security
teams will simply decline - and declining it *late in a sales cycle* is the risk.

**The governance recursion nobody has specified.** Q2.2c already states the
requirement: the trigger must be the Decision Engine's T4-class hard-deny path
reaching the existing kill-switch surface, "**not** a second independent path
owned by the connector" (`integration_inputs.md:313-321`; T4 is defined at
`docs/04_decision_engine/policy_inputs.md:141` as auto-reject before execution,
no human wait). Under path A this produces a question the repo has never faced:
Skylize's platform-scope kill switch stops **Skylize's agents** - what stops an
in-flight action against **someone else's production**? The 2.2 table names this
("a kill switch must be able to stop an in-flight action against someone else's
production") but nothing in `kill_switch_protocol.md` addresses it.

### D.2 Path B - act on a Skylize-owned sandbox GCP project

**Mechanics.** Skylize owns the project and the resources. Nothing is required
from the customer.

**Schema.** **No per-tenant credential row at all.** Under the classification at
`integration_inputs.md:186-189` this is a **platform-level** credential, and the
repo has an established rule that these are barred from the tenant vault. It
follows the `SKYLIZE_*`-env-var-on-`Settings` pattern used by every other
platform secret (`src/skylize/config.py:128-160`), exactly as the Slack decision
resolved (Q2.3a). `oauth_credentials` is not involved.

**Security posture.** Good, and can be made key-free too: Skylize's own workload
runs on AWS ECS (`infra/terraform/staging/modules/ecs/`), and WIF supports AWS as
an identity provider (docs.cloud.google.com/iam/docs/workload-identity-federation),
so a Skylize-owned sandbox can be reached without a downloadable service-account
key either. **"No long-lived key" is achievable on both paths** and is therefore
*not* a differentiator between them - a point worth stating because it is easy to
assume otherwise.

**The GCP-specific hazard, sharper than the recorded AWS version.** Q2.2b already
warns that under option B "an over-broad ECS grant reaches Skylize's own control
plane - the sandbox must be a separate account, not a separate cluster"
(`integration_inputs.md:307-312`). **In GCP this is worse.** A Cloud Billing
account spans many projects, and `roles/billing.admin` is granted *on the billing
account* (C.4.ii). A sandbox that is merely a separate **project** under
Skylize's **existing billing account** would put a billing-disable demo one
mis-scoped binding away from Skylize's own infrastructure. The sandbox must be a
separate project **under a separate billing account**. That is a GCP-specific
constraint with no AWS analogue and it must not be lost in translation.

**Blast radius.** Contained to Skylize's own sandbox.

**Demo credibility.** Lower, and the review brief's instinct is worth taking
seriously: a CFO watching Skylize stop Skylize's own VM is watching a simulation.
Whether that is disqualifying is a sales judgement this audit cannot make - but
note that C.3's evidence suggests the demo's *recorded* centerpiece is the audit
trail and the governance token, not the resource, and a token-provenance story is
equally convincing against a sandbox.

**Customer trust ask.** None.

**Validation budget.** See D.5 - the free-credit path funds this path only.

### D.3 Path A-prime - customer's project via user-delegated OAuth

Recorded as a real third option, not folded into A (see B.3). **Only path where
`oauth_credentials` fits as-is.** Cheapest to build *given the existing
infrastructure*, but requires the unbuilt OAuth broker (A.6), a second Google
OAuth app to avoid dragging Drive into a heavier verification tier (A.2, B.3.3),
and it accepts human-attributed actions and coarse project-wide scopes - a trade
this repo has already rejected once on audit-posture grounds (Q2.3b).

### D.4 Comparison

| | A: customer project (WIF) | A': customer project (user OAuth) | B: Skylize sandbox |
|---|---|---|---|
| Customer must do | create pool/provider + IAM bindings | click consent | nothing |
| Skylize must build | **public OIDC IdP** (new edge surface) | **OAuth broker** (unbuilt for all providers) | config + connector only |
| Long-lived key stored | none | refresh token (encrypted) | none (AWS->GCP WIF) |
| `oauth_credentials` fit | **no** - new table | **yes, as-is** | n/a - platform secret |
| Least privilege | precise (resource-level + IAM Conditions) | coarse (project-wide scopes) | precise, but on our own resources |
| Attribution in customer logs | distinct federated identity | **a human employee** | n/a |
| Credential dies when | customer removes binding (undetected until use) | employee leaves | never |
| Blast radius | customer production | customer production | Skylize sandbox |
| Demo credibility | high | high | lower |
| Trust ask | high | medium-high | none |

### D.5 GCP free credit - current, but only half-relevant

**Current, verified.** $300 in Welcome credit over **90 days**
(docs.cloud.google.com/free/docs/free-cloud-features, page last updated
2026-08-26, fetched 2026-09-04). Eligibility requires never having been a paying
Google Cloud user and never having signed up for the trial before. Free Tier
separately includes 1 non-preemptible `e2-micro` VM per month in certain US
regions. Excluded: GPUs, Marketplace, quota increases, Windows Server VMs,
VMware Engine.

**Three caveats that make it partially stale as a decision input:**

1. **It funds path B only.** Path A's cost is a public OIDC identity provider
   (B.4) - engineering time, not GCP spend. No amount of GCP credit reduces it.
   If the free credit was the reason to lean B, that reasoning covers one path's
   costs and is silent on the other's.
2. **90 days is a hard wall.** "If you don't upgrade to a Paid billing account
   before 90 days pass... your Free Trial billing account will be closed and all
   of its associated projects and resources will be stopped." A demo environment
   that self-terminates mid-sales-cycle is a liability, not a budget. Adequate
   for **prototyping the question**; not a substrate for a live demo.
3. **It interacts badly with action (ii).** A billing-disable demo run against a
   Free Trial billing account entangles the demo's mechanism with the trial's own
   closure semantics. If billing-disable is the chosen action, the sandbox
   probably needs a real paid billing account from day one - which, per D.2, must
   be a **separate** billing account anyway.

**Verdict:** the offer is current and adequate for *prototyping* path B. It is
not a reason to prefer B, and it does not fund a durable demo.

---

## E. Open questions requiring an owner decision

Numbered in this audit's own namespace (`GA-*`) to avoid pre-writing
`integration_inputs.md` content. Mapping to existing questions noted.

**GA-1 (blocks everything; = Q2.2a).** Customer project or Skylize sandbox?
Section D. Nothing downstream can be specified until this is fixed in writing.

**GA-2 (blocks GA-3; Gate 1).** **What does the GCP kill-switch actually do?**
No artifact in this repo answers this (section C). Until the concrete action is
named, the IAM grant cannot be derived and the blast radius cannot be assessed.
Candidates and their evidenced blast radius are in C.4. Note the trade-off is
real: the most convincing action is the one Google warns may irretrievably delete
resources and requires the broadest grant.

**GA-3 (= Q2.2b, GCP-specific).** The resource-and-verb allowlist. Q2.2b's demand
- "an allowlist of resource types **and** verbs is required; 'cloud access' is
not a specification" - applies unchanged. GCP-specific addition: state whether
the binding is project-level, resource-level, or resource-level plus IAM
Conditions (C.4.i).

**GA-4 (schema; Gate 2).** Given GA-1, does GCP get its **own table** (path A:
non-secret WIF config), **no table** (path B: platform secret on `Settings`), or
**reuse `oauth_credentials`** (path A-prime only)? Presented as a decision, not
taken. Section B.

**GA-5 (security, path A only).** Does Skylize become a public OIDC identity
provider, and if so does its JWKS share anything with the governance P-384
signing key? B.4 argues these should be treated as separate trust boundaries but
does not decide it. Owner: `chief_security_officer` per
`kill_switch_protocol.md:4`.

**GA-6 (safety; the sharpest finding).** How is kill-switch authority
**proactively** health-checked? Under WIF a customer can silently remove the
binding and Skylize learns at the moment of use (B.5). No provider in this repo
does proactive credential health-checking today. A kill switch that discovers it
has lost authority when invoked is worse than none, because it was relied upon.

**GA-7 (= Q2.2c, extended).** Q2.2c already requires the trigger be the Decision
Engine T4 hard-deny path reaching the existing kill-switch surface, never a
connector-owned mechanism. **Extension for path A:** what stops an in-flight
action against a *customer's* production, given the existing kill switch stops
only Skylize's agents (A.4)? `kill_switch_protocol.md` does not cover this.

**GA-8 (product framing).** Section A.5: real-time budget-ceiling enforcement
today is `org_spend_ceiling`, Skylize's **own LLM spend** in micro-USD.
`budget_ledger` is unfed and ADR-0006 forbids conflating them. If the CFO Test
means enforcing against a *customer's cloud bill*, that is a new spend concept.
Which is intended?

**GA-9 (evidence gap).** `Skylize_Design_Specification.md` is not in this
repository. If a demo script exists outside the tree, it should be brought in (or
its relevant section quoted into `integration_inputs.md` 2.2), because section
C's conclusion rests on its absence.

---

## F. Statelessness invariant - CONFIRMED, and it is load-bearing here

The review brief asked whether design intent requires memory access for
`cfo_agent`, `chief_security_officer`, `director_ai_safety`, `llm_safety_agent`,
`prompt_injection_agent`. **It does not, and this is enforced structurally.**

- `cfo_agent`: `memory_read_access=[]`, `memory_write_access=[]`
  (`src/skylize/contracts/mvp/finance.py:183-184`); tools are `llm.generate` and
  `utility.current_datetime` only (`:172-176`).
- The four safety agents: all `[]` / `[]`
  (`src/skylize/contracts/mvp/safety.py:28-29`, `:50-51`, `:72-73`, `:93-94`;
  module docstring `:4`).
- Enforced by `tests/contract/test_stateless_agents_no_oauth_access.py`, which
  asserts both halves - the contracts stay stateless (`:97-103`) and none of
  their tools carries a `ToolOAuthProfile` or `ToolPermissionProfile`
  (`:107-120`) - against the **fully wired** registry, a subtlety the file itself
  records as a previously-silent hole (`:12-18`).
- **Ran, not skipped**, at `008827b` on 2026-09-04: `30 passed`. This is a
  contract test with no Postgres dependency, so the CLAUDE.md skip-hazard does
  not apply.

**Do not confuse `cfo` with `cfo_agent`.** `cfo` (`finance.py:15`) *does* hold
memory access (`:30-31`). The stateless one is `cfo_agent` (`:163`).

**Why this matters for GCP.** `EXPECTED_OAUTH_TOOL_IDS` (`:48-57`) and
`EXPECTED_PERMISSION_TOOL_IDS` (`:68-71`) are explicit allowlists that "a new
connector has to be added here consciously" (`:46-47`). So **a GCP kill-switch
tool wired into `cfo_agent` would fail this test** - correctly. That is a
tripwire, not an obstacle, and it points the same direction Q2.2c already
requires: the kill-switch trigger belongs on the **governance path** (Decision
Engine T4 -> Governance Authority -> kill-switch surface), not as a tool in the
CFO agent's hands. GA-7 should be answered consistently with that.

---

## G. Summary

1. **Greenfield.** Zero GCP code, zero `google-cloud-*` dependency, zero
   `service_account`/WIF references. No cleanup needed (A.7).
2. **"Kill-switch" means something else here.** In this repo it stops Skylize's
   agents by revoking governance tokens, at scopes agent/department/tenant/
   platform. Acting on external infrastructure is a category shift, not an
   extension (A.4).
3. **No demo script defines a GCP action** (Gate 1). The only recorded "CFO Test"
   is an audit-provenance demo (`audit_feed_endpoint.md:44`), and the only wired
   demo is agent -> gate -> HITL -> provenance. Both paths are therefore described
   generically (C).
4. **`oauth_credentials` does not fit WIF** (Gate 2) - `encrypted_access_token
   NOT NULL` has no value when nothing is stored. Its tenancy and RLS half *does*
   fit. Presented as an owner schema decision (B).
5. **But GCP user-delegated OAuth would fit as-is** - a genuine third option with
   a different cost profile, recorded as path A-prime rather than dismissed (B.3,
   D.3).
6. **WIF's real cost is that Skylize must become a public OIDC identity
   provider** - discovery document, JWKS, per-tenant issuers. Larger than any
   Tier 0 connector so far, and it raises a governance-key question (B.4).
7. **Blast radius is action-dependent and the extremes are far apart.** VM stop:
   reversible, bindable to one instance. Billing disable: Google's own words are
   "resources might be irretrievably deleted", recovery is manual with "no
   guarantee of service recovery", and it needs `roles/billing.admin` on the
   billing account (C.4).
8. **GCP-specific sandbox hazard:** billing accounts span projects, so a sandbox
   must be a separate project under a **separate billing account** - sharper than
   the AWS version of this warning already recorded at Q2.2b (D.2).
9. **Revocation detection is the sharpest safety gap.** Under WIF nothing is
   stored to mark dead; loss of authority surfaces only at the moment of use. No
   proactive health-check exists for any provider (B.5, GA-6).
10. **Free credit is current but half-relevant:** $300/90 days, verified
    2026-09-04. Funds prototyping path B; does nothing for path A's cost; the
    90-day closure makes it unsuitable as a live demo substrate (D.5).
11. **Statelessness confirmed and enforced**, and the contract test is a tripwire
    that will correctly fire if a GCP tool is wired into `cfo_agent` (F).

**Section 2.2 remains a HARD BLOCK.** This audit supplies GCP-specific evidence
for Q2.2a/b/c; it resolves none of them.

---

## Sources

Codebase: commit `008827b`, cited `file:line` throughout.

Live, all fetched 2026-09-04:
- [Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation)
- [Let customers access their Google Cloud resources from your product or service](https://docs.cloud.google.com/iam/docs/use-workload-identity-federation-to-let-customers-access-their-cloud-resources)
- [Best practices for managing service account keys](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys)
- [Using resource hierarchy for access control](https://docs.cloud.google.com/iam/docs/resource-hierarchy-access-control)
- [Compute Engine IAM roles and permissions](https://docs.cloud.google.com/compute/docs/access/iam)
- [Stop or restart a Compute Engine instance](https://docs.cloud.google.com/compute/docs/instances/stop-start-instance)
- [Disable billing usage with notifications](https://docs.cloud.google.com/billing/docs/how-to/disable-billing-with-notifications)
- [Method: projects.updateBillingInfo](https://docs.cloud.google.com/billing/docs/reference/rest/v1/projects/updateBillingInfo)
- [OAuth 2.0 Scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes)
- [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
- [Free Google Cloud features and trial offer](https://docs.cloud.google.com/free/docs/free-cloud-features) (page last updated 2026-08-26)
