# Skylize - Integration Inputs (Connector Rules for OAuth Broker + CredentialVault Extension)

> **Status: DRAFT - AWAITING OWNER APPROVAL (Mr. Ozkan)**
> **Date compiled:** 2026-08-22
> **Scope:** Stripe, AWS, GCP, Slack, GitHub.
>
> This file is the **sole source of connector rules** for the OAuth broker and any
> CredentialVault scope extension. Every connector, broker endpoint, provider scope
> string, and `org_credentials` schema change MUST cite a line in this file. No
> connector code may exist without a corresponding approved entry here. Same
> discipline as `docs/04_decision_engine/policy_inputs.md`: no value, scope, or
> account target enters code without a traceable, owner-approved source.
>
> **How to read this file.** Every concrete claim below is marked with one of:
> - `[CODE-VERIFIED]` - extracted directly from the current codebase. Ground truth.
> - `[RESEARCH-SUGGESTED]` - a defensible default. **NOT yet approved.**
> - `[OWNER-DECISION-REQUIRED]` - a design choice only the owner can make; no default
>   is safe to assume.
>
> Nothing here is `[APPROVED]` until the owner changes the banner on each section.
> **Connector implementation is BLOCKED until the relevant section reads `[APPROVED]`.**

---

## Global combining principle

`[RESEARCH-SUGGESTED]` A connector is an **egress** surface, so it inherits the
platform's existing invariants rather than defining new ones:

1. **Most-restrictive-wins.** The effective permission for an agent-initiated
   external action is the intersection of: the agent contract's `allowed_tools`,
   the GovernanceToken `scope`, the human principal's compiled authority (when the
   token is principal-bound), and the provider grant actually held in
   `org_credentials`. Any one of them denying is a deny.
2. **Attenuation-only.** A connector may never widen authority. A provider token
   stored with broad scopes does not grant the agent those scopes; the agent's
   ceiling is still the contract/token/principal intersection.
3. **Fail closed.** Missing credential, missing ceiling, unreadable provider scope,
   or an unrecognized action class denies. Absence is never an implicit allow.

---

## 1.0 - Current state

> **Section status: `[CODE-VERIFIED]` - no owner decision needed; this is a fact record.**

### 1.0.1 What exists

- **`org_credentials` table** (`migrations/versions/0007_org_credentials.py:37-46`):
  `id`, `org_id`, `provider`, `label`, `encrypted_value`, `metadata_json`,
  `created_at`, `rotated_at`. RLS ENABLED and FORCED (`:59-61`) with a
  `tenant_isolation` policy on `current_setting('skylize.org_id')` for both USING
  and WITH CHECK (`:63-68`); `skylize_app` granted SELECT/INSERT/UPDATE/DELETE
  (`:70-72`). Unique on `(org_id, provider, label)` (`:54-57`).
- **`CredentialVault`** (`src/skylize/app/credentials/vault.py:20-144`) -
  store / retrieve / rotate / delete / delete_by_id / list_providers. Every mutation
  writes an audit record (`:53-59`, `:93-99`, `:114-120`); `retrieve` is explicitly
  documented as never appearing in logs (`:69`).
- **`PgCredentialRepository`** (`src/skylize/dal/credentials.py:64-123`) - every
  statement runs inside `tenant_session(org_id)` and additionally carries an
  explicit `org_id` predicate, so RLS and the query agree.
- **Credential routes** (`src/skylize/edge/routes/credentials.py:11-14`) - store /
  list / resolve / delete, `owner`/`admin` gated, `resolve` additionally
  rate-limited (`:28`).
- **Exactly one real connector: HubSpot** (`src/skylize/tools/builtin/hubspot_tools.py`).
  It is the precedent to follow: token resolved per call, never cached across calls
  (`:3-6`), one HTTP client per call so a token cannot leak across orgs (`:80-83`),
  "not connected" degraded into a clean `ToolExecutionError` rather than a 500
  (`:131-138`).

### 1.0.2 What does NOT exist

`[CODE-VERIFIED]` Verified by repo-wide `git grep` on 2026-08-22:

- **No OAuth broker.** No authorize endpoint, no callback route, no state/PKCE
  handling, no token-refresh loop. `src/skylize/edge/routes/` contains no OAuth
  route. The only two occurrences of "oauth" in the tree are prose:
  `docs/06_integrations/shopify.md:26` and `migrations/versions/0007_org_credentials.py:8`.
  **Faz A-D of the OAuth broker exist only as plans; zero lines are implemented.**
- **No SDK or client for any of the five providers.** `pyproject.toml:14` records
  that `aioboto3` is deliberately deferred to "the sprint that first imports it";
  no `stripe`, `boto3`, `google-cloud-*`, `slack_sdk`, or GitHub client is a
  dependency. `website/package.json` has no Stripe/Slack/Octokit/AWS/Google package.
- **No KMS.** `git grep -i "kms|key_management|envelope_encrypt"` over `src/` and
  `migrations/` returns nothing. At-rest protection is a **single platform-wide
  Fernet key** (`src/skylize/bootstrap.py:322-323`, `src/skylize/config.py:80`),
  falling back to an ephemeral generated key when unset. There is no per-tenant key,
  no key ref column, and no rotation of the master key.
- **`stripe.refund` is not a connector.** It appears only as an illustrative scope
  string in docs, models, and tests (e.g. `src/skylize/app/principal/models.py:56,74`;
  `tests/unit/test_principal_authority.py:44`;
  `tests/contract/test_cowork_contract.py:89`). No handler, no registration, no egress.
- **No n8n workflow WF-03 / WF-04.** `git grep "WF-03|WF-04|WF_03|WF_04"` returns
  nothing repo-wide. No CFO Finance n8n workflow exists to duplicate or reconcile.

### 1.0.3 Known drift (report only, not fixed this pass)

`[CODE-VERIFIED]` `src/skylize/dal/credentials.py:4` says the table comes from
"migration 0010". The table is created by migration **0007**
(`migrations/versions/0007_org_credentials.py:27`). `docs/audits/epic_user_auth_buildout.md:99`
shows where the stale `0010_org_credentials.py` name came from (untracked WIP that
was later renumbered). Comment-only defect.

---

## 1.1 - BLOCKING FINDING: agent-initiated external actions face no money ceiling

> **Section status: `[OWNER-DECISION-REQUIRED]` - this must be resolved BEFORE any
> spend-capable connector (Stripe above all) is written.**

### 1.1.1 The question asked

Does a Stripe refund issued by an agent hit the Decision Engine budget-ceiling check
**synchronously, before** the Stripe API call fires, or only as **post-call audit**?

### 1.1.2 The answer

`[CODE-VERIFIED]` **Neither.** On the code as it stands there is no monetary ceiling
check on a tool-initiated external action at any point - not before, not after.
Three paths, all verified:

**Path A - the tool path (`ToolProxy`), which is where a `stripe.refund` tool would live.**
`ToolProxy.invoke` runs the ordered token pipeline (`src/skylize/tools/proxy.py:114-122`)
but passes `requested_token_cost=0` and `tokens_used_so_far=0`, with the comment
"tool calls don't debit the LLM token budget" (`:119-120`). The BUDGET stage evaluates
`tokens_used_so_far + requested_token_cost > token.max_token_budget`
(`src/skylize/contracts/token.py:413`), i.e. `0 + 0 > budget` - **structurally
unreachable for any non-negative budget.** The budget stage is a no-op for every
tool call. Separately, `ToolProxy` holds no `DecisionEvaluator` and no
`CapitalRepository`: `git grep "DecisionEvaluator|evaluator" src/skylize/tools/`
returns nothing.

**Path B - the synchronous `/agents/execute` gate.** It does run before the mint and
before any LLM spend (`src/skylize/app/agents/execution.py:276-294`), but the
proposal it evaluates is built by `_build_execution_proposal`
(`:968-993`), whose own docstring states "The proposal carries no spend" (`:975`) and
which sets no `spend_minor_units`. `involves_spend` is `spend_minor_units is not None`
(`src/skylize/app/decision_engine/events.py:136-138`), so it is always `False` here.
Worse, an `agent.execute` proposal returns **terminally** from
`_decide_agent_execution` (`src/skylize/app/decision_engine/evaluator.py:139-140`),
which is placed at stage 2.5 - **ahead of stage 4, `capital_check` (`:151-155`)**.
The capital stage is therefore never reached on the request path at all.

**Path C - `capital_check` itself.** The only money-aware stage
(`src/skylize/app/decision_engine/evaluator.py:373-398`) is correct in isolation:
over-ceiling defers to a human, and a missing ceiling fails closed and also defers
(`:378-385`). But it only ever sees proposals built by `DecisionProposal.from_event`
from three async business events - `creative.review`, `sales.campaign`,
`sales.budget_reallocation` (`src/skylize/app/decision_engine/events.py:44-51,141+`).
A tool call is none of them.

### 1.1.3 What the tool path DOES enforce (stated for fairness)

`[CODE-VERIFIED]` The gap is monetary, not total. Before dispatch, `ToolProxy`
enforces: registry resolution fail-closed (`proxy.py:104-111`); the full ordered
token pipeline - signature, expiry, revocation, principal-authority freshness,
scope, delegation (`contracts/token.py:352-428`); `max_calls_per_run`
(`proxy.py:136-149`); and the convergence breaker, recorded **before** dispatch so
no side effect runs on a runaway loop (`proxy.py:151-175`). Every outcome is audited
(`proxy.py:205-228`).

### 1.1.4 Owner decisions required

- **Q1.1a `[OWNER-DECISION-REQUIRED]`** Must a spend-capable tool call carry a
  declared monetary amount into a synchronous `capital_check` before egress? The
  `[RESEARCH-SUGGESTED]` answer is yes: no connector that moves money ships until
  the tool path can produce a spend-bearing proposal and block on it.
- **Q1.1b `[OWNER-DECISION-REQUIRED]`** Where does that check live - inside
  `ToolProxy` (a new stage after scope, before dispatch), or as a required
  pre-flight the connector itself performs? `[RESEARCH-SUGGESTED]` `ToolProxy`, so
  it cannot be forgotten by the next connector author.
- **Q1.1c `[OWNER-DECISION-REQUIRED]`** Does a refund count against the same
  ceiling as a spend, as a negative spend, or is it a separate class? Note ADR-0006:
  `budget_ledger` is currency MINOR units and must not be conflated with the token
  or AI-cost ledgers.

---

## 2.0 - Provider classification (org-level vs platform-level)

> **Section status: `[OWNER-DECISION-REQUIRED]`**

`[RESEARCH-SUGGESTED]` Two distinct credential classes, which must never share a
`provider` slot in `org_credentials`:

- **org-level** - the customer's own account, connected by the customer, stored
  per-`org_id`, reached through the OAuth broker. This is what `org_credentials`
  is for (`migrations/versions/0007_org_credentials.py:7-10`).
- **platform-level** - a Skylize service account used on Skylize's behalf. No
  broker, no per-tenant row; belongs in the secrets manager, not the tenant vault.

| Provider | `[RESEARCH-SUGGESTED]` class | Note |
|---|---|---|
| Stripe (customer's account: refunds, invoices) | org-level | highest blast radius of the five |
| Stripe (Skylize's own billing of its customers) | platform-level | **must not** live in `org_credentials` |
| AWS | **UNRESOLVED - see 2.2** | cannot be classified without Q2.2a |
| GCP | **UNRESOLVED - see 2.2** | cannot be classified without Q2.2a |
| Slack | depends on Q2.3a | customer workspace = org-level; Skylize notifications = platform-level |
| GitHub | org-level | GitHub App installed into the customer's org |

**Q2.0a `[OWNER-DECISION-REQUIRED]`** Confirm the split above, and confirm that
platform-level credentials are barred from `org_credentials` by convention or by a
check.

---

## 2.1 - Stripe

> **Section status: `[OWNER-DECISION-REQUIRED]` - account model DECIDED (Q2.1e, Q2.1f,
> 2026-08-28); remainder still open and BLOCKED additionally on 1.1.**
> Full design: `docs/06_integrations/stripe_connector_design.md`.

`[CODE-VERIFIED]` Existing doc position: Stripe is the "payment & subscription
system of record", integrated by **reference IDs only, never card data**, with PCI
burden staying with Stripe (`docs/06_integrations/stripe.md:11-20`); inbound is
signature-verified at the edge, outbound only through the adapter (`:21-22`);
`chief_security_officer` review is already required (`:52`). No code implements any
of it.

- **Q2.1a `[OWNER-DECISION-REQUIRED]` Test-mode vs live-mode keys.** Does an org
  connect one credential or two? `[RESEARCH-SUGGESTED]`: store mode in the existing
  `label` column (`0007:41`, `''` = default) so `(org_id, 'stripe', 'live')` and
  `(org_id, 'stripe', 'test')` coexist under the existing unique index (`0007:54-57`)
  with no migration. Requires a rule for which mode a given agent run resolves, and
  a fail-closed default.
- **Q2.1b `[OWNER-DECISION-REQUIRED]` Webhook secret handling.** Is the signing
  secret a per-org `org_credentials` row or a platform secret? Related:
  `docs/06_integrations/stripe.md:31` specifies invalid signature -> 401, but no
  endpoint exists.
- **Q2.1c `[OWNER-DECISION-REQUIRED]` Refund authorization ceiling.** A concrete
  number, or the explicit statement that refunds always defer to a human.
  `policy_inputs.md:110` already carries a `[RESEARCH-SUGGESTED]` row -
  "Refund (small, under threshold, no fraud flag) | Medium | L2" - but the threshold
  itself is unset, and `policy_inputs.md:141` defines T4 as auto-reject before
  execution. Until 1.1 is resolved neither is enforceable on the tool path.
- **Q2.1d `[OWNER-DECISION-REQUIRED]` Idempotency key strategy.** Non-optional here.
  The HubSpot precedent retries on 429/5xx with `reraise=True`
  (`src/skylize/tools/builtin/hubspot_tools.py:95-100,111-116`). That shape applied
  to a refund POST **duplicates a refund** on a timeout-then-success. `[RESEARCH-SUGGESTED]`:
  derive the Stripe `Idempotency-Key` deterministically from the run's
  `correlation_id` plus the tool input, so a retry inside a run and a replay of the
  run both collapse to one refund. Owner must confirm the derivation and its scope.
  **Note (2026-08-28):** Stripe prunes idempotency keys after **24 hours**
  (https://docs.stripe.com/api/idempotent_requests), so this derivation protects
  in-run retries but NOT a later replay. A durable local dedupe record is what makes
  replay safe. Separately, the derivation is not implementable through
  `ToolProxy.invoke` as it stands - `src/skylize/tools/proxy.py:322` hardcodes
  `f"tool:{tool.tool_id}:{uuid4()}"` and `:314-321` states "Idempotent replay needs a
  caller-supplied key, which this signature does not accept." Still open.
- **Q2.1e `[DECIDED 2026-08-28]` Connect account model.** **Standard connected accounts
  via the Connect OAuth flow, on the Accounts v1 API.** Deciding constraint: Q2.1f
  requires linking a customer's pre-existing Stripe account; only OAuth does that, and
  OAuth requires Accounts v1 - "You must use Accounts v1 in the following cases: Using
  OAuth to authenticate connected accounts" (https://docs.stripe.com/connect/accounts-v2).
  An Accounts v2 design was drafted and **withdrawn** on this basis; v2 could replicate
  Standard's liability profile but not its reachability. Accepted cost: OAuth is
  documented as "not recommended for new Connect platforms." See design doc 1.0.
- **Q2.1f `[RESOLVED 2026-08-28 - moot]` Pre-existing vs newly provisioned account.**
  Skylize must connect the customer's existing Stripe account. Under the Q2.1e decision
  this is intrinsic to the flow rather than an open choice: Stripe's OAuth authorize step
  lets a logged-in user "choose an account to connect to your platform directly"
  (https://docs.stripe.com/connect/oauth-standard-accounts). No further decision needed.

**Resolution status of the earlier questions under the Q2.1e decision:**

- **Q2.1a** - answered in design doc 4.0.2, but **not** as originally proposed. Mode
  becomes a first-class `livemode` boolean on a new `org_stripe_accounts` table, not a
  value smuggled into the `label` column. Owner confirmation still required.
- **Q2.1b** - **answered with evidence**: the webhook signing secret is **platform-level**.
  A Connect endpoint is one endpoint with one signing secret covering all connected
  accounts (`connect: true`), with the connected account named by a top-level `account`
  field on the event (https://docs.stripe.com/connect/webhooks). The per-org
  `org_credentials` option this question offered does not exist in Stripe's model.
- **Q2.1c**, **Q2.1d** - unchanged and still open. Both are independent of Q2.1e.
- **New questions raised by the decision** (design doc): **Q2.1h** platform-controlled
  customer accounts cannot connect via OAuth `read_write` since June 2021 - product answer
  required; **Q2.1i** confirm discarding the deprecated `access_token` / `refresh_token`
  at the callback; **Q3.0b** the RLS circularity in `acct_ -> org_id` webhook resolution.

---

## 2.2 - AWS / GCP

> **Section status: `[OWNER-DECISION-REQUIRED]` - HARD BLOCK. Not resolvable from
> existing docs. Returned to the owner as an open question.**

`[CODE-VERIFIED]` Every AWS/GCP reference in the repository is about **Skylize's own
hosting**: ECS/RDS/ElastiCache/ALB/Secrets Manager under `infra/terraform/staging/`
(`docs/MVP_GAP_ANALYSIS.md:155`, `:202-204`), S3-compatible object storage
(`docs/02_architecture/tech_stack.md:49`). There is **no** reference anywhere to a
customer cloud account, a cross-account IAM role, workload identity federation, or
an agent managing cloud resources. The account target is genuinely undetermined -
it is not recorded anywhere and must not be assumed.

- **Q2.2a `[OWNER-DECISION-REQUIRED]` Target account.** Two options, both real:

  | | **A. Customer cross-account IAM role / GCP workload identity** | **B. Skylize-owned sandbox account** |
  |---|---|---|
  | Blast radius | customer production infrastructure | contained to Skylize's own sandbox |
  | Credential shape | role ARN + external ID (no long-lived key); STS AssumeRole per call | Skylize-held, platform-level |
  | `org_credentials` fit | poor - the row holds a role reference, not a secret; needs a session-credential path | not stored per-tenant at all |
  | Customer onboarding | customer must run a CloudFormation/Terraform stack to create the role | none |
  | Governance burden | a kill switch must be able to stop an in-flight action against **someone else's** production | ordinary |
  | Sellable as | real infrastructure automation | demo / evaluation only |

  `[RESEARCH-SUGGESTED]` B until the kill-switch and money gates in 1.1 are closed;
  A is not safe to build against customer production while a spend-capable tool call
  has no synchronous ceiling.

- **Q2.2b `[OWNER-DECISION-REQUIRED]` Exact resource types the agent may act on.**
  ECS tasks only? EC2 instances? Autoscaling groups? Anything broader? An allowlist
  of resource types **and** verbs is required; "cloud access" is not a specification.
  Note that Skylize's own gateway runs on ECS (`docs/MVP_GAP_ANALYSIS.md:155`), so
  under option B an over-broad ECS grant reaches Skylize's own control plane -
  the sandbox must be a separate account, not a separate cluster.
- **Q2.2c `[OWNER-DECISION-REQUIRED]` Kill-switch trigger source.** Requirement, not
  a question: the trigger must be the Decision Engine's T4-class hard-deny path
  (`policy_inputs.md:141`) reaching the existing kill-switch surface
  (`src/skylize/edge/routes/kill_switch.py:26-45`,
  `src/skylize/app/governance/authority.py`), **not** a second independent path
  owned by the connector. Owner to confirm that a connector may never define its own
  stop mechanism.

---

## 2.3 - Slack

> **Section status: `[APPROVED]` - 2026-08-28 (owner). Post-only HITL notifier per
> Q2.3a/b/c below; button-interaction handling explicitly out of scope for this
> approval and remains `[RESEARCH-SUGGESTED, UNVERIFIED]`.**

`[CODE-VERIFIED]` Slack exists in the tree only as an example in a migration comment
(`migrations/versions/0007_org_credentials.py:8`) and as the example scope string
`slack.post` in a unit test (`tests/unit/test_principal_authority.py:44`). No Slack
code.

- **Q2.3a `[OWNER-DECISION-REQUIRED]` Workspace scope - ANSWERED.** Owner decision:
  Skylize's own workspace (platform-level), not the customer's. Consequences of this
  answer, all following directly from it:
  - **No OAuth broker for Slack.** The broker (Faz A-D, still unbuilt per 1.0.2)
    exists to let a *customer* grant Skylize access to *their* workspace. A
    platform-level credential is Skylize authenticating to its own workspace, which
    is a one-time app install by Skylize, not a per-customer authorization flow.
  - **No `org_id`, no RLS, no `org_credentials` row.** `org_credentials`
    (`migrations/versions/0007_org_credentials.py:37-46`) is keyed on `(org_id,
    provider, label)` and RLS-scoped to `current_setting('skylize.org_id')`
    (`:59-68`) - built for a credential that varies per tenant. A platform-level
    Slack token is the same value for every tenant, so it does not belong in that
    table at all (consistent with the `org-level` vs `platform-level` split already
    drawn in 2.0, and matches the Stripe platform-billing row in the 2.0 table,
    `:195`).
  - **Credential storage: follow the existing platform-secret pattern, not a new
    mechanism.** The repo's only precedent for a platform-wide (non-tenant) secret
    is `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY`: a `SKYLIZE_*` environment variable read
    into `Settings` (`src/skylize/config.py:85`) and resolved fail-closed at boot -
    `resolve_credential_encryption_key` refuses to start on a real backend if the
    variable is unset, precisely to avoid an undecryptable-on-restart failure mode
    (`src/skylize/bootstrap.py:82-129`). Every other platform secret in the repo
    (`n8n_api_key`, `search_api_key`, `knowledge_webhook_secret`, `anthropic_api_key`,
    `openai_api_key`, `mem0_api_key`, `qdrant_api_key` - all `src/skylize/config.py:128-160`)
    follows the identical shape: plain `SKYLIZE_*` env var on `Settings`, no
    dedicated secrets-manager service exists anywhere in the tree. `[RESEARCH-SUGGESTED]`
    a Slack bot token should follow suit - e.g. `SKYLIZE_SLACK_BOT_TOKEN` - read into
    `Settings` and validated at boot the same way, rather than inventing a new
    storage path. Owner to confirm the variable name and whether boot-time
    validation should fail closed (refuse to start) or fail soft (Slack notifications
    silently disabled) if unset; `resolve_credential_encryption_key`'s fail-closed
    precedent (`bootstrap.py:121-129`) is the stricter option and matches this repo's
    general posture, but Slack notifications are plausibly non-critical-path in a way
    the credential-encryption key is not.
  - **Scope derivation.** The only planned action for this integration is posting a
    HITL-approval message to a human-designated channel (this section's Q2.3c,
    unchanged below). `[RESEARCH-SUGGESTED]` **`chat:write`** is the bot-token OAuth
    scope required for Slack's `chat.postMessage` method - this is standard,
    widely-documented Slack Web API behavior, but it is **UNVERIFIED against Slack's
    own current API reference in this pass** (no live doc fetch was performed) and
    must be confirmed against `https://api.slack.com/methods/chat.postMessage` (or
    the current scopes reference) before the App manifest is written. No other scope
    is derivable from the stated action; do not request a broader scope set (e.g.
    `channels:read`, `groups:read`) without a corresponding planned action to justify
    it, per the attenuation-only principle in this file's Global combining principle.
  - **Button-interaction responses, if ever added, are a separate mechanism, not a
    scope.** If a later phase needs the HITL message to carry interactive
    approve/deny buttons, `[RESEARCH-SUGGESTED, UNVERIFIED]` Slack's own
    documentation describes this as handled via an app-level **Interactivity Request
    URL** plus **signing-secret verification** of the inbound payload, not an
    additional OAuth scope. This is asserted with lower confidence than the
    `chat:write` scope claim above and must likewise be checked against Slack's
    current docs before any interactive-button work is scoped or built. Out of
    scope for the current post-only plan; recorded here only so a future session
    does not go looking for a nonexistent "interactivity" OAuth scope.
- **Q2.3b `[OWNER-DECISION-REQUIRED]` Bot token vs user token.** `[RESEARCH-SUGGESTED]`
  bot token only: a user token makes agent actions indistinguishable from a human's
  in Slack's own audit trail, which contradicts the platform's audit posture. The
  platform-level answer to Q2.3a strengthens rather than changes this recommendation:
  a bot token installed once into Skylize's own workspace is the standard shape for
  a service-account Slack integration, with no per-tenant token-selection logic
  needed. Recommend ratifying as-is.
- **Q2.3c `[OWNER-DECISION-REQUIRED]` Per-org channel provisioning.** Does the agent
  create channels, or only post to channels a human pre-designated?
  `[RESEARCH-SUGGESTED]` post-only to a designated channel first; channel creation
  is an external-action class needing its own entry in `policy_inputs.md` 0.3. Note
  "per-org" is now a slight misnomer under the platform-level answer to Q2.3a - there
  is one Skylize workspace, not one per customer org - but the substance is
  unchanged: recommend ratifying post-only to a single human-pre-designated channel,
  channel creation stays out of scope.

---

## 2.4 - GitHub

> **Section status: `[OWNER-DECISION-REQUIRED]` - architecture DECIDED below
> (Q2.4a install scope, Q2.4b permission manifest + verb surface, Q2.4c ruleset
> dependency + onboarding probe, Q2.4d credential shape, Q2.4e webhook ingress);
> remainder is confirmation, not open design. Predecessor:
> `docs/audits/audit_github_readiness.md` (`e4d2173`), empirically verified at
> `99299dd`/`6ca81d4`. Depends additionally on 4.0 (Section 1.1 must be resolved).**

`[CODE-VERIFIED]` No GitHub integration code, no Octokit/PyGithub/githubkit, no
App manifest. The only `.github/` content is Skylize's own CI
(`audit_github_readiness.md` A.3, re-verified 2026-09-05).

**Architecture, in one paragraph.** GitHub is not "enable + gate" like Drive,
Asana and Notion. It is three tiers, and Skylize-side runtime gating is the
narrowest of the three, not the primary mechanism:

- **Tier 1 - permission omission.** The App manifest never requests
  `administration` or `secrets`. This makes repository deletion,
  branch-protection/ruleset editing, collaborator changes, and secret/Actions-
  variable modification **structurally impossible** - no gate exists to bypass
  because the capability was never minted. `[LIVE-VERIFIED]` 2026-09-05,
  `audit_github_readiness.md` C.3, E.2.
- **Tier 2 - GitHub's own ruleset enforcement.** For push and force-push to a
  branch a customer has protected, GitHub itself refuses the installation token
  with `GH013` (git) / `HTTP 422` (REST) when the Skylize App is not a listed
  bypass actor - proven live, not inferred. `[EMPIRICALLY VERIFIED]` 2026-09-05,
  `audit_github_readiness.md` E.3.1: a real App installation token
  (`contents: write`, no `administration`) was refused on normal push,
  force-push, and REST `PATCH .../git/refs/... {"force":true}`, against a
  ruleset with `bypass_actors: []`. A repository admin was refused identically -
  rulesets grant admins no implicit bypass.
- **Tier 3 - the narrow Skylize-side residue.** Only what Tiers 1-2 cannot cover:
  branch deletion on an *unprotected* branch, and PR merge where the customer has
  no "require a PR" rule. Decided per-verb below (Q2.4b), not left open.

- **Q2.4a `[RESEARCH-SUGGESTED, RATIFY]` Install scope.** Org-level GitHub App
  install, repo-selected at install time. Confirmed correct and for a stronger
  reason than short lifetime: the installation-token mint call accepts
  `permissions` and `repository_ids` parameters that narrow a token below the
  installation's full grant, so Skylize's attenuation-only invariant
  (`integration_inputs.md:33-36`) becomes **provider-enforced**, not merely a
  Skylize-side discipline. `[LIVE-VERIFIED]` `audit_github_readiness.md` C.1-C.2.
  Less ambiguous than Drive's org-vs-platform question was: an App installation
  is inherently org/account-scoped with repository selection built into GitHub's
  own install UI (`integration_inputs.md:196`).

- **Q2.4b `[DECIDED]` Manifest permissions and verb surface, replacing the prior
  "hard-deny via scope" framing.** The prior draft proposed branch deletion be
  "hard-denied to agents" as a scope decision. **That is impossible and is
  withdrawn.** `[LIVE-VERIFIED]` `audit_github_readiness.md` A.4 Defect 1:
  `DELETE .../git/refs/{ref}` and `POST/PATCH .../git/refs` are the *same*
  `contents: write` permission as branch creation and push - there is no
  `contents: write-except-delete`. Same defect for PR merge (Defect 2):
  `PUT .../pulls/{n}/merge` is also `contents: write`, not `pull_requests:write`.
  Q2.4b is therefore reframed into three separable questions, each answered:

  1. **Manifest permissions requested:** `contents: write`, `pull_requests: write`,
     `metadata: read`. Nothing else. This is what makes Tier 1 real - `administration`
     and `secrets` are never requested, so their verbs need no runtime gate.
  2. **Verb surface Skylize registers as tools - DECIDED, verb-surface minimalism
     over a runtime gate for branch deletion:** Skylize **never registers a
     delete-branch tool**, on any branch, protected or not. The precedent is
     already in this codebase: `tests/contract/test_stateless_agents_no_oauth_access.py:270-292`
     asserts the GCP verb surface is *exactly* `["integration.gcp_stop_instance"]`
     and warns against "adding an irreversible verb." An agent that has no
     delete-branch tool cannot delete a branch regardless of what the token would
     technically permit - cheaper and more auditable than a gate that "only exists
     inside a function body [and] is one refactor from being skipped"
     (`src/skylize/tools/base.py:159-160`). This closes the one verb Tier 1/2
     leave open (`audit_github_readiness.md` E.3 "The residue," E.4) without any
     new gate code. If a future pass needs branch deletion as a tool, it needs a
     `ToolPermissionProfile`-style gate at that time - not before.
  3. **PR merge - DECIDED, defer to a human by default:** merge is a routine
     action an agent should eventually be trusted with, so verb-surface omission
     is the wrong tool here (customers want agents opening AND landing PRs
     eventually). Skylize registers a merge verb but it is **HITL-gated by
     default**, same posture as the GCP stop verb's `hitl_id` requirement
     (`src/skylize/tools/builtin/gcp_tools.py:126-130`). This is a default, not a
     structural impossibility like Tier 1's verbs - unlike branch deletion, merge
     cannot be made impossible by simply not building a tool, because the whole
     point of a PR-based workflow is that merge is the intended terminal action.
  4. **Release publication:** rides on `contents: write` (`audit_github_readiness.md`
     E.2 table) and is out of scope for this pass - no release-publish tool is
     registered. Revisit if a future pass adds one.

- **Q2.4c `[DECIDED]` Interaction with existing branch protection, plus a new
  onboarding precondition.** Requirement unchanged and reaffirmed: a Skylize
  agent must **never** be granted bypass on the customer's ruleset/branch-protection
  rules; a GitHub refusal stands and surfaces as a clean tool error; the connector
  holds no admin path around it (`administration` is never requested - Tier 1
  makes this structural, not merely a promise).

  **New this pass - the dependency Tier 2 has on the CUSTOMER's own configuration,
  and how Skylize detects its absence.** Tier 2 protection exists only if the
  customer has actually put a ruleset (or classic protection) on their protected
  branches and left the Skylize App off the bypass list. A customer with no
  ruleset on `main` gets **zero** Tier 2 protection, and `contents: write` alone
  then permits a direct push to `main` - not a defect in this design, but a
  precondition that must be probed and surfaced, never silently assumed
  (`audit_github_readiness.md` Q-NEW-2 follow-on). Modelled on the
  `gcp_wif_connections` health-probe pattern (migration 0024, `app/gcp/probe.py`),
  simplified because GitHub's check is one layer, not two:

  | `connection_state` | Meaning | Remedy |
  |---|---|---|
  | `unverified` | Installed, never successfully probed | finish onboarding |
  | `protected` | Probe confirmed >=1 ruleset/protection rule covers a branch the org will govern, and the Skylize App is not a listed bypass actor | none - Tier 2 is live |
  | `unprotected` | Installation is healthy but the target branch carries no ruleset/protection at all | customer must add branch protection; Skylize's guarantee reduces to Tier 1 only until they do |
  | `bypass_granted` | A ruleset exists but lists the Skylize App (or a team/role the App inherits) as a bypass actor | customer must remove the Skylize App from bypass actors |
  | `revoked` | Installation uninstalled or suspended (see Q2.4e) | customer reinstalls |

  A transient probe failure (network fault, GitHub 5xx) records the attempt but
  must **never** overwrite an existing terminal state, per the discipline
  `app/credentials/oauth.py`'s `_classify_failure` and `probe.py`'s "a transient
  failure never overwrites a good state" already establish for OAuth and WIF.
  `unprotected` and `bypass_granted` are read states, not errors - the connector
  functions in either, just without the Tier 2 guarantee, and the org's dashboard
  must say so plainly rather than implying full protection exists.

- **Q2.4d `[DECIDED]` Credential shape - a third pattern, not `oauth_credentials`,
  not a copy of `gcp_wif_connections`.** `[CODE-VERIFIED against 0021 and 0024]`
  `audit_github_readiness.md` D.2-D.5. GitHub's flow rhymes with WIF (platform
  key signs a short-lived assertion, no token persisted, no refresh token) but
  differs in the one place that matters for schema shape: the App private key is
  **App-level, shared across every tenant's installation** - not a per-tenant
  secret the way a WIF connection's trust relationship is per-tenant. Concretely:

  - The App private key is a **platform-level secret**, resolved once at
    composition time in the shape of `resolve_credential_encryption_key`
    (`bootstrap.py:95`) / `resolve_slack_notifier_config` (`bootstrap.py:160`) -
    **not** a new per-tenant table column, and **not** `org_credentials`.
  - The per-tenant row needed is small: `org_id`, `installation_id`,
    `account_login`, selected-repository list (or "all"), plus the
    `connection_state`/probe columns from Q2.4c above. **No encrypted column and
    no `key_id`** - `installation_id` is not a secret (same argument
    `0024:48-53` makes for `issuer_slug`: "possession of the URL grants nothing -
    only the signing key mints tokens"; here, possession of the installation id
    grants nothing without the platform's App private key).
  - This is a **new table**, structurally modelled on `gcp_wif_connections`'
    decisions (`(org_id, label)` identity cardinality, `tenant_isolation` RLS
    ENABLE + FORCE, `skylize_app` grant, exclusion from migration 0002's
    cross-tenant carve-out) but smaller - GitHub has no customer-typed IAM
    config to store, since the trust is established by GitHub's own install UI,
    not assembled from customer input (`audit_github_readiness.md` D.4.1).
  - Reusing `oauth_credentials` is **ruled out**, not merely disfavoured: an
    installation token has no refresh token (permanently NULL
    `encrypted_refresh_token`) and is never persisted (no honest value for
    `encrypted_access_token`) - the identical defect `0024:14-32` already
    documented for WIF, now confirmed to apply to GitHub with the same force.
  - Migration for this table is **not part of this pass** - `integration_inputs.md`
    §4.0's precondition order still applies (this section reads `[APPROVED]`
    before any migration is written and reviewed).

- **Q2.4e `[OWNER-DECISION-REQUIRED]` Webhook ingress for uninstall detection -
  net-new infrastructure, genuinely open.** A customer revokes access by
  uninstalling the App; GitHub emits an `installation` webhook (`action: "deleted"`
  or `"suspend"`). **No webhook ingress of any kind exists anywhere in this repo
  today** - this is the single largest net-new piece of infrastructure a GitHub
  connector requires, larger than the connector logic itself. Owner must decide
  between:
  1. **Webhook ingress** (recommended default) - new HTTP endpoint, signature
     verification (`X-Hub-Signature-256`, HMAC over the platform's webhook
     secret), and a tenant-resolution path from `installation.id` to `org_id`.
     Fastest detection; the only option that catches an uninstall the moment it
     happens rather than at the next call or next scheduled probe.
  2. **Live-call-failure detection only** (the Notion pattern,
     `src/skylize/tools/builtin/notion_tools.py:30-46`) - that file's own
     description of this as "silent degradation" applies here with the same
     force; not recommended as the sole mechanism.
  3. **Periodic probe** (the WIF pattern, `app/gcp/probe.py`) - catches it
     eventually, adds polling load, still not instant.
  These are not mutually exclusive - a probe (Q2.4c) as the fallback and a
  webhook as the fast path is a defensible combination, but the owner must pick
  the floor, not have Skylize infer it.

---

## 2.5 - Google Drive

> **Section status: `[APPROVED]` - 2026-08-31 (owner). Scope (Q2.5a) verified against
> live Google docs; write actions (Q2.5b), governance narrative (Q2.5c), and the
> Decision Engine hook (Q2.5d) decided below. Q2.5e's out-of-scope list stands as
> recorded. Depends additionally on 4.0 (Section 1.1 must be resolved and Q3.0a's
> schema question is now answered by this section's own infrastructure, below).**

`[CODE-VERIFIED]` Drive is `[OWNER-DECISION-REQUIRED -> ANSWERED]` as **org-level**:
each customer connects their own Google Drive, distinct from Slack's platform-level
answer (2.3, Q2.3a). This follows the classification table in 2.0 and drives every
answer below, exactly as the platform-level answer drove every one of Slack's.

Unlike Slack and GitHub, the OAuth broker infrastructure this integration needs is no
longer purely aspirational: `oauth_credentials` (migration
`migrations/versions/0021_oauth_credentials.py`) and the on-demand refresh primitive
(`src/skylize/app/credentials/oauth.py`) shipped provider-agnostically ahead of any
connector, per `docs/06_integrations/oauth_provider_infrastructure_design.md`. Drive
is the first provider intended to use it; no Drive-specific code exists yet -
`grep -rniE "google|drive|gdrive" src/skylize` (excluding the OAuth infrastructure's
own "not Drive-specific" disclaimers) returns nothing.

- **Q2.5a `[VERIFIED]` OAuth scope - DECIDED.**
  **`drive.file`**, not the full `drive` scope. `drive.file` grants access only to
  files the app creates or that a user explicitly opens with the app - the
  narrowest scope that covers "Skylize creates and manages the deliverables it
  produces for a client," which is this integration's entire stated purpose (see
  Q2.5c). **Verified against Google's live API-specific-authorization guide**
  (`https://developers.google.com/workspace/drive/api/guides/api-specific-auth`,
  checked 2026-08-29): `drive.file` is listed **Non-sensitive / Recommended** and
  requires only basic app verification, not a CASA assessment. Full `drive` and
  `drive.readonly` are listed **Restricted** and require CASA (Cloud Application
  Security Assessment) plus an annual re-assessment - a recurring cost this
  narrower scope avoids entirely. This confirms the cost-avoidance lever raised in
  prior research (the `compass_artifact` research doc) and resolves the confidence
  caveat carried since the previous pass, which had flagged this UNVERIFIED
  pending exactly this check. Do not request the full `drive` scope (or
  `drive.readonly` beyond what `drive.file` already covers) without a planned
  action `drive.file` cannot satisfy, per the attenuation-only principle in this
  file's Global combining principle.
- **Q2.5b `[OWNER-DECISION-REQUIRED]` Which write actions are gated.** Mirroring
  2.4's per-verb treatment for GitHub. Two verbs are in scope for this pass:
  - **File creation / upload.** `[RESEARCH-SUGGESTED]` the routine case: an agent
    producing a client deliverable (see Q2.5c) writes it into the client's Drive.
    Lower blast radius than sharing below - the file stays inside the customer's
    own Drive, under their own retention and access controls.
  - **`permissions.create` (sharing).** `[RESEARCH-SUGGESTED]` the higher-risk
    verb, and this section's `chief_security_officer`-relevant flag: sharing is the
    action where data leaves Skylize's custody to an **arbitrary external party**
    the agent chooses at run time - a link grant, or an add-a-collaborator call,
    can hand a document to anyone with an email address or "anyone with the link."
    This is a materially different risk shape from file creation, which stays
    inside a boundary the customer already controls. `[RESEARCH-SUGGESTED]`
    `permissions.create` should defer to a human by default, at least until a
    tighter rule (e.g. an owner-approved allowlist of recipient domains) is
    defined; a hard default-deny is the fallback position if no such rule is
    approved this pass.
  - File deletion, permission *revocation*, and Shared Drive (Team Drive)
    membership changes are explicitly **not addressed** by this section - see
    Q2.5e for the full out-of-scope list.
- **Q2.5c `[OWNER-DECISION-REQUIRED]` Governance narrative - RESEARCH POSITION.**
  `[RESEARCH-SUGGESTED]` Drive's role in the platform is **deliverable teslimi**
  (agency client-operations delivery): an agent produces a work product for a
  client engagement and places it in the client's own Drive, optionally sharing it
  with named stakeholders. This framing is what makes `drive.file` sufficient
  (Q2.5a) and what makes file-creation the routine, low-risk verb and sharing the
  exceptional, high-risk one (Q2.5b) - the narrative and the two technical answers
  are load-bearing on each other, and changing one without revisiting the others
  is not safe.
- **Q2.5d `[DECIDED]` Decision Engine hook for `permissions.create`.**
  `docs/audits/audit_gdrive_readiness.md` section C.6 originally flagged this gap:
  `[CODE-VERIFIED]` the synchronous Decision Engine gate
  (`src/skylize/app/agents/execution.py:283-294`, docstring `:283-293`) runs **once
  per `/agents/execute` request**, before the token mint, evaluating one
  `DecisionProposal` for the whole request - not once per tool call an agent makes
  during execution. `ToolProxy` still holds no `DecisionEvaluator`
  (`grep -rn "DecisionEvaluator|evaluator" src/skylize/tools/` returns nothing,
  re-confirmed at this commit). Without a per-action gate, an agent approved once
  at request entry could call `drive.permissions_create` an arbitrary number of
  times within that one execution with no additional verdict - contract
  `allowed_tools`, token scope, `max_calls_per_run` (`tools/proxy.py:152-166`), and
  the convergence breaker still apply, but none of those is a *decision*, only a
  *ceiling*.

  **Owner decision (2026-08-31): a third opt-in `ToolProxy` stage, not the
  per-request gate.** `permissions.create` (sharing) gets its own pre-dispatch
  check; routine file creation does not - matching Q2.5b's finding that sharing is
  the verb whose blast radius differs in kind, not degree. The new stage
  structurally mirrors the two opt-in stages already proven in `tools/proxy.py`:
  `ToolSpendProfile` (`tools/base.py:38-59`, reservation at `proxy.py:237-248`)
  and `ToolOAuthProfile` (`src/skylize/app/credentials/oauth.py`, gate at
  `proxy.py:203-220`) - each adds one opt-in check to `ToolProxy.invoke` that fires
  only for a tool declaring the matching profile, leaving every other tool's
  behavior untouched. A `permissions.create`-scoped profile (provisional name:
  `ToolPermissionProfile` or similar) follows the same shape.

  **This is a DECISION, not an implementation.** No such profile is designed in
  structural detail or built this pass - the Drive `permissions.create` tool that
  would declare it does not exist yet either. The concrete profile shape, its
  denial-type hierarchy (mirroring `ToolCredentialDenied` /
  `ToolSpendDenied`), and its exact insertion point relative to the OAuth and
  spend stages are a follow-up implementation pass, gated on this decision but not
  completed by it.
- **Q2.5e `[OWNER-DECISION-REQUIRED]` Explicitly out of scope for this section.**
  Recorded so a future session does not assume these are covered by omission:
  - **Shared Drives (Team Drives).** Different permission model, different API
    surface (`drive.teamdrives.*`), not addressed here.
  - **Real-time change notifications (push webhooks).** `drive.changes.watch` /
    `drive.files.watch` introduce an INBOUND surface (Google calling Skylize) this
    section does not analyze; Stripe's inbound-webhook signature-verification
    precedent (`stripe.md:21-22`, cited in 2.1) would be the nearest pattern if
    this is taken up later.
  - **Full-text search over a customer's Drive contents.** A materially broader
    read surface than `drive.file` grants and than the deliverable-teslimi
    narrative (Q2.5c) requires; would need its own scope and its own owner
    decision if ever proposed.
  - **File deletion and permission revocation.** Named in Q2.5b as un-addressed;
    repeated here for visibility since both are destructive verbs a future
    connector author might otherwise assume are "the opposite of creation/sharing"
    and therefore lower-risk, which does not follow.

---

## 2.6 - Asana

> **Section status: `[APPROVED]` - 2026-09-03 (owner). Scope Q2.6a (granular,
> `addUser` excluded), write actions Q2.6b (task/project create, `addMembers`),
> governance narrative Q2.6c, Decision Engine hook Q2.6d, fixed-`writer` role
> mapping Q2.6e, and revocation override Q2.6f (kept as defence-in-depth) all
> decided below. Q2.6g's out-of-scope list, `addUser` included, stands as
> recorded.**
> Drafted 2026-09-02 against commit `a8e7328`; `addUser` removed at `273ff61`.
> Predecessor: `docs/audits/audit_notion_asana_readiness.md`. Depends
> additionally on 4.0 (Section 1.1 must be resolved).
>
> **Process note, stated plainly:** this file's own banner says "Connector
> implementation is BLOCKED until the relevant section reads `[APPROVED]`", and
> 2.5 followed draft -> approve -> implement across three commits (`ac679be`,
> `a9e6547`, `2448819`). The Asana connector was implemented in the SAME commit
> as its draft (`a8e7328`), on explicit owner instruction, before this section
> reached `[APPROVED]`. The deviation is recorded here rather than left for a
> future session to infer.

`[CODE-VERIFIED]` Asana is **org-level**: the action happens in the customer's own
Asana workspace, against their own projects and their own people. Same test that
made Drive org-level (2.5) and Slack platform-level (2.3, Q2.3a). A task created in
a Skylize-owned Asana workspace is invisible to the customer, so platform-level is
not merely wrong here - it is unimplementable.

Asana required **zero changes to the OAuth primitive**. `[LIVE-VERIFIED]` 2026-09-02
against `https://developers.asana.com/docs/oauth`: authorization-code flow, token
endpoint `POST https://app.asana.com/-/oauth_token`, client credentials as
form-encoded **body** parameters, and a response carrying `"expires_in": 3600`,
`token_type: "bearer"`, and `refresh_token`. That is exactly the RFC 6749 §5.1 shape
`_default_parse_token_response` implements (`app/credentials/oauth.py:104-127`), and
exactly the request shape `_post_refresh` builds (`:423-429`). The provider config is
therefore an endpoint, credentials, and scopes - nothing more, mirroring
`google_provider.py`.

- **Q2.6a `[DECIDED - owner, 2026-09-02]` OAuth scope: granular, `addUser` excluded.**
  Asana publishes **granular scopes** in `<resource>:<action>` form, space-delimited
  (`[LIVE-VERIFIED]` 2026-09-02,
  `https://developers.asana.com/docs/oauth-scopes`): `tasks:read`, `tasks:write`,
  `tasks:delete`, `projects:read`, `projects:write`, `projects:delete`,
  `stories:read`, `stories:write`, `users:read`, `workspaces:read`,
  `attachments:read/write/delete`, `team_memberships:read`, `teams:read`, and others.
  A **"Full permissions"** alternative exists: "If no scopes are specified, the
  `default` OAuth scope will be used - provided the app was originally registered
  with Full permissions", and an app registered that way **cannot** request specific
  scopes. Full permissions grants access to all endpoints.

  **Decided: the granular set, not `default`.** `tasks:write projects:write` -
  granular scopes are what make this file's attenuation-only principle enforceable
  and what keep `oauth_credentials.scopes` (`0021:96`) meaningful rather than
  decorative.

  **The consequence this decision resolves.** `[LIVE-VERIFIED]` Asana's published
  scope list contains **no `workspaces:write`** - only `workspaces:read` - and maps
  **neither** membership endpoint (`addMembers`, `addUser`) to any granular scope.
  1. `projects:write` is the *plausible* scope for `POST /projects/{gid}/addMembers`
     (it mutates a project and returns a `ProjectResponse`), but this is
     **`[UNVERIFIED]`** - Asana does not document the mapping. `addMembers` ships
     under this assumption.
  2. `POST /workspaces/{gid}/addUser` has **no plausible granular scope at all**.
     Enabling it would require registering the Skylize Asana app with **Full
     permissions**, which grants every endpoint for every customer who connects -
     a grant incomparably wider than Drive's `drive.file` and a direct conflict with
     the Global combining principle's minimal-scope philosophy.

  **Owner decision: `addUser` is OUT OF SCOPE.** Requesting Full permissions to
  enable one workspace-level verb is disproportionate to what this platform
  otherwise requests (`drive.file`, `tasks:write projects:write`). An earlier pass
  built and gated `integration.asana_add_workspace_user` anyway, to make the blocked
  state visible; it has since been **removed** rather than shipped half-usable. Only
  `integration.asana_add_project_member` (`addMembers`, `projects:write`) ships. See
  Q2.6g, where `addUser` is now recorded alongside the other explicit exclusions.

- **Q2.6b `[DECIDED - owner, 2026-09-02]` Which write actions are gated.** Mirroring
  2.5's Q2.5b severity split. Three verbs ship this pass; `addUser` was decided out
  of scope by Q2.6a and moved to Q2.6g:
  - **Task creation** (`POST /tasks`). `[RESEARCH-SUGGESTED]` **routine.** Creates
    work inside a workspace the customer already controls, under their own retention
    and access rules. Nothing leaves their custody. Not permission-gated - the same
    reasoning that leaves Drive's `files.create` ungated.
  - **Project creation** (`POST /projects`). `[RESEARCH-SUGGESTED]` **routine**, for
    the identical reason. Structure, not access.
  - **`POST /projects/{project_gid}/addMembers`.** `[RESEARCH-SUGGESTED]`
    **HIGH-RISK.** `[LIVE-VERIFIED]` the `members` field is documented as "An array
    of strings identifying users. These can either be the string `me`, **an email**,
    or the gid of a user." An email address chosen by the agent at run time, granting
    a named external party access to a customer's project, is structurally identical
    to Drive's `permissions.create` - the verb 2.5 gave the third gate to. Documented
    side effect worth recording: "a user being added as a member may also be added as
    a *follower*", i.e. the grant is slightly wider than the verb's name suggests.

- **Q2.6c `[DECIDED - owner, 2026-09-03]` Governance narrative.**
  `[RESEARCH-SUGGESTED]` Asana's role is **agency work-intake and delivery
  tracking**: an agent turns an engagement into tracked work in the client's own
  Asana - creating the project and the tasks that constitute a deliverable - and,
  exceptionally, brings a named stakeholder into that project so they can follow it.
  As with 2.5's deliverable-teslimi framing, this narrative and the technical
  answers are load-bearing on each other: it is what makes `tasks:write
  projects:write` sufficient (Q2.6a) and what makes creation the routine verb and
  membership the exceptional one (Q2.6b). Changing one without revisiting the others
  is not safe.

- **Q2.6d `[DECIDED - owner, 2026-09-02]` Decision Engine hook: REUSE the existing
  gate, build nothing new.** The gap 2.5's Q2.5d identified is unchanged at this
  commit: `[CODE-VERIFIED]` the synchronous Decision Engine gate runs once per
  `/agents/execute` request, and `ToolProxy` holds no `DecisionEvaluator`
  (`grep -rn "DecisionEvaluator" src/skylize/tools/` returns nothing, re-confirmed).
  Asana's membership verb therefore needs what Drive's sharing needed.

  **It gets it from the SAME mechanism, not a second one.** `ToolPermissionProfile`
  (`tools/base.py:89-121`), the third opt-in `ToolProxy` stage shipped in `2448819`
  (`proxy.py:266-270`, handler `:400-476`), is provider-agnostic by construction -
  its docstring already states "Nothing here is Drive-specific: the gate knows about
  an `action_class`, a grantee, and a role" (`app/permissions/gate.py:5-6`). Asana
  supplies one new `action_class` value and reuses everything else: the
  `org_permission_grants` allow-list (migration 0022), exact-address-or-bare-domain
  matching, the deny-by-default asymmetry, and the audited denial path.

  **No HITL deferral**, following the Q2.1d precedent 2.5 relied on: a HITL replay
  would re-execute the original action a second time on approval. The allow-list is
  the mechanism; a human pre-authorizes recipients, and the agent may then act only
  within that pre-authorization.

  Action class: **`asana.project.add_members`**. An earlier pass reserved a second,
  distinct class (`asana.workspace.add_user`) for the wider workspace-invitation
  verb specifically so a project-level pre-authorization could never carry over to
  it; that verb is now removed entirely (Q2.6a), so only the one action class ships.
  The distinct-class discipline stands as the pattern for any future Asana verb of
  different blast radius.

- **Q2.6e `[DECIDED - owner, 2026-09-02]` Asana has no role axis: map to a FIXED
  `writer` in application code, never in the schema.** `[CODE-VERIFIED]`
  `org_permission_grants.max_role` is `CHECK (max_role IN ('reader', 'commenter',
  'writer'))` (`0022:85-86`) with `ROLE_RANK = {"reader": 0, "commenter": 1,
  "writer": 2}` (`dal/permission_grants.py:27`), and `ToolPermissionProfile`
  requires a non-empty `role_field` (`tools/base.py:116`). `[LIVE-VERIFIED]` Asana's
  `addMembers` accepts **no role or access-level parameter at all** - a project
  member is a project member.

  **The CHECK constraint is NOT relaxed.** A nullable or free-text role would be a
  role-less escape hatch that any future provider could use to bypass the rank
  comparison entirely, and the constraint is one of the few places the gate's
  ordering is enforced by the database rather than by code. Instead, the Asana
  membership tool carries `role: Literal["writer"] = "writer"` on its input schema.
  The field exists because the gate reads it off the validated input
  (`proxy.py:435`); it is pinned by the type so an agent cannot vary it, and
  `extra="forbid"` prevents smuggling another value. `writer` is the correct rank:
  Asana membership confers full participation, so mapping it to anything lower would
  understate what is being granted and let an org authorize less than it is actually
  giving away.

  Consequence an operator must understand: an `org_permission_grants` row for an
  Asana action class with `max_role` of `reader` or `commenter` authorizes
  **nothing**, because every Asana membership request arrives as `writer` and the
  gate ANDs grantee-match with rank. That is the intended, fail-closed behaviour.

- **Q2.6f `[DECIDED - owner, 2026-09-02, ON A CORRECTED PREMISE]` Revocation
  detection.** The audit (`audit_notion_asana_readiness.md` §B.2.3) flagged that
  Asana's error envelope is `{"errors":[{"message": ...}]}` rather than RFC 6749, and
  that `_default_is_revocation` (`oauth.py:130-138`) would therefore never mark a dead
  Asana grant `revoked` - silent degradation, the customer never prompted to
  reconnect. **Live verification this pass CORRECTS that premise, and the correction
  matters.**

  `[CODE-VERIFIED]` `is_revocation_error` is called from exactly one place -
  `_classify_failure` (`oauth.py:469`), reached only from `_post_refresh`
  (`oauth.py:445`). It therefore only ever sees a response from the **token
  endpoint** (`app.asana.com/-/oauth_token`), never from the REST API
  (`app.asana.com/api/1.0/*`).

  `[LIVE-VERIFIED]` those are two different error formats. The REST API does use
  `{"errors":[{"message": "Not Authorized"}]}`
  (`https://developers.asana.com/docs/errors`, 2026-09-02) - the audit was right
  about that surface. But Asana's **token endpoint** returns RFC 6749:
  `HTTP 400 {"error": "invalid_grant", "error_uri": "...", "error_description":
  "The \`refresh_token\` provided was invalid."}`, evidenced verbatim in two
  independent Asana developer-forum threads (`forum.asana.com/t/...615160`,
  `.../738321`, both read 2026-09-02). **The default predicate would have worked.**

  **An Asana-specific override ships anyway, as defence in depth**, because Asana
  does not *document* its token-endpoint error contract and forum posts are not a
  specification. It is a strict SUPERSET of the default: identical behaviour on the
  RFC 6749 shape, plus it can read the `errors` envelope should the token endpoint
  ever return one. It never widens into ambiguity - a bare "Not Authorized" is NOT
  treated as revocation, because at the token endpoint that is equally consistent
  with a wrong platform client secret, which is a Skylize misconfiguration and must
  never be reported to a customer as their revocation. Being provider-scoped
  (`OAuthProviderConfig.is_revocation_error`), it cannot affect Drive's, Slack's, or
  Stripe's detection.

  **Owner decision, 2026-09-03: the override STAYS**, even though the corrected
  premise means the shared default would also have worked. It is accepted as
  harmless defence-in-depth rather than dropped in favour of the default -
  Asana's token-endpoint error contract is not documented, so the override
  covers a real (if presently unobserved) gap. Not revisited by this decision.

- **Q2.6g `[DECIDED - owner, 2026-09-02]` Explicitly out of scope for this section.**
  Recorded so a future session does not assume these are covered by omission:
  - **Workspace-level user invitation (`POST /workspaces/{gid}/addUser`).** Decided
    OUT OF SCOPE by Q2.6a: no published Asana granular scope covers it, and enabling
    it would require registering the Skylize Asana app with Full permissions -
    every endpoint, for every connected customer. Disproportionate to this
    platform's minimal-scope philosophy (`drive.file`, `tasks:write
    projects:write`). Built and gated in an earlier pass to make the blocked state
    visible, then removed rather than shipped unusable. If this is revisited, it is
    a Full-permissions and blast-radius decision, not a code change.
  - **Webhooks.** `POST /webhooks` is an INBOUND surface (Asana calling Skylize)
    with its own signature-handshake model. Not analyzed here; Stripe's
    inbound-webhook precedent (2.1) is the nearest pattern if taken up later.
  - **Custom fields.** `custom_fields` on task and project payloads are a per-workspace
    schema this connector neither reads nor writes. A customer's custom fields can
    carry sensitive structured data and deserve their own scope decision.
  - **Portfolios, goals, time-tracking, task templates, project templates.** Each has
    its own scope pair in Asana's granular list and none is required by Q2.6c's
    narrative.
  - **Deletion of anything** - `tasks:delete`, `projects:delete`. 2.5 set the
    precedent of OMITTING the destructive verb rather than gating it
    (`drive_tools.py:17-18`); this section follows it. A future author must not
    assume deletion is "the opposite of creation" and therefore equally routine.
  - **Attachments.** `attachments:write` would let an agent push file content into a
    customer's Asana; that is a data-egress question closer to Drive's than to task
    creation's and is not analyzed here.
  - **Removing members** (`removeMembers`, `removeUser`). Revocation is destructive
    and is deliberately un-addressed, exactly as Drive's permission revocation was.
  - **Reading a customer's existing Asana content.** No `*:read` scope is requested
    and no read tool is built. The connector is write-only by construction.

- **Q2.6h `[CODE-VERIFIED]` Rate limits, recorded for the retry design.**
  `[LIVE-VERIFIED]` 2026-09-02, `https://developers.asana.com/docs/rate-limits`:
  **150** requests/minute on free domains, **1,500** on paid; **50** concurrent
  reads and **15** concurrent writes, evaluated independently; a separate **60**
  requests/minute cap on the search API; **5** concurrent duplication/export jobs.
  On exceed: **HTTP 429** with the standard `Retry-After` header, and the docs advise
  using the returned value rather than assuming a fixed wait. Cost-based accounting
  also applies - "the cost of a request is calculated after the response is built and
  is deducted from a per-minute quota" - so heavy requests can throttle at low volume.
  **Limits are allocated per authorization token**, which under this section's
  org-level answer means per customer org: one org's burst cannot throttle another's.
  That is a materially better isolation property than Notion's per-workspace model
  and is worth preserving if a shared client is ever proposed.

---

## 2.7 - Notion

> **Section status: `[APPROVED]` - 2026-09-04 (owner). Capabilities-vs-scopes
> Q2.7a, write actions Q2.7b, governance narrative Q2.7c, version pinning Q2.7d,
> and revocation wiring Q2.7e all decided below. Q2.7f's out-of-scope list stands
> as recorded. Q2.7g (shared-workspace rate-limit pacing) is
> `[DEFERRED - follow-up, non-blocking]`: a real engineering question, tracked
> for a later pass, and explicitly NOT a condition of this approval.**
> Drafted 2026-09-03 against commit `8f147d4`; connector shipped at `92c7706`.
> Predecessor: `docs/audits/audit_notion_asana_readiness.md`. Depends
> additionally on 4.0 (Section 1.1 must be resolved).
>
> **Process note:** as with 2.6, the connector was implemented in the SAME
> commit as its draft (`92c7706`) on explicit owner instruction, ahead of
> `[APPROVED]`. Recorded rather than left for a future session to infer.

`[CODE-VERIFIED]` Notion is **org-level**: a page created for a customer must
exist in **their own** workspace, where their team reads it. Same test that made
Drive (2.5) and Asana (2.6) org-level and Slack platform-level (2.3). Notion's
own model makes platform-level unimplementable rather than merely wrong: a grant
is scoped to one `workspace_id` returned in the token response, and a connection
can only reach pages a human in that workspace has already shared with it.

Notion is the provider that **required** both OAuth-primitive extensions shipped
in `8f147d4`, and it is the only provider that uses all three config hooks. The
audit's two incompatibility findings were re-verified live on 2026-09-03 and both
**still hold**:

- **HTTP Basic client auth.** `[LIVE-VERIFIED]`
  `https://developers.notion.com/reference/create-a-token`: the endpoint declares
  `"security": [{"basicAuth": []}]` with `"basicAuth": {"type": "http", "scheme":
  "basic"}`, and the refresh request body carries only `grant_type` and
  `refresh_token`. Handled by `auth_style="header"`.
- **No `expires_in`.** `[LIVE-VERIFIED]` same source; the full 200 response is
  `access_token`, `token_type`, `refresh_token`, `bot_id`, `workspace_icon`,
  `workspace_name`, `workspace_id`, `owner`, `duplicated_template_id`,
  `request_id`. Handled by a Notion-specific parser returning
  `expires_in_seconds=None`, persisted as `expires_at IS NULL` (migration 0023).

**A THIRD finding this pass, which the audit did not have.** `[LIVE-VERIFIED]`
Notion's token endpoint does **not** use RFC 6749's error shape either. It
returns its own envelope, `{"object": "error", "code": "invalid_grant",
"message": ..., "status": 400}`, so the shared `_default_is_revocation` - which
reads a top-level `"error"` key - would find `None` and **never** detect a dead
grant. Notion therefore needs an `is_revocation_error` override that reads
`code`. **This is materially different from Asana's situation** (2.6 Q2.6f),
where the REST API used a custom envelope but the token endpoint turned out to be
RFC 6749-conformant, making Asana's override defence-in-depth. Notion's override
is load-bearing.

- **Q2.7a `[DECIDED - owner, 2026-09-04]` Least privilege: capabilities, not scopes.**
  `[LIVE-VERIFIED]` `https://developers.notion.com/reference/capabilities`:
  Notion has **no OAuth `scope` parameter at all**. An integration's capabilities
  are fixed when it is REGISTERED in Notion's developer portal: *Read content*,
  *Update content*, *Insert content*, *Read comments*, *Insert comments*, and a
  three-way user-information setting (*No user information* / *without email
  addresses* / *with email addresses*).

  Two consequences, and the first is a genuine governance regression:
  1. **The attenuation invariant is not checkable for Notion.** The token
     response carries no `scope` field, so `oauth_credentials.scopes` (`0021:96`)
     stays empty for every Notion grant. Drive records `drive.file` and Asana
     records `tasks:write projects:write`; Notion records nothing, because there
     is nothing to record. **No code change can recover this** - it is a property
     of Notion's API, and it is recorded here rather than papered over.
  2. The least-privilege decision is therefore *which capabilities to register
     with*, made once in a portal and not per-authorization.

  **Decided: register the Skylize Notion integration with *Insert content* and
  *Update content* only**, plus **user information set to *No user
  information***. *Read content* is not registered: no read tool is built (see
  Q2.7f), and adding a capability with no corresponding action would widen the
  integration's reach beyond what the narrative in Q2.7c actually needs. *With
  email addresses* is declined for the same reason it was flagged: nothing in
  this connector needs a Notion user's identity, and requesting it would pull
  customer PII into a surface with no use for it. This is a one-time portal
  registration choice; it cannot vary per customer.

- **Q2.7b `[DECIDED - owner, 2026-09-03]` Write actions and severity: ALL ROUTINE.**
  Three verbs ship, and **none is permission-gated**:
  - **Page creation** (`POST /v1/pages`). Routine. Lands inside a workspace
    location a human already shared with the integration.
  - **Database creation** (`POST /v1/databases`). Routine. Structure, not access.
  - **Content append** (`PATCH /v1/blocks/{id}/children`). Routine. Changes what
    a page says, never who can read it.

  **THE FINDING: Notion has no elevated action to gate, and this is confirmed
  rather than assumed.** `[LIVE-VERIFIED]` 2026-09-03 against the capabilities
  reference: there is **no sharing, permission-changing, or external-invitation
  capability in Notion's API**. The capability list above is exhaustive. A
  connection's reach is bounded by what a human shared with it in Notion's UI,
  and the API cannot widen that boundary.

  So Notion has **no analogue of Drive's `permissions.create` or Asana's
  `addMembers`** - no action hands data to a party the agent picks at run time.
  **No `ToolPermissionProfile` is declared, and a future author must not add one
  to make Notion look symmetrical with the other two connectors**; there would be
  nothing for it to authorize. A contract test asserts the Notion tool set
  carries zero permission profiles, so the absence is pinned rather than
  incidental.

  The nearest genuine concerns are different in kind and lower in severity, and
  are recorded so they are not mistaken for gaps: *Insert comments* (not
  requested, Q2.7f) could push agent-authored text in front of whoever already
  watches a page - a notification-surface concern, not a data-custody transfer -
  and *user information with email addresses* is a **read**-side privacy choice
  (Q2.7a), not a write action.

- **Q2.7c `[DECIDED - owner, 2026-09-04]` Governance narrative.**
  `[RESEARCH-SUGGESTED]` Notion's role is **deliverable drafting in the client's
  own workspace**: an agent produces a written work product - a brief, a research
  summary, a structured tracker - as a page or database inside the customer's
  Notion, and appends to it as the work develops. This is the same
  deliverable-teslimi framing 2.5 established for Drive, in the medium the
  customer's team actually reads. It is what makes *Insert*/*Update content*
  sufficient (Q2.7a) and what makes every verb routine (Q2.7b): the connector
  writes content into a boundary the customer already controls, and Notion gives
  it no way to widen that boundary.

- **Q2.7d `[DECIDED - owner, 2026-09-03]` `Notion-Version` is pinned, not tracked.**
  Notion requires a `Notion-Version` header on every request and rejects requests
  without one. `[LIVE-VERIFIED]` 2026-09-02 the latest documented version is
  **`2026-03-11`**, and it is pinned as a module constant. Notion's versioning has
  no analogue in Drive or Asana. A version bump can change response shapes, so
  moving the constant is a reviewed change with a re-read of the affected
  endpoints, never a silent bump to "latest". A unit test pins the value so a
  casual edit fails loudly.

- **Q2.7e `[DECIDED - owner, 2026-09-03]` Revocation detection: THE critical
  wiring, and the reason this section matters beyond its three verbs.**

  A Notion grant never expires (see this section's preamble), and that has a
  consequence that is easy to miss and severe if missed. `[CODE-VERIFIED]`
  `evaluate_grant` never returns `NEEDS_REFRESH` for a NULL-expiry grant, so no
  refresh is ever attempted, so the **entire refresh-failure revocation path**
  (`_post_refresh` -> `_classify_failure` -> `is_revocation_error` ->
  `GrantRevoked`) **is unreachable**. Nothing about the passage of time can ever
  mark a Notion grant dead. Left unhandled, a customer who disconnects Skylize in
  Notion would keep a row marked `'valid'` **forever**: every call would 401, the
  ToolProxy credential gate would keep passing the grant as healthy, and nobody
  would ever be told to reconnect.

  `OAuthCredentialService.mark_revoked_by_provider` shipped in `8f147d4` **with no
  production caller**. **This connector is that caller.** Every Notion call funnels
  through one response check, so the wiring is structural: a verb added later
  inherits it rather than having to remember it.

  **Only 401 `unauthorized` counts, and the narrowness is the security decision.**
  `[LIVE-VERIFIED]` `https://developers.notion.com/reference/status-codes`:

  | Response | Notion's documented meaning | Revocation? |
  |---|---|---|
  | 401 `unauthorized` | "The bearer token is not valid." | **YES** - unambiguous |
  | 403 `restricted_resource` | "The token lacks permission, or the request exceeds a workspace block limit." | **NO** - our own capability misconfiguration (Q2.7a) or a customer quota |
  | 404 `object_not_found` | "the resource has not been shared with owner of the bearer token" | **NO** - a sharing gap in Notion's UI |

  **This narrows the owner's brief, which said "401/403".** 403 is documented as a
  capability-or-quota condition, not a token-validity one. Marking a customer
  revoked because *we* registered the integration without *Insert content* would
  demand a reconnect that fixes nothing and would hide the real fault behind a
  per-tenant symptom - exactly the asymmetry `app/credentials/oauth.py` exists to
  enforce. **403 is therefore deliberately NOT treated as revocation**, and a
  parametrized test asserts it leaves `connection_state` untouched.

  The effect is one-directional and eventual, by design: the call that discovers
  the 401 still fails, and it is the **next** gated tool call that reads the
  terminal state through the unchanged ToolProxy gate and denies with
  `ToolCredentialReconnectRequired`. **Nothing in ToolProxy's dispatch flow is
  touched**, so `2448819`'s deny-by-default chokepoint keeps exactly the shape its
  tests pin. Tests assert the full loop (401 -> durable state -> next call
  denied), including through real Postgres, rather than merely that a function was
  called.

- **Q2.7f `[DECIDED - owner, 2026-09-03]` Explicitly out of scope.**
  Recorded so a future session does not assume these are covered by omission:
  - **Comments** (*Insert comments* / *Read comments*). Not requested and no tool
    built. *Insert comments* would let an agent push text into a discussion and
    notify its watchers - a different surface from page content, deserving its own
    decision.
  - **Reading a customer's Notion content.** No read tool is built and *Read
    content* is not required by Q2.7c. The connector is write-only by construction.
  - **Search across a workspace.** A materially broader read surface than the
    narrative needs; would need its own owner decision.
  - **Deletion / archiving** of pages, blocks or databases. 2.5 and 2.6 both set
    the precedent of OMITTING the destructive verb rather than gating it; this
    section follows it.
  - **Rich block types** - headings, lists, code, callouts, nested children,
    file/image blocks. The connector writes **plain paragraphs only**. A
    half-built markdown-to-blocks translator is the kind of thing that silently
    mangles a customer's deliverable, so richer structure is deferred rather than
    approximated.
  - **Database property schemas beyond text.** Database creation makes one title
    column plus optional rich-text columns. Selects, relations, rollups and
    formulas are a per-workspace schema question, not a connector default.
  - **User-identity lookups.** Tied to Q2.7a's *No user information*
    recommendation.
  - **Webhooks / inbound events.** An INBOUND surface (Notion calling Skylize),
    unanalyzed here; Stripe's signature-verification precedent (2.1) is the
    nearest pattern if taken up later.

- **Q2.7g `[DEFERRED - follow-up, non-blocking, owner 2026-09-04]` Rate limits,
  and a real open question about a SHARED budget.** Deferred rather than decided:
  the reactive handling below ships as-is, and the shared-budget pacing question
  is tracked for a later pass rather than resolved or blocked on here.
  `[LIVE-VERIFIED]` 2026-09-03, `https://developers.notion.com/reference/request-limits`:
  - **Per connection:** "an average of three requests per second, with some bursts
    beyond the average allowed."
  - **Per workspace:** a **separate** limit, "shared across all of the workspace's
    connections and scaled to the workspace's plan" - the change that landed
    2026-06-16.
  - **On exceed:** HTTP **429** code `rate_limited`, with
    `additional_data.rate_limit_reason` naming which limit broke, and a
    `Retry-After` header in seconds. HTTP **529** `service_overload` gets the same
    treatment.
  - **Notion's own retry guidance:** retry 429 and 529; retry 500/502/503/504
    **"only for idempotent requests (GET, DELETE)"**; exponential backoff with
    jitter capped at 30 seconds; respect `Retry-After`; cap attempts (6
    recommended).
  - **Size limits:** 1000 block elements and 500KB per request; rich text and URLs
    2000 characters; arrays 100 elements.

  **Two design consequences, both implemented:**
  1. **5xx is NOT retried on these writes**, diverging from Drive and Asana, on
     Notion's own written instruction. Every verb here is a non-idempotent
     POST/PATCH, so a timeout-then-success retry would create a **second page**.
     Drive and Asana do retry 5xx - a real duplication hazard (the unresolved
     idempotency gap 2.5's audit logged as D.5) that this connector declines to
     inherit. Only 429/529 are retried, honouring `Retry-After`, jittered, capped.
  2. **Size limits are enforced client-side** so an oversized append is refused
     with a clear message rather than a 400 from Notion.

  **THE DEFERRED QUESTION.** The per-workspace budget is shared with **every
  other integration the customer runs on that workspace**, not a
  Skylize-dedicated allowance. Two things follow that this pass does **not**
  resolve, and that approval of this section does not resolve either:
  - A Skylize connector can be throttled by a customer's unrelated tools, and
    conversely **Skylize's own burst can throttle the customer's other
    integrations** - a way for this platform to degrade software it does not own.
    That is a product-behaviour question, not only an engineering one.
  - Purely **reactive** backoff (implemented) does not prevent that; only
    **proactive client-side pacing** would. A real limiter would need a shared
    token bucket keyed by `workspace_id` across processes - i.e. Redis and a
    cross-instance coordination design. **Deliberately NOT built here**, because
    inventing a distributed rate limiter inside a connector pass would be scope
    creep with real failure modes of its own. Flagged as the one substantive
    engineering question 2.7 leaves open.

---

## 3.0 - `org_credentials` schema gaps for OAuth (no migration this pass)

> **Section status: `[OWNER-DECISION-REQUIRED]` - recorded, deliberately not implemented.**

`[CODE-VERIFIED]` The table (`migrations/versions/0007_org_credentials.py:37-46`)
stores exactly one opaque encrypted string per `(org_id, provider, label)`. An OAuth
grant is not one opaque string. Absent and needed for a broker:

1. **No `expires_at`** - nothing can know an access token is stale before using it.
2. **No refresh-token slot** - a refresh token would have to be smuggled into
   `metadata_json` (plaintext JSONB, `:43`) or double-encoded into `encrypted_value`.
   **`metadata_json` is not encrypted** and must never hold secret material.
3. **No granted-scope column** - the attenuation invariant cannot be checked against
   what the provider actually granted.
4. **No revocation state** - a customer disconnecting upstream is invisible; only a
   row delete exists (`vault.py:102-141`).
5. **No key reference** - one platform-wide Fernet key (`bootstrap.py:322-323`) with
   no key id column, so envelope encryption or per-tenant keys cannot be introduced
   without a rewrite of every row.
6. **No account-identity column** - nothing records *which* Stripe account or *which*
   Slack workspace a row points at, so two connections of the same provider are
   distinguishable only by a free-text `label`.

**Q3.0a `[OWNER-DECISION-REQUIRED]`** Extend `org_credentials`, or add a separate
`org_oauth_grants` table alongside it? `[RESEARCH-SUGGESTED]` a separate table: API
keys and OAuth grants have different lifecycles, and the existing table's
single-opaque-string contract is relied on by the HubSpot connector and by
`tests/integration/test_jsonb_readback_pg.py:230`.

**Scope narrowed by 2.3.** With Slack now answered as platform-level (2.3, Q2.3a),
a Slack token is a `Settings`-sourced env var, never a per-tenant OAuth grant - it
does not touch `org_credentials` or a hypothetical `org_oauth_grants` table at all.
Q3.0a's answer only needs to cover providers that end up **org-level**: on the 2.0
classification table as it stands (`:192-199`), that is Stripe (customer account),
GitHub, and whichever of AWS/GCP is decided in 2.2 if the answer is customer
cross-account (option A). Any provider that resolves to platform-level by the same
reasoning applied to Slack here is out of scope for this table by construction, not
by exception - re-derive `org_credentials` vs `org_oauth_grants` need per-provider as
each remaining classification lands, rather than assuming all five providers need it.

---

## 4.0 - Preconditions before ANY connector code is written

`[RESEARCH-SUGGESTED]` In order:

1. Section 1.1 resolved and implemented - a spend-capable tool call reaches a
   synchronous ceiling check before egress.
2. Q2.2a answered - AWS/GCP account target fixed in writing.
3. Q3.0a answered - grant storage decided; migration written and reviewed separately.
4. Per-provider section reads `[APPROVED]`.
5. Idempotency strategy (Q2.1d) approved before any non-idempotent verb ships.

---

## Sign-off

Change each `Section status` line to `[APPROVED]` with a date. Connector
implementation for a given provider may begin only when that provider's section
reads `[APPROVED]` **and** the preconditions in 4.0 are met.

- 1.1 Spend ceiling on tool egress: ______________________  (owner, date)
- 2.0 Provider classification: __________________________  (owner, date)
- 2.1 Stripe (account model Q2.1e/Q2.1f decided 2026-08-28; Q2.1c, Q2.1d, Q2.1h,
  Q2.1i still open - see `stripe_connector_design.md`): ___  (owner, date)
- 2.2 AWS / GCP: _______________________________________  (owner, date)
- 2.3 Slack: Approved as post-only HITL notifier (2.3 above)  2026-08-28  (owner)
- 2.4 GitHub (architecture Q2.4a/b/c/d decided - App install scope, permission
  manifest + verb surface [no delete-branch tool; PR merge HITL-gated by
  default], ruleset dependency + onboarding probe, third credential shape;
  Q2.4e webhook ingress still OPEN, owner must pick the detection floor):
  _______________________________________________________  (owner, date)
- 2.5 Google Drive (scope Q2.5a verified, write actions Q2.5b, governance
  narrative Q2.5c, and Decision Engine hook Q2.5d decided; Q2.5e out-of-scope list
  stands as recorded): Approved  2026-08-31  (owner)
- 2.6 Asana (scope Q2.6a decided - granular, `addUser` excluded; write actions
  Q2.6b decided - task/project create, `addMembers`; narrative Q2.6c, gate
  reuse Q2.6d, fixed-`writer` mapping Q2.6e, and revocation override Q2.6f
  (kept) decided; Q2.6g out-of-scope list decided, `addUser` included):
  Approved  2026-09-03  (owner)
- 2.7 Notion (capabilities Q2.7a decided - Insert/Update content, no user
  information; write actions Q2.7b decided - all routine, no permission gate,
  Notion has no sharing API; narrative Q2.7c, version pinning Q2.7d, and
  revocation wiring Q2.7e decided; Q2.7f out-of-scope list stands as recorded;
  Q2.7g shared-workspace rate-limit pacing DEFERRED as a non-blocking
  follow-up): Approved  2026-09-04  (owner)
- 3.0 Credential schema: _______________________________  (owner, date)
