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

> **Section status: `[OWNER-DECISION-REQUIRED]`**

`[CODE-VERIFIED]` No GitHub integration code, no Octokit, no App manifest. The only
`.github/` content is Skylize's own CI.

- **Q2.4a `[OWNER-DECISION-REQUIRED]` Install scope.** Org-level GitHub App install
  or per-repository? `[RESEARCH-SUGGESTED]` App, repo-selected at install time:
  installation tokens are short-lived and per-installation, which suits
  attenuation-only far better than a PAT.
- **Q2.4b `[OWNER-DECISION-REQUIRED]` Which actions are gated.** Explicit per-verb
  decision required for at minimum: push to a protected branch; branch deletion;
  PR merge; release publication; secret or Actions-variable modification.
  `[RESEARCH-SUGGESTED]` merge and protected-branch push defer to a human by
  default; branch deletion and secret modification are hard-denied to agents.
- **Q2.4c `[OWNER-DECISION-REQUIRED]` Interaction with existing branch protection.**
  Requirement: a Skylize agent must **never** be granted bypass on the customer's
  branch-protection rules. If GitHub refuses the action, that refusal stands and is
  surfaced as a clean tool error - the connector must not hold an admin path around
  it. Owner to confirm.

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

> **Section status: `[DRAFT]` - NOT APPROVED. Owner sign-off pending.**
> Drafted 2026-09-02 against commit `a8e7328`. Predecessor:
> `docs/audits/audit_notion_asana_readiness.md`. Depends additionally on 4.0
> (Section 1.1 must be resolved).
>
> **Process note, stated plainly:** this file's own banner says "Connector
> implementation is BLOCKED until the relevant section reads `[APPROVED]`", and
> 2.5 followed draft -> approve -> implement across three commits (`ac679be`,
> `a9e6547`, `2448819`). The Asana connector was implemented in the SAME commit
> as this draft, on explicit owner instruction. The deviation is recorded here
> rather than left for a future session to infer. Nothing below is approved.

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

- **Q2.6a `[OWNER-DECISION-REQUIRED]` OAuth scope - PARTLY BLOCKED, read carefully.**
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

  `[RESEARCH-SUGGESTED]` the granular set, not `default`. Granular scopes are what
  make this file's attenuation-only principle enforceable and what keep
  `oauth_credentials.scopes` (`0021:96`) meaningful rather than decorative. The
  minimum covering the verbs in Q2.6b is **`tasks:write projects:write`**.

  **THE BLOCKER, and it is not cosmetic.** `[LIVE-VERIFIED]` Asana's published scope
  list contains **no `workspaces:write`** - only `workspaces:read` - and maps
  **neither** membership endpoint (`addMembers`, `addUser`) to any granular scope.
  Two consequences the owner must decide on before any scope string is registered:
  1. `projects:write` is the *plausible* scope for `POST /projects/{gid}/addMembers`
     (it mutates a project and returns a `ProjectResponse`), but this is
     **`[UNVERIFIED]`** - Asana does not document the mapping.
  2. `POST /workspaces/{gid}/addUser` has **no plausible granular scope at all**.
     Enabling it appears to require registering the Skylize Asana app with **Full
     permissions**, which grants every endpoint for every customer who connects -
     a grant incomparably wider than Drive's `drive.file` and a direct conflict with
     the Global combining principle.

  **This section does NOT resolve that conflict.** Both tools are implemented and
  both are permission-gated, so an org that has authorized nobody can perform
  neither (Q2.6d). But `integration.asana_add_workspace_user` cannot be *exercised*
  under the granular scope set recommended above, and requesting Full permissions to
  enable one verb is a decision only the owner can make. See Q2.6g.

- **Q2.6b `[OWNER-DECISION-REQUIRED]` Which write actions are gated.** Mirroring
  2.5's Q2.5b severity split. Four verbs are in scope for this pass:
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
  - **`POST /workspaces/{workspace_gid}/addUser`.** `[RESEARCH-SUGGESTED]`
    **HIGH-RISK, and wider than anything in the Drive connector.** `[LIVE-VERIFIED]`
    "Add a user to a workspace or organization. The user can be referenced by their
    globally unique user ID or **their email address**", and the response is "the
    full user record for the **invited** user" - so this endpoint invites, it does not
    merely attach an existing member. It grants at **organization** scope rather than
    per-object; Drive's narrowest-scope design deliberately kept every action to
    app-created files. Subject to Q2.6a's scope blocker.

- **Q2.6c `[OWNER-DECISION-REQUIRED]` Governance narrative - RESEARCH POSITION.**
  `[RESEARCH-SUGGESTED]` Asana's role is **agency work-intake and delivery
  tracking**: an agent turns an engagement into tracked work in the client's own
  Asana - creating the project and the tasks that constitute a deliverable - and,
  exceptionally, brings a named stakeholder into that project so they can follow it.
  As with 2.5's deliverable-teslimi framing, this narrative and the two technical
  answers are load-bearing on each other: it is what makes `tasks:write
  projects:write` sufficient (Q2.6a) and what makes creation the routine verb and
  membership the exceptional one (Q2.6b). Changing one without revisiting the others
  is not safe.

- **Q2.6d `[DECIDED - owner, 2026-09-02]` Decision Engine hook: REUSE the existing
  gate, build nothing new.** The gap 2.5's Q2.5d identified is unchanged at this
  commit: `[CODE-VERIFIED]` the synchronous Decision Engine gate runs once per
  `/agents/execute` request, and `ToolProxy` holds no `DecisionEvaluator`
  (`grep -rn "DecisionEvaluator" src/skylize/tools/` returns nothing, re-confirmed).
  Asana's two membership verbs therefore need what Drive's sharing needed.

  **They get it from the SAME mechanism, not a second one.** `ToolPermissionProfile`
  (`tools/base.py:89-121`), the third opt-in `ToolProxy` stage shipped in `2448819`
  (`proxy.py:266-270`, handler `:400-476`), is provider-agnostic by construction -
  its docstring already states "Nothing here is Drive-specific: the gate knows about
  an `action_class`, a grantee, and a role" (`app/permissions/gate.py:5-6`). Asana
  supplies two new `action_class` values and reuses everything else: the
  `org_permission_grants` allow-list (migration 0022), exact-address-or-bare-domain
  matching, the deny-by-default asymmetry, and the audited denial path.

  **No HITL deferral**, following the Q2.1d precedent 2.5 relied on: a HITL replay
  would re-execute the original action a second time on approval. The allow-list is
  the mechanism; a human pre-authorizes recipients, and the agent may then act only
  within that pre-authorization.

  Action classes: **`asana.project.add_members`** and
  **`asana.workspace.add_user`**. Deliberately distinct, so an org can pre-authorize
  project membership without thereby authorizing workspace invitations - the two
  differ in blast radius by an order of magnitude (Q2.6b).

- **Q2.6e `[DECIDED - owner, 2026-09-02]` Asana has no role axis: map to a FIXED
  `writer` in application code, never in the schema.** `[CODE-VERIFIED]`
  `org_permission_grants.max_role` is `CHECK (max_role IN ('reader', 'commenter',
  'writer'))` (`0022:85-86`) with `ROLE_RANK = {"reader": 0, "commenter": 1,
  "writer": 2}` (`dal/permission_grants.py:27`), and `ToolPermissionProfile`
  requires a non-empty `role_field` (`tools/base.py:116`). `[LIVE-VERIFIED]` Asana's
  `addMembers` and `addUser` accept **no role or access-level parameter at all** - a
  project member is a project member.

  **The CHECK constraint is NOT relaxed.** A nullable or free-text role would be a
  role-less escape hatch that any future provider could use to bypass the rank
  comparison entirely, and the constraint is one of the few places the gate's
  ordering is enforced by the database rather than by code. Instead, both Asana
  membership tools carry `role: Literal["writer"] = "writer"` on their input schema.
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

  **Open for the owner:** now that the premise is corrected, the override may be
  judged unnecessary complexity and dropped in favour of the default. Recorded as a
  live question rather than settled by me.

- **Q2.6g `[OWNER-DECISION-REQUIRED]` Explicitly out of scope for this section.**
  Recorded so a future session does not assume these are covered by omission:
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
- 2.4 GitHub: __________________________________________  (owner, date)
- 2.5 Google Drive (scope Q2.5a verified, write actions Q2.5b, governance
  narrative Q2.5c, and Decision Engine hook Q2.5d decided; Q2.5e out-of-scope list
  stands as recorded): Approved  2026-08-31  (owner)
- 2.6 Asana (DRAFT - scope Q2.6a BLOCKED on the Full-permissions question for
  `addUser`; write actions Q2.6b, narrative Q2.6c, gate reuse Q2.6d, fixed-`writer`
  mapping Q2.6e, and revocation override Q2.6f drafted; Q2.6g out-of-scope list
  stands as recorded): ___________________________________  (owner, date)
- 3.0 Credential schema: _______________________________  (owner, date)
