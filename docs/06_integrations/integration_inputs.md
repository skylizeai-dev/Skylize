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

> **Section status: `[OWNER-DECISION-REQUIRED]` - BLOCKED additionally on 1.1.**

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
- 2.1 Stripe: __________________________________________  (owner, date)
- 2.2 AWS / GCP: _______________________________________  (owner, date)
- 2.3 Slack: Approved as post-only HITL notifier (2.3 above)  2026-08-28  (owner)
- 2.4 GitHub: __________________________________________  (owner, date)
- 3.0 Credential schema: _______________________________  (owner, date)
