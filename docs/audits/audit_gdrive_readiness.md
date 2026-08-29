# Audit — Google Drive connector readiness (pre-`integration_inputs.md` audit)

> **Status: AUDIT ONLY — no code, no schema, no `integration_inputs.md` content produced.**
> **Date:** 2026-08-29
> **Commit audited:** `68bc79b` (branch `main`, clean tree)
> **Scope:** Tier 0 step 3 — determine whether a Google Drive OAuth connector can be
> specified in `integration_inputs.md` yet, and what blocks it.
> **Method:** Anti-Fabrication Protocol. Every claim below is either cited `file:line`
> or explicitly marked **not found in codebase**. `.claude/skills/` is vendored
> third-party material and was excluded from every search; all greps are scoped to
> `src/ migrations/ tests/ docs/ policy/ scripts/ infra/`.

---

## 0. Executive summary — BOTH hard exit gates tripped

| Gate | Result |
|---|---|
| **Gate 1** — "STOP if OAuth refresh-token handling does not exist" | **TRIPPED.** No third-party OAuth refresh logic exists anywhere. See [§C.1](#c1-oauth-refresh--gate-1-tripped). |
| **Gate 2** — "STOP if `org_credentials` cannot hold a per-provider token shape without a migration" | **TRIPPED.** The table stores one opaque string per `(org_id, provider, label)`; a Drive grant needs at minimum access token + refresh token + expiry + granted scopes. A migration is unavoidable. See [§C.2](#c2-org_credentials-cannot-hold-an-oauth-grant--gate-2-tripped). |

A third blocker, not anticipated by the task framing, was found:

- **`§2.4` is already occupied by GitHub** (`docs/06_integrations/integration_inputs.md:410`).
  A Google Drive section cannot be written as `§2.4`. Section numbering is an owner
  decision, not an editorial one — see [§D.1](#d1-section-numbering).

Consequence: **`integration_inputs.md` cannot receive an approvable Google Drive
section in its current state.** Drive is the first provider that would require the
OAuth broker to actually exist — Slack was approved specifically by *avoiding* it
(`integration_inputs.md:337-342`), and Stripe's decided OAuth flow (`:263-271`) is
still unimplemented. Drive has no non-OAuth escape hatch.

---

## A. What exists and works

### A.1 `CredentialVault` — full CRUD, audited, per-org

`src/skylize/app/credentials/vault.py:20-144`. Public surface:

| Method | Line | Notes |
|---|---|---|
| `store(org_id, provider, raw_value, *, label, metadata, correlation_id)` | `:31-61` | Fernet-encrypts `raw_value`; audits `credential.stored` (`:53-59`) |
| `retrieve(org_id, provider, label='')` | `:63-73` | Returns plaintext `str`. Docstring: "Return value MUST NOT appear in logs" (`:69`) |
| `rotate(org_id, provider, new_value, *, label, correlation_id)` | `:75-100` | Overwrites `encrypted_value`, stamps `rotated_at` (`:87-92`); audits `credential.rotated` |
| `delete(...)` / `delete_by_id(...)` | `:102-141` | Both audit `credential.deleted` |
| `list_providers(org_id)` | `:143-144` | Distinct `provider` values |

This is genuinely working code with tenant isolation enforced at the DAL:
`PgCredentialRepository` runs every statement inside `tenant_session(org_id)` **and**
carries a redundant explicit `org_id` predicate (`src/skylize/dal/credentials.py:80-123`),
so RLS and the query agree rather than relying on RLS alone.

**Assessment against a Google OAuth token.** The API surface is the *right shape* for
one opaque secret and the wrong shape for an OAuth grant:

- `store`/`rotate` take a single `raw_value: str` (`vault.py:35`, `:79`). An
  access token + refresh token + `expires_at` + granted-scope list is four values.
- `retrieve` returns a bare `str` (`:63-73`). A Drive caller needs to know *whether the
  access token is still valid* before using it; `retrieve` cannot express that, and
  there is no `expires_at` to consult.
- `metadata_json` exists (`:38`, `:48`) but is **plaintext JSONB** (`0007:43`) — it is
  a legitimate home for `expires_at` and granted scopes, and an illegitimate one for a
  refresh token. `integration_inputs.md:459-461` already states this constraint.
- `rotate` is a *manual* operation requiring a `correlation_id` and writing an audit
  row (`:93-99`). It is not a refresh primitive; see [§C.1](#c1-oauth-refresh--gate-1-tripped).

### A.2 `org_credentials` table — RLS-hardened, unchanged since 0007

`migrations/versions/0007_org_credentials.py`. Columns (`:37-46`): `id`, `org_id`,
`provider`, `label`, `encrypted_value`, `metadata_json`, `created_at`, `rotated_at`.
`RLS ENABLE` + `FORCE` (`:59-61`), `tenant_isolation` policy on
`current_setting('skylize.org_id')` for both `USING` and `WITH CHECK` (`:63-68`),
`skylize_app` granted SELECT/INSERT/UPDATE/DELETE (`:70-72`), unique on
`(org_id, provider, label)` (`:54-57`).

**Verified: no migration after 0007 alters this table.** `grep org_credentials
migrations/versions/*.py` excluding `0007_` returns exactly one hit, and it is a
prose comment, not DDL (`migrations/versions/0019_principal_authority.py:233`).
Migrations run to `0020_seed_owner_principal.py`.

### A.3 The one real connector precedent — HubSpot

`src/skylize/tools/builtin/hubspot_tools.py` is the only implemented per-org
integration and is the pattern a Drive tool would follow:

- Token resolved via `CredentialVault.retrieve(org_id, "hubspot")` **on every call,
  never cached, never from an env var** (`:3-6`), so rotation/disconnection takes
  effect immediately.
- One `httpx.AsyncClient` per call, built from that call's token, so a credential
  cannot leak across orgs via a shared client (`:78-88`).
- "Not connected" degrades to a clean `ToolExecutionError`, not a 500
  (`_resolve_token`, `:129-137`).
- Retries only on rate-limit/5xx, `reraise=True` (`:95-100`, `:111-116`).

Note the retry shape is safe for HubSpot's verbs but is exactly the hazard
`integration_inputs.md:243-256` flags for non-idempotent writes. A Drive `files.create`
inherits that hazard directly — see [§D.5](#d5-idempotency-for-drive-writes).

### A.4 `ToolProxy.invoke` — the live governed dispatch path

`src/skylize/tools/proxy.py:82-256` is the class wired at `src/skylize/bootstrap.py:590`
and consumed by `AgentExecutionService` (`src/skylize/app/agents/execution.py:63`, `:214`).
**This is where a Drive tool would hook in.** Ordered gates before any side effect:

1. Registry resolution, fail-closed on unknown `tool_id` (`proxy.py:118-126`).
2. Full ordered token pipeline via `validate_tool_call` (`:129-146`) — signature,
   expiry, revocation, scope, delegation.
3. `max_calls_per_run` contract ceiling (`:152-166`).
4. Convergence breaker, recorded **before** dispatch so no side effect runs on a
   runaway loop (`:168-192`).
5. Input schema validation (`:194-201`).
6. **Spend reservation for spend-capable tools only** (`:203-214`) — see [§B.1](#b1-integration_inputsmd-11-is-now-partly-stale).
7. Dispatch (`:217`), then audit-before-settle: the audit row is written *before* the
   ledger commit precisely so a ledger failure cannot erase evidence that a real-world
   action ran (`:232-240`).

Every denial path audits before raising (`:120-124`, `:139-146`, `:160-166`, `:184-190`).

### A.5 First-party session-token refresh (adjacent, not reusable)

`src/skylize/app/auth/tokens.py:36` (`create_refresh_token`) and
`src/skylize/app/auth/user_service.py:100-152` implement refresh-token rotation with
JTI revocation (`:110-117`) and expiry checks (`:113`). Listed here only to forestall
a false positive: this is **Skylize issuing its own JWTs to its own users**, symmetric,
signed with `settings.jwt_secret` (`user_service.py:102`). It shares the word
"refresh" with OAuth and nothing else. See [§C.1](#c1-oauth-refresh--gate-1-tripped).

---

## B. What exists but is broken, stale, or incomplete

### B.1 `integration_inputs.md` §1.1 is now partly stale

§1.1 (`:106-177`) is titled **"BLOCKING FINDING: agent-initiated external actions face
no money ceiling"** and states "there is no monetary ceiling check on a tool-initiated
external action at any point" (`:117-118`), and that `ToolProxy` "holds no
`DecisionEvaluator` and no `CapitalRepository`" (`:132-134`).

**That has been overtaken by code.** Commit `87edd2f` (2026-08-25, *after* the doc's
2026-08-22 compile date at `:4`) added `feat(tools): enforce the spend ceiling on the
tool-call path via SpendLedger`. `ToolProxy` now holds a `SpendLedger` and reserves
against it before dispatch (`proxy.py:203-214`, `_reserve_spend` at `:258-332`),
wired at `bootstrap.py:587-595`. It fails closed on a missing ledger (`:283-287`) and
on a token with no `on_behalf_of` principal (`:294-302`).

**What has NOT changed, and what this means for Drive:**

- `ToolProxy` still holds no `DecisionEvaluator`. Verified: `grep -rn
  "DecisionEvaluator\|evaluator" src/skylize/tools/` returns **zero hits**. The doc's
  wording at `:133-134` remains accurate on that specific point.
- The BUDGET stage of the token pipeline is still structurally unreachable for tool
  calls — `proxy.py:136-137` still passes `requested_token_cost=0` and
  `tokens_used_so_far=0` with the comment "tool calls don't debit the LLM token budget".
  §1.1's Path-A analysis (`:127-134`) still holds.
- **The spend gate is opt-in and only fires for spend-capable tools.**
  `ToolSpendProfile` is `None` by default (`src/skylize/tools/base.py:72-74`) and
  `proxy.py:209` reserves only `if tool.spend is not None`.

**A Google Drive tool is not spend-capable.** It moves no money, so it would declare no
`ToolSpendProfile`, so §A.4 step 6 never fires for it. §1.1's staleness is therefore
**not a reason to unblock Drive** — Drive was never going to be gated by the money
path. It is recorded here because `integration_inputs.md:478-479` lists "Section 1.1
resolved and implemented" as precondition #1 for *any* connector, and that precondition
now needs re-adjudication by the owner rather than treating the doc text as current.

### B.2 Three distinct `ToolProxy` classes; only one is live

This directly answers the standing memory note that `runtime/tool_proxy.py:219-228` is
"a second unguarded dispatch proxy used by `agent_runner.py:256`".

**The "unguarded" characterisation is incorrect as stated.** Lines `219-228` of
`src/skylize/runtime/tool_proxy.py` are the **BUDGET stage inside `validate_token`** —
they compute `remaining = token.max_token_budget - tokens_used` and raise
`BudgetExceeded`. They are a guard, not a bypass. And `agent_runner.py:256` calls
`dispatch_llm`, which runs `validate_token` first (`runtime/tool_proxy.py:246-283`,
docstring `:253-256`).

**The real structural finding is different and larger.** Three classes named or
formerly named `ToolProxy` coexist:

| Class | Location | Wired? |
|---|---|---|
| `ToolProxy` (registry/spend/audit) | `src/skylize/tools/proxy.py:82` | **LIVE** — `bootstrap.py:590`, used by `app/agents/execution.py:214` |
| `ToolProxy` (Redis-backed, LLM-only) | `src/skylize/runtime/tool_proxy.py:143` | **Not wired.** Constructed only in tests |
| `RegistryToolProxy` | `src/skylize/runtime/tool_proxy.py:374` | **Not wired.** No `bootstrap.py` construction site |

`runtime/tool_proxy.py:13-17` acknowledges the rename collision in its own module
docstring. Its consumer `LLMAgentRunner` (`runtime/agent_runner.py`) is exported
(`runtime/__init__.py:12`, `app/orchestrator/__init__.py:5`) but **constructed only in
tests** — `grep -rn "LLMAgentRunner(" src tests` returns four hits, all under `tests/`
(`tests/integration/test_llm_agent_runner_e2e.py:247,414`;
`tests/runtime/test_agent_runner.py:181`; `tests/unit/test_llm_agent_runner.py:73`).
`grep "AgentRunner" src/skylize/bootstrap.py` returns nothing.

**Effect on a Drive tool:** none, positively — the second proxy is LLM-only.
`dispatch_llm` dispatches exclusively to `LLMGateway` (`runtime/tool_proxy.py:246-283`)
and cannot dispatch a Drive tool at all. **A Drive tool hooks into
`src/skylize/tools/proxy.py` only.** The risk is a future author registering a
connector against the wrong proxy; that is a hygiene concern for the owner, not a
Drive blocker. Flagged, not designed.

### B.3 Stale comment: `dal/credentials.py` cites the wrong migration

`src/skylize/dal/credentials.py:4` says the table is RLS-scoped by "migration 0010".
It is created by **0007** (`migrations/versions/0007_org_credentials.py:27`); `0010` is
`0010_workflow_run_steps.py`. Already recorded at `integration_inputs.md:98-104` as a
known comment-only defect. **Confirmed still present at this commit.** Not fixed —
audit-only pass.

### B.4 The OAuth broker is still zero lines

`integration_inputs.md:73-79` records "Faz A-D of the OAuth broker exist only as plans;
zero lines are implemented", verified by the doc's authors on 2026-08-22.
**Re-verified independently at this commit and the finding is unchanged:**
`grep -rni "oauth" src/ migrations/ tests/ policy/ scripts/ infra/` returns hits in
exactly one file — `migrations/versions/0007_org_credentials.py` — and that hit is
prose in the module docstring (`:8`, "e.g. a HubSpot API key or Slack OAuth token"),
not code. **`src/` contains zero occurrences of the string "oauth" in any casing.**
No authorize endpoint, no callback route, no `state`/PKCE handling, no token exchange.

---

## C. What does not exist at all — net new

### C.1 OAuth refresh — GATE 1 TRIPPED

**There is no third-party OAuth token-refresh logic anywhere in the codebase.**

Search performed: `grep -rn "refresh_token\|refresh-token\|expires_at\|expires_in\|
token_refresh\|_refresh(" --include=*.py src/skylize`. Every `refresh_token` hit
resolves to one of two unrelated systems:

1. **Skylize's own first-party JWT sessions** — `app/auth/tokens.py:36`,
   `app/auth/user_service.py:37,100-152`, `app/auth/service.py:38-97`. Skylize signs
   these itself with `settings.jwt_secret` (`user_service.py:102`). No external
   provider, no token endpoint, no client secret, no HTTP exchange.
2. **Unrelated `expires_at` fields** — HITL queue expiry (`app/hitl/service.py:106,418`),
   governance-token expiry (`app/governance/authority.py:324,336`), Slack message
   display (`app/notifications/slack.py:51,59`).

`grep -rn "expires_in"` returns **zero hits repo-wide** — the OAuth-specific field name
appears nowhere.

**Why this is a genuine capability gap and not a small one.** Slack, the only approved
integration, was approved on terms that structurally avoid refresh: `integration_inputs.md:337-342`
records "**No OAuth broker for Slack**" because the credential is platform-level, and
the shipped notifier confirms it — `src/skylize/app/notifications/slack.py:38-39` takes
a `bot_token: str` in its constructor, holds it for process lifetime
(`:35-36`: "the token and channel never vary per-call"), and sends it as a static bearer
(`:66`). Slack bot tokens do not expire. **Google OAuth access tokens do.** A Drive
connector is the first thing in this repo that would need:

- a token endpoint exchange (`authorization_code` → tokens; `refresh_token` → new access token);
- a client secret at platform level;
- refresh-on-expiry-or-401 at call time, inside the per-call resolution pattern §A.3
  established — with concurrency control, since two agent runs for one org can refresh
  simultaneously and Google may rotate the refresh token;
- handling of a *revoked* grant (`invalid_grant`), which `CredentialVault` has no state
  to express (`integration_inputs.md:465-467` records "No revocation state").

`CredentialVault.rotate` (`vault.py:75-100`) is **not** this primitive. It requires a
caller-supplied `correlation_id`, writes a `credential.rotated` audit row per call, and
takes a single `new_value: str`. Repurposing it as an automatic refresh path would emit
an audit record on every token refresh and still could not persist the new expiry.

**Per the stated exit gate, this is reported and not designed.** Whether refresh lives
in the vault, in a broker service, or in the connector is [Q-D.3](#d3-where-does-refresh-live).

### C.2 `org_credentials` cannot hold an OAuth grant — GATE 2 TRIPPED

The table stores exactly one opaque encrypted string per `(org_id, provider, label)`
(`0007:37-46`). A Google Drive grant minimally requires: `access_token`,
`refresh_token`, `expires_at`, `granted_scopes`, and an account identity (which Google
account/domain the grant belongs to).

`integration_inputs.md:452-471` already enumerates six gaps — no `expires_at`, no
refresh-token slot, no granted-scope column, no revocation state, no key reference, no
account-identity column — and I **re-verified each against `0007:37-46` at this
commit; all six still hold.** Notably `metadata_json` is plaintext JSONB (`0007:43`)
and must never hold a refresh token (`integration_inputs.md:459-461`).

**A migration is unavoidable.** Per the exit gate, **no migration is designed here.**
The owner decision `Q3.0a` (`integration_inputs.md:473-479`) — extend `org_credentials`
vs. add `org_oauth_grants` — is unanswered and now blocks Drive directly. Note the doc
narrows `Q3.0a` to org-level providers only (`:481-490`); Drive's classification is
itself undecided, see [§D.2](#d2-is-drive-org-level-or-platform-level).

### C.3 Zero Google or Drive references — anywhere

`grep -rniE "GOOGLE|GDRIVE|GOOGLE_DRIVE|drive\.file|googleapis" src/ migrations/
tests/ policy/ scripts/ infra/ docs/` yields **no Drive-related code, config var, or
doc section**. The only matches are:

- `tests/unit/test_credential_routes.py:133,139` and
  `tests/unit/test_credential_vault.py:139` — the literal string `"google_ads"` used as
  an arbitrary provider name in vault tests. Test fixture data, not an integration.
- `infra/terraform/staging/modules/ecs/main.tf:201` — `logDriver = "awslogs"`. Substring
  coincidence on "Driver".

**There is no dead code, no partial attempt, and no stale config to reconcile.**
`docs/06_integrations/` contains no Google or Drive document. Drive is genuinely
greenfield — which is the one favourable finding in this audit.

### C.4 No Google SDK, and none is expected

`integration_inputs.md:80-85` records that no `google-cloud-*` package is a dependency
and that `website/package.json` carries no Google package. The HubSpot precedent
(`hubspot_tools.py:76`, "Thin wrapper over HubSpot's REST API — no SDK dependency")
suggests a Drive connector would follow suit with raw `httpx`. **Recorded as
observation, not recommendation** — SDK-vs-raw-HTTP is not mine to decide this pass.

### C.5 No per-tenant key management

`integration_inputs.md:86-91`: a single platform-wide Fernet key
(`src/skylize/bootstrap.py:322-323`, `src/skylize/config.py:80`), no KMS, no key-ref
column, no master-key rotation. Unchanged. This matters more for Drive than for
HubSpot: a Drive refresh token is a long-lived credential to a customer's document
store, so the blast radius of the single-key design rises. Flagged for the owner as an
input to [Q-D.4](#d4-does-drive-change-the-encryption-posture); not designed here.

### C.6 No per-call Decision Engine hook for non-spend external writes

Answering review item 6 precisely. **A write-type external action reaches the Decision
Engine only if it is spend-capable, and only via `SpendLedger` — not via
`DecisionEvaluator`.**

- The synchronous Decision Engine gate runs **once per `/agents/execute` request**, at
  stage 2.5, before the token mint (`app/agents/execution.py:283-294`,
  `_run_decision_gate` at `:513-560`). It evaluates a `DecisionProposal` for the
  *request*, not for each tool call the agent subsequently makes.
- `integration_inputs.md:135-143` (`[CODE-VERIFIED]`) records that this proposal carries
  no spend and that an `agent.execute` proposal returns **terminally** from
  `_decide_agent_execution` at evaluator stage 2.5, *ahead of* stage 4 `capital_check`,
  so the capital stage is never reached on the request path.
- `ToolProxy.invoke` holds no evaluator (verified zero grep hits, §B.1) and calls none.

**So the honest answer for a Drive write:** there is currently **no per-action
governance verdict** for it. `files.create` and `permissions.create` (a sharing change —
arguably the highest-consequence Drive verb) would be gated by contract `allowed_tools`,
token scope, `max_calls_per_run`, and the convergence breaker (§A.4 steps 1-4) — all
real controls — but **not** by any `decision.*` verdict, and not by HITL deferral.
Slack's HITL notifier does not change this: it is a *post-only notifier* for decisions
made elsewhere (`app/notifications/slack.py:1-19`), not a gate.

Whether Drive verbs need per-verb governance — and Drive sharing is the natural place
to ask — is [Q-D.6](#d6-per-verb-gating-for-drive).

---

## D. Open questions requiring owner decision

These are stated as questions. **No recommendation is offered where the task's
audit-only scope forbids one**; where `integration_inputs.md` already carries a
`[RESEARCH-SUGGESTED]` position, it is cited rather than restated as my own.

### D.1 Section numbering
`§2.4` is **GitHub** (`integration_inputs.md:410`). Sections run `2.0` classification,
`2.1` Stripe, `2.2` AWS/GCP, `2.3` Slack, `2.4` GitHub, then `3.0`. The Tier 0 task
names Drive as "§2.4"; that slot is taken. Does Drive become `§2.5`, and does the
file's header scope line (`:5`, "Scope: Stripe, AWS, GCP, Slack, GitHub") get amended
to include it? **Owner call — renumbering an approved document is not an editorial
decision**, particularly as `§2.3` is `[APPROVED]` and signed (`:501`).

### D.2 Is Drive org-level or platform-level?
The `§2.0` classification table (`:192-199`) does not list Google Drive. Every prior
provider's entire design followed from this answer — Slack's `[APPROVED]` outcome
(platform-level ⇒ no broker, no `org_credentials` row, no RLS, env-var token,
`:337-355`) is the clearest demonstration. **Drive cannot be specified until it is
classified.** If org-level, it needs the broker, the grant table, and refresh. If
platform-level (a Skylize service account / domain-wide delegation), it needs none of
those and looks like Slack. This is the highest-leverage unanswered question in this
audit; everything in §C.1 and §C.2 is contingent on it.

### D.3 Where does refresh live?
If D.2 answers org-level: does refresh live inside `CredentialVault` (widening its
scope from "one opaque string" — the contract HubSpot and
`tests/integration/test_jsonb_readback_pg.py:230` rely on, per
`integration_inputs.md:475-479`), inside a new broker service, or inside the connector?
Sub-questions the owner will need to resolve regardless of venue: concurrent-refresh
behaviour when two runs for one org refresh at once; whether each refresh emits an
audit row (`vault.rotate` currently does, `vault.py:93-99`); and what happens on
`invalid_grant` given there is no revocation state (`integration_inputs.md:465-467`).

### D.4 Does Drive change the encryption posture?
A Drive refresh token is a long-lived credential to a customer's documents, protected
today by one platform-wide Fernet key with no rotation (§C.5). Does that remain
acceptable, or does Drive force the KMS/envelope-encryption question
(`integration_inputs.md:468-471`, gap 5) that has so far been deferred?

### D.5 Idempotency for Drive writes
`integration_inputs.md:243-256` (Q2.1d) establishes that the HubSpot retry shape —
retry on 429/5xx with `reraise=True` (`hubspot_tools.py:95-100`) — **duplicates a write
on a timeout-then-success**. Drive `files.create` has exactly this hazard. The doc also
records that a deterministic idempotency key is **not implementable through
`ToolProxy.invoke` as it stands**: `proxy.py:322` hardcodes
`f"tool:{tool.tool_id}:{uuid4()}"` and `:314-321` states "Idempotent replay needs a
caller-supplied key, which this signature does not accept." **Re-verified at this
commit — both still true.** Note this bites Drive even though Drive is not
spend-capable: the *concept* is unresolved and the proxy signature is the same one a
Drive tool would use.

### D.6 Per-verb gating for Drive
Given §C.6 — no per-action Decision Engine verdict for non-spend external writes —
which Drive verbs, if any, require a governance verdict or human deferral rather than
contract/scope checks alone? `§2.4`'s GitHub treatment is the structural precedent: it
demands "explicit per-verb decision required for at minimum" a named list, with
`[RESEARCH-SUGGESTED]` defaults per verb (`:418-424`). The Drive analogue would need at
minimum: file create; file/folder delete; **permission grant (sharing), including
`anyone-with-link`**; ownership transfer; and shared-drive membership change. Sharing
is the verb with no GitHub analogue and the largest confidentiality blast radius.

### D.7 Scope derivation
`§2.3` sets the precedent: derive the **narrowest** scope from the single planned
action, justify it against the attenuation-only principle (`:29-38`), refuse to request
broader scopes without a corresponding planned action, and mark the scope string
`UNVERIFIED` until checked against the provider's live documentation
(`:356-368` — Slack's own `chat:write` claim is still carried as unverified). Drive's
scope choice (`drive.file` vs `drive` vs `drive.readonly`) cannot be derived until the
planned action list from D.6 exists. **No scope string is proposed here.** Note
`grep "drive.file"` returns **not found in codebase** — no prior scope claim exists to
inherit or contradict.

### D.8 Does §4.0 precondition #1 still bind?
`integration_inputs.md:478-479` makes "Section 1.1 resolved and implemented" precondition
#1 before **any** connector code. Per §B.1, the tool path acquired a spend ceiling in
`87edd2f` — but via `SpendLedger`, not the `capital_check` the section asks for, and
only for opt-in spend-capable tools. Is precondition #1 satisfied, partially satisfied,
or unsatisfied — and does it gate a **non-spend** connector like Drive at all? As
written it does, by "ANY". **Owner adjudication needed**, since a literal reading
blocks Drive on a money question Drive does not raise.

---

## E. Verification log

Searches run at commit `68bc79b`, all scoped away from `.claude/skills/` (vendored):

| Search | Result |
|---|---|
| `oauth\|OAuthBroker\|oauth_broker` in `src/ migrations/ tests/ policy/ scripts/ infra/` | 1 file, prose only: `migrations/versions/0007_org_credentials.py:8`. Zero hits in `src/` |
| `GOOGLE\|GDRIVE\|GOOGLE_DRIVE\|drive\.file\|googleapis` in same dirs + `docs/` | No Drive integration. Only `"google_ads"` test fixtures + `logDriver = "awslogs"` |
| `refresh_token\|expires_in\|token_refresh` in `src/skylize` | `expires_in`: **zero hits**. `refresh_token`: first-party JWT only |
| `org_credentials` in `migrations/versions/` excluding `0007_` | 1 hit, a comment (`0019:233`). No DDL change |
| `DecisionEvaluator\|evaluator` in `src/skylize/tools/` | **Zero hits** |
| `LLMAgentRunner(` in `src/ tests/` | 4 hits, all under `tests/` |
| `AgentRunner` in `src/skylize/bootstrap.py` | Zero hits |

**Not verified this pass (declared, not assumed):** no tests were run; no live Google
documentation was fetched, so no Drive scope string or API behaviour in this document
is verified against Google — none is asserted. `docs/REPO_STATE.md` was not used as a
source; all claims come from code and from `integration_inputs.md` re-verified against
code.

## F. Changes made

**None.** No code, no schema, no migration, no `integration_inputs.md` edit. The two
defects re-confirmed in §B.1 (stale §1.1) and §B.3 (wrong migration number in
`dal/credentials.py:4`) were deliberately **not** fixed — both are outside an
audit-only pass, and §B.1 in particular requires the owner adjudication in [D.8](#d8-does-40-precondition-1-still-bind).
