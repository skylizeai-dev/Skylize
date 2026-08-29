# OAuth Provider Infrastructure — provider-agnostic credential design

> **Status: DESIGN ONLY — NOT APPROVED, NOT IMPLEMENTED.**
> No code, no migration, no `CredentialVault` change, no Drive-specific logic.
> **Date:** 2026-08-29
> **Commit designed against:** `0e34060` (branch `main`, clean tree)
> **Predecessor:** `docs/audits/audit_gdrive_readiness.md` (commit `0e34060`)
> **Consumers:** Google Drive (Tier 0 step 3), then Notion / Asana. Single
> implementation, single security surface, single audit path.
>
> Claim marking follows `integration_inputs.md:14-18`:
> `[CODE-VERIFIED]` = read out of the tree at this commit ·
> `[INFERRED]` = derived from code by reasoning, **not executed** ·
> `[DESIGN]` = proposed here, needs owner approval ·
> `[OWNER-DECISION-REQUIRED]` = no safe default exists.

---

## 0. Gate report — read this first

### 0.1 Gate "uniqueness constraint conflict" — **NOT tripped**

The instruction was to stop if `org_credentials`'s `(org_id, provider, label)`
uniqueness conflicts with needing multiple token fields per row. **It does not, and I
will not manufacture a conflict that isn't there.**

Multiple token fields is a request for more **columns**, not more **rows**. The unique
index (`migrations/versions/0007_org_credentials.py:54-57`) governs row *identity* —
"one credential per (org, provider, label)" — which is exactly the right cardinality
for an OAuth grant: one live grant per org per provider per named connection. The
constraint is an asset here, not an obstacle.

**One real limitation, distinct from a conflict** `[CODE-VERIFIED]`: `label` is the
*only* discriminator between two connections of the same provider (`0007:41`, `''` =
default), and there is no account-identity column — already logged as gap 6 at
`integration_inputs.md:469-471`. So "which Google account is this?" is answerable
only by free-text convention. §1 addresses that with a real column; it is a gap, not a
constraint conflict. **No constraint is silently redesigned in this document.**

### 0.2 Gate "Temporal unproven" — **TRIPPED**

The task states "Temporal Cloud is already in the stack" and "durable execution is a
stated platform principle." **The first half is not accurate at this commit.** Temporal
is *present* but has **never executed anything in any environment**:

| Check | Result |
|---|---|
| `@workflow.defn` definitions | **Zero, repo-wide.** `grep -rn "@workflow.defn\|from temporalio import workflow" src tests` → no matches |
| Worker's own docstring | "**No workflow definitions are registered yet** … Registration is activities-only" (`src/skylize/app/orchestrator/temporal/worker.py:20-22`) |
| Registered activities | Exactly two: `run_judge_verification`, `write_run_step` (`worker.py:64-67`) |
| Connection target | `temporal_address = "localhost:7233"`, `namespace = "default"` (`src/skylize/config.py:169-170`) — the **dev server**. No Cloud endpoint, no mTLS cert config, no API-key setting |
| Infra provisioning | `grep -rni temporal infra/` → **no matches.** No Temporal in Terraform |
| Deployment | ECS runs one container, `"api"` (`infra/terraform/staging/modules/ecs/main.tf:62`); `Dockerfile:83` CMD is `uvicorn` only. **The worker process is not deployed anywhere** |
| Tests | `tests/unit/test_temporal_worker.py` only, and it deliberately never connects — "Actually serving a task queue needs a reachable Temporal server (lazy clients…)" (`:12`), "run() must not reach `Client.connect`" (`:45`) |

`temporalio>=1.7` is a real dependency (`pyproject.toml:68`) and the activity code is
genuine, so this is a **credible foundation, not vapour** — but a background-refresh
recommendation would rest on a workflow engine that has never run a workflow, is not
provisioned, and is not deployed. **That dependency risk is hereby flagged explicitly,
per the gate.**

As it happens the recommendation in §2 does **not** depend on resolving this, because a
second and stronger objection to background refresh exists that is independent of
Temporal's maturity. See §2.2.

---

## 1. Schema design

### 1.1 What an OAuth grant must persist

Provider-agnostic minimum, derived from the OAuth 2.0 authorization-code flow that
Drive, Notion, and Asana all use:

| Field | Why | Secret? | Queried? |
|---|---|---|---|
| `access_token` | the bearer sent on each call | **yes** | no |
| `refresh_token` | obtains a new access token | **yes** | no |
| `access_token_expires_at` | nothing can know staleness without it | no | **yes** — every call |
| `granted_scopes` | attenuation invariant is uncheckable without it | no | **yes** |
| provider account identity | which Google account / Notion workspace | no | **yes** |
| `revoked_at` + reason | dead-grant state (§4) | no | **yes** |
| provider-specific extras | genuinely varies per provider | varies | no |

### 1.2 Structured columns vs JSONB — both arguments

**The case for JSONB.** One `grant_json` column absorbs every provider's shape without
a migration per provider — Drive's `id_token`/`token_type`, Notion's `workspace_id` and
`bot_id`, Asana's differing scope encoding. The repo uses JSONB liberally and it is
already ergonomic: `_init_connection` registers json/jsonb codecs on every pooled
connection so JSONB decodes to Python objects uniformly
(`src/skylize/dal/connection.py:30-44`), and the module notes that the absence of this
was "the defect class behind the deliverables 500" (`:34-35`). Adding a provider would
be a code change only.

**The case for structured columns.** Three code-verified facts decide it:

1. **This schema never queries inside JSONB.** `[CODE-VERIFIED]` Every JSONB column in
   the tree — `contract_json`, `proposal_json`, `score_json`, `verdict_json`,
   `metadata_json`, `attrs_json`, `config_json`, `provenance`, `payload`, `input`,
   `output`, `judge_verdict`, `detail`, `request_json` (`0001:69,179,185,210-216,240,
   265,282,335`; `0005:54`; `0006:56`; `0007:43`; `0009:58`; `0010:67-69`; `0015:46`;
   `0019:188`) — is written whole and read back whole. **There is no GIN index on any
   JSONB column anywhere.** The only GIN index in the schema is on a tsvector
   *expression* for full-text search (`0001:255`). The established convention is
   unambiguous: **JSONB = opaque payload; anything that appears in a predicate is a
   real column.**
2. **The precedent for exactly this problem is a real column plus a partial index.**
   `spend_reservation` needs to find rows by expiry, and the schema gives it
   `expires_at` as a column with `CREATE INDEX spend_reservation_sweep ON
   spend_reservation (expires_at) WHERE state = 'held'` (`0019:169-170`). An OAuth
   grant's expiry check is the identical access pattern.
3. **`metadata_json` is plaintext and must never hold a secret.** `[CODE-VERIFIED]`
   `0007:43` is a bare `JSONB NOT NULL DEFAULT '{}'`; only `encrypted_value` is
   ciphertext. `integration_inputs.md:459-461` already states the refresh token "would
   have to be smuggled into `metadata_json` (plaintext JSONB) or double-encoded into
   `encrypted_value`" and that this must not happen. A single `grant_json` blob would
   either repeat that mistake or require encrypting the whole blob — which then makes
   `expires_at` unreadable without decrypting, defeating (1) and (2) entirely.

**Decision `[DESIGN]`: structured columns for everything queried or secret; one
`provider_metadata JSONB` for genuinely provider-varying opaque extras.** Point 3 is
decisive on its own — encrypting the blob to protect the refresh token would bury the
expiry timestamp inside ciphertext, and every refresh strategy in §2 needs to read
expiry without decrypting anything.

### 1.3 Migration sketch — **NOT WRITTEN, NOT APPLIED**

Illustrative shape only, to make the discussion concrete. Per the hard exit gate, **no
migration file is created by this pass.**

```
-- SKETCH ONLY — not a migration, not applied.
CREATE TABLE oauth_grants (
    grant_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                   TEXT NOT NULL REFERENCES tenants(org_id),
    provider                 TEXT NOT NULL,           -- 'google_drive', 'notion', ...
    label                    TEXT NOT NULL DEFAULT '',-- mirrors org_credentials:41
    provider_account_id      TEXT NOT NULL,           -- closes gap 6
    access_token_encrypted   TEXT NOT NULL,           -- Fernet, as org_credentials
    refresh_token_encrypted  TEXT,                    -- NULL where provider issues none
    access_token_expires_at  TIMESTAMPTZ NOT NULL,    -- real column, see 1.2
    granted_scopes           TEXT[] NOT NULL DEFAULT '{}',
    provider_metadata        JSONB NOT NULL DEFAULT '{}',  -- NEVER secret material
    revoked_at               TIMESTAMPTZ,
    revoked_reason           TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    refreshed_at             TIMESTAMPTZ
);
-- Identity cardinality mirrors 0007:54-57 exactly.
CREATE UNIQUE INDEX oauth_grants_unique ON oauth_grants (org_id, provider, label);
CREATE INDEX oauth_grants_lookup ON oauth_grants (org_id, provider);
-- RLS ENABLE + FORCE + tenant_isolation policy, mirroring 0007:59-68 / 0019:246-253.
```

Two deliberate properties:

- **`granted_scopes TEXT[]`, not JSONB.** The attenuation-only invariant
  (`integration_inputs.md:29-38`) is a set-containment check; `TEXT[]` supports it in
  SQL and JSONB does not without a functional index this schema has no precedent for.
- **No `expires_at` sweep index in the sketch.** Under the §2 recommendation nothing
  scans by expiry across tenants, so the `spend_reservation_sweep` analogue is
  deliberately *absent*. If the owner reverses §2, that index is what gets added.

---

## 2. Refresh strategy — recommendation: **on-demand, not Temporal**

### 2.1 The two candidates

- **(A) On-demand / lazy.** Before dispatch, check `access_token_expires_at`; if within
  a skew margin, refresh synchronously inside the request, persist, proceed.
- **(B) Proactive background.** A scheduled Temporal workflow scans for grants nearing
  expiry across all tenants and refreshes them ahead of use.

### 2.2 The decisive objection to (B) is RLS, not Temporal

Gate 0.2 already establishes Temporal has never run a workflow. **But even a mature,
Cloud-connected Temporal deployment could not implement (B) without breaking a
deliberate security invariant**, which is why the recommendation does not hinge on
Temporal's maturity.

`[CODE-VERIFIED]` Tenant tables are `ENABLE` + `FORCE ROW LEVEL SECURITY` with
`tenant_isolation` keyed on `current_setting('skylize.org_id')` (`0007:59-68`,
`0019:246-253`). Migration 0002 added a cross-tenant carve-out for the Governance
Authority's startup warm-up — and granted it **for reads only, on purpose**:

> "READ (USING) passes when the row's org_id matches the tenant binding OR a read-only
> `skylize.rehydrate = 'on'` flag is set … **WRITE (WITH CHECK) is UNCHANGED — still
> requires a matching org_id, so the rehydrate flag can never be used to write across
> tenants.**" — `migrations/versions/0002_rehydrate_rls_carveout.py:11-16`

**A token refresh is a write.** It persists a new access token and a new expiry. So a
cross-tenant proactive refresher is blocked by an invariant the codebase adopted
deliberately and documented as deliberate. Implementing (B) requires one of:

1. a cross-tenant **write** carve-out — reversing 0002's stated decision;
2. running the sweeper as a **superuser** to bypass RLS — dissolving the isolation
   guarantee for the table holding every customer's document-store credentials;
3. enumerating orgs outside RLS and looping `tenant_session(org_id)` per org — legal,
   but it is (A)'s machinery wrapped in a scheduler, and it re-introduces the problem
   below.

Note also `org_credentials` is **not** in 0002's carve-out table list (`0002:33-36`) —
it did not exist yet — so it has no carve-out at all today, and a new `oauth_grants`
table would have none either unless one were deliberately added.

**This trap is not hypothetical — there is a live latent instance of it.**
`[INFERRED, not executed]` `SpendLedger.sweep_expired` (`src/skylize/app/principal/spend.py:421-447`)
is documented "Run from Temporal on a schedule, not from a request path" (`:422-423`) —
the closest thing in the tree to design (B). It acquires a raw connection via
`self._pool.acquire()` (`:424`) with **no** `set_config('skylize.org_id')` and no
rehydrate flag. The runtime pool connects as the non-superuser `skylize_app` role
(`src/skylize/config.py:31-32`, `src/skylize/bootstrap.py:363` → `runtime_db_url`),
and `spend_reservation` is FORCE-RLS with no carve-out (`0019:246-253`). With
`skylize.org_id` unset, `current_setting(..., true)` returns NULL and `org_id = NULL`
is NULL — never true — so **no row is visible and the sweep reclaims nothing.**
`Database.admin_session`'s own docstring states the rule plainly: "RLS tables return
nothing here by design" (`src/skylize/dal/connection.py:85-87`).

This is marked `[INFERRED]` and not `[CODE-VERIFIED]`: I reasoned it from the policy
text and the role, **I did not execute it**. It is plausibly untested for a reason —
`grep -rn sweep_expired src tests` shows **no caller anywhere in `src/`** and no
Postgres integration test; the only implementations exercised are unit-test stubs
(`tests/unit/test_tool_proxy_spend.py:152`, `tests/unit/test_principal_authority.py:298`).
**Recommend confirming this separately; it is a pre-existing defect in current code,
out of scope for this design pass, and is reported rather than fixed.**

### 2.3 Recommendation `[DESIGN]`: on-demand refresh inside `tenant_session`

| Dimension | (A) On-demand — **recommended** | (B) Background |
|---|---|---|
| RLS | Natural fit: runs inside `tenant_session(org_id)` (`connection.py:71-81`), the org is already bound by the request | Requires a write carve-out, a superuser, or per-org looping (§2.2) |
| Latency | One extra token-endpoint round trip **only when expired** — typically once per token lifetime (Google: ~1h), amortised to near zero | Zero in the hot path, when it works |
| Failure mode | Fails **before** egress, synchronously, as a clean typed denial. The agent never gets a half-valid state | A silent refresh failure is discovered by the request as a 401 mid-dispatch — after the tool has already started |
| Dependency risk | None new | Temporal has never run a workflow, is not deployed, and is not provisioned (§0.2) |
| Correctness under clock skew | Skew margin makes it conservative; worst case is a harmless early refresh | Same, plus a scheduling-lag failure class |
| Thundering herd | Needs concurrency control (below) | Naturally spread |

**Recommended parameters `[DESIGN]`**, all owner-adjustable:

- **Skew margin:** refresh when `access_token_expires_at - now < 5 minutes`. Never
  trust the token to the last second.
- **Concurrency.** Two agent runs for one org can refresh simultaneously, and Google
  may rotate the refresh token, so a loser could persist a stale one. Use
  `SELECT … FOR UPDATE` on the grant row inside the existing transaction —
  `tenant_session` already wraps every call in one (`connection.py:78-81`), and
  `FOR UPDATE SKIP LOCKED` is established in this codebase (`spend.py:435`).
- **Retry.** Bounded and short — the caller is a live request. `[OWNER-DECISION-REQUIRED]`
  whether to reuse the `tenant` retry shape from `hubspot_tools.py:95-100`
  (429/5xx, exponential, `reraise=True`); note a *refresh* POST is idempotent in a way
  a `files.create` is not, so retrying it is safe where §D.5 of the prior audit warned
  against retrying writes.
- **No caching across calls.** Matches the HubSpot precedent exactly — token resolved
  per call, never cached, so a rotated or revoked credential takes effect immediately
  (`hubspot_tools.py:3-6`).

**Not recommended, but worth recording:** (A) and (B) are not exclusive. Once Temporal
genuinely runs workflows, an opportunistic per-org refresher could reduce hot-path
refreshes — but only as an *optimisation over* (A), never as the correctness
mechanism, because (A) must remain the authority for the failure modes in row 3 above.

---

## 3. ToolProxy hook point

### 3.1 Which ToolProxy

`[CODE-VERIFIED]` `src/skylize/tools/proxy.py:82` — the class wired at
`src/skylize/bootstrap.py:590` and consumed by `AgentExecutionService`
(`src/skylize/app/agents/execution.py:214`). The other two (`runtime/tool_proxy.py:143`
and `:374`) are unwired and LLM-only; see `audit_gdrive_readiness.md` §B.2.

### 3.2 Exact insertion point

**Between `src/skylize/tools/proxy.py:201` and `:203`** — after input-schema validation
completes (`:194-201`), immediately before the spend-ceiling comment block that begins
at `:203`.

```
proxy.py:194-201   input schema validation            (existing)
proxy.py:  202  →  ** NEW: OAuth credential freshness stage **
proxy.py:203-214   spend reservation                  (existing)
proxy.py:217       handler dispatch                   (existing)
```

**Why exactly there, and not later** — the ordering is forced by the existing comment
at `:203-207`, which states the spend hold "is a shared mutable resource, so a hold
must be placed as late as possible — **after every cheaper denial has had its chance**
— to minimise the window in which budget is held for a call that was never going to
run." A dead or unrefreshable credential is precisely such a cheaper denial. Placing
the credential stage *after* the reservation would reserve budget, discover the token
is dead, and then have to unwind the hold — the exact waste `:203-207` was written to
prevent.

**Why not earlier** — every gate above it (registry, token pipeline, call limit,
convergence breaker, schema validation) is local and cheap. The credential stage may
perform a network round trip, so it belongs below all of them, consistent with the
cheapest-denial-first ordering the method already follows.

### 3.3 It fits the `ToolSpendProfile` pattern exactly

`[CODE-VERIFIED]` The spend feature's shape is the template, and it is a good one:

- Opt-in declaration on the definition: `spend: ToolSpendProfile | None = None`
  (`src/skylize/tools/base.py:72-74`), defaulted to `None` "so every tool registered
  before this field existed is unaffected."
- Guarded activation: `if tool.spend is not None:` (`proxy.py:209`).
- Fail-closed when infrastructure is absent (`proxy.py:283-287`).
- Every denial audited before it is raised (`_reserve_spend` docstring, `:267-269`).

`[DESIGN]` The OAuth stage mirrors all four: a new `oauth: ToolOAuthProfile | None = None`
field on `ToolDefinition` (alongside `spend` at `base.py:74`), an `if tool.oauth is not
None:` guard at the new `:202`, fail-closed when no OAuth service is wired, and an
audited denial on every failure path.

**Answering the question as posed:** it is a *new stage*, but not a new *pattern* — it
is the `ToolSpendProfile` pattern applied to a second resource. No existing stage is
modified and no existing tool changes behaviour.

### 3.4 The resolved token does **not** flow through `ToolContext`

`[CODE-VERIFIED]` `ToolContext` carries `org_id`, `agent_id`, `correlation_id` and
nothing else (`base.py:25-32`). `[DESIGN]` **Leave it that way.** The new stage's job is
to *guarantee freshness*, not to deliver the secret: it ensures a valid, non-expired
grant exists (refreshing if needed) or denies. The connector then resolves the token
itself per call, exactly as HubSpot does today (`hubspot_tools.py:3-6, 129-137`).

Two reasons: it keeps the plaintext token off a context object that is passed to every
handler and is trivially logged; and it preserves the "resolved per call, never cached"
property that makes rotation take effect immediately. **Trade-off, stated honestly:**
this costs a second read of the grant row within the same request. That is a cheap
in-transaction read, and the alternative widens secret exposure.

---

## 4. Revocation and failure handling

### 4.1 The failure that matters

A customer revokes access in Google's console. The refresh token is now dead. The next
refresh returns HTTP 400 `{"error": "invalid_grant"}`. **This must not surface as an
unhandled exception or a silent no-op.**

`[DESIGN]` On `invalid_grant`:

1. Stamp `revoked_at` / `revoked_reason` on the grant row — the state gap logged at
   `integration_inputs.md:465-467` ("No revocation state … only a row delete exists").
   Never delete the row: deletion loses the evidence that a connection existed.
2. Deny the tool call with a typed error, modelled on `ToolSpendUnavailable`
   (`proxy.py:283-287`) — a distinct type so callers can distinguish "reconnect
   required" from "transient network failure."
3. Audit the denial before raising, per `_reserve_spend`'s discipline (`:267-269`).
4. Degrade to a clean message for the agent, following the HubSpot "not connected"
   precedent (`hubspot_tools.py:129-137`) — never a 500.
5. Notify a human out of band (§4.2).

### 4.2 The HITL queue is the **wrong** vehicle for re-auth — and that is a finding

The task proposes "HITL notification to re-auth?". `[CODE-VERIFIED]` **The existing HITL
queue cannot carry this**, for a structural reason:

The HITL queue is built to **replay a frozen agent execution**. Each row carries a
`request_json` replay envelope written once at enqueue and never rewritten
(`src/skylize/app/hitl/service.py:23`, `dal/hitl.py:121`), and approval validates and
replays it (`HitlReplayEnvelope.model_validate(row.request_json)`, `service.py:173`).
The semantics are "a human approves this pending action, and it then runs."

A re-auth prompt is not that. **No human approval can restore a revoked Google grant** —
someone must complete an out-of-band OAuth consent flow in a browser. An approve button
would have nothing to replay, and the original tool call's inputs may be stale by the
time consent is granted. Forcing re-auth into `hitl_queue` would put a row in a queue
whose approve path cannot succeed.

`[CODE-VERIFIED]` Corroborating: `HumanInLoopTrigger` (`src/skylize/contracts/base.py:60-68`)
has six members — `SPEND_OVER_CEILING`, `FIRST_EXTERNAL_LAUNCH`, `BRAND_LEGAL_SENSITIVE`,
`AUTHORITY_EXCEEDED`, `SECURITY_SEVERITY_HIGH`, `LOW_CONFIDENCE_IRREVERSIBLE` — and
**none denotes a broken connection.** Adding one is an owner decision; **this pass adds
nothing.**

`[DESIGN]` The right shape is a **connection-state surface**, not an approval queue: the
`revoked_at` column from §1.3 is the durable state, surfaced to org admins through the
existing credential routes (`src/skylize/edge/routes/credentials.py`), plus a
notification. The notification pattern already exists and fits exactly —
`SlackApprovalNotifier` is post-only and **best-effort by design**, logging failures
rather than raising so an outage "must never fail the request that produced the 202"
(`src/skylize/app/notifications/slack.py:14-19`). A re-auth notice has the same
posture: the durable truth is the DB column; the message is a convenience.

**Q4.2a `[OWNER-DECISION-REQUIRED]`** Does a revoked connection warrant a new
`HumanInLoopTrigger` member and a `hitl_queue` row despite the replay mismatch above,
or a separate connection-state surface as recommended? This is the single largest open
design question in this document.

### 4.3 Refresh failures that are *not* revocation

`[DESIGN]` Distinguish, because conflating them causes a transient network blip to look
like a revoked account:

| Failure | Meaning | Response |
|---|---|---|
| `invalid_grant` | grant is dead | §4.1 — mark revoked, deny, notify |
| 429 / 5xx | provider transient | bounded retry, then deny **without** marking revoked |
| network timeout | unknown | deny **without** marking revoked; never assume revocation |
| `invalid_client` | Skylize's own client credentials wrong | platform misconfiguration — deny, alert operators, **not** the customer |

The asymmetry is the point: **marking a grant revoked is destructive to the customer's
experience** (it demands a reconnect), so only an unambiguous provider signal may do it.

---

## 5. Interface contract — the provider abstraction boundary

`[DESIGN]` The whole purpose of this pass. A connector supplies **configuration and
provider-specific parsing**; the infrastructure owns **storage, encryption, freshness,
concurrency, revocation state, and audit**.

**What a provider supplies** (sketch, not code):

```
OAuthProviderConfig
  provider_id            'google_drive' | 'notion' | 'asana'
  authorize_url          provider's consent endpoint
  token_url              exchange + refresh endpoint
  client_id / secret     WHERE they live, not the values — see below
  default_scopes         narrowest set derivable from planned actions
  parse_token_response   provider JSON -> (access, refresh, expires_at, scopes, account_id)
  account_id_of          how to name the connected account (gap 6)
  revocation_signal      how this provider says "grant is dead"
```

`parse_token_response` and `revocation_signal` exist because providers genuinely differ:
some return `expires_in` seconds, some an absolute time, some no refresh token at all
(hence `refresh_token_encrypted` is NULLable in §1.3); revocation is signalled as
`invalid_grant` by some and as a 401 with a distinct body by others. **These are the
only two places provider variation is allowed to leak in.**

**What the infrastructure owns:** the authorize/callback endpoints and `state`/PKCE
handling; the token exchange; encrypted persistence to `oauth_grants`; the freshness
check and refresh (§2.3) with row-level concurrency control; revocation state (§4);
the `ToolProxy` pre-dispatch stage (§3); and audit records on every mutation, mirroring
`CredentialVault`'s discipline (`vault.py:53-59, 93-99, 114-120`).

**Client ID / secret are platform-level, not per-tenant** `[DESIGN]`. Skylize registers
one OAuth application per provider; customers authorize *into* it. The established
pattern for a platform secret is a `SKYLIZE_*` env var on `Settings` resolved
fail-closed at boot — the reasoning is set out at `integration_inputs.md:343-355` and
demonstrated by `resolve_credential_encryption_key` (`bootstrap.py:82-129`). `[CODE-VERIFIED]`
there is no secrets-manager service in the tree; every platform secret today is a
`Settings` field (`config.py:128-160`).

**Explicitly out of scope for the provider:** encryption, expiry policy, retry policy,
audit, RLS. A connector that touches those has crossed the boundary — which is the
whole point of building this once rather than per-provider.

---

## 6. Migration impact — new table, not an alter

`[DESIGN]` **Add `oauth_grants` alongside `org_credentials`. Do not alter
`org_credentials`.** Four reasons, in descending strength:

1. **The single-opaque-string contract is load-bearing and pinned by tests.**
   `[CODE-VERIFIED]` `CredentialVault.store`/`rotate` take one `raw_value: str`
   (`vault.py:35, 79`) and `retrieve` returns one `str` (`:63-73`); the HubSpot
   connector consumes exactly that (`hubspot_tools.py:129-137`); and
   `tests/integration/test_jsonb_readback_pg.py:229-245` pins the `metadata_json`
   JSON-encoded-`str` row contract explicitly ("the row contract stays a JSON-encoded
   str", `:230-231`). Widening the table forces every existing consumer to reckon with
   columns meaningless to it.
2. **Different lifecycles.** An API key is written once and manually rotated. An OAuth
   grant is refreshed automatically, carries expiry, and can be revoked upstream. Same
   argument the codebase already makes for keeping the three ledgers distinct
   (ADR-0006).
3. **NOT NULL columns cannot be added to a populated table** without defaults that are
   meaningless for API-key rows — what is `access_token_expires_at` for a HubSpot key?
   Making them all nullable surrenders exactly the integrity structured columns were
   chosen for in §1.2.
4. **`integration_inputs.md` already research-suggests this** (Q3.0a, `:473-479`),
   citing reasons 1 and 2. This design agrees with the standing recommendation rather
   than contradicting it — but note Q3.0a is **still formally unanswered**, so §6 is a
   proposal awaiting that decision, not a fait accompli.

**Consequential requirements on the (unwritten) migration** `[DESIGN]`:

- RLS `ENABLE` + `FORCE` + `tenant_isolation` policy mirroring `0007:59-68`.
- `GRANT SELECT, INSERT, UPDATE, DELETE` to `skylize_app`, mirroring `0007:70-72`.
- **Deliberately NOT added to migration 0002's cross-tenant read carve-out list**
  (`0002:33-36`). Under the §2.3 recommendation nothing needs a cross-tenant read, and
  a carve-out granted "just in case" on the table holding every customer's document-store
  credentials is the opposite of fail-closed.
- `CredentialVault` is untouched; the new table gets its own repository following
  `PgCredentialRepository`'s discipline — `tenant_session(org_id)` **plus** a redundant
  explicit `org_id` predicate so RLS and the query agree (`dal/credentials.py:80-123`).

**Per the hard exit gate, the migration is not written.**

---

## 7. Stateless-agent invariant — confirmed untouched

`[CODE-VERIFIED]` All five named agents are stateless, with empty memory access:

| Agent | Contract | `memory_read_access` / `memory_write_access` |
|---|---|---|
| `chief_security_officer` | `src/skylize/contracts/mvp/safety.py:14` | `[]` / `[]` (`:28-29`) |
| `director_ai_safety` | `safety.py:37` | `[]` / `[]` (`:50-51`) |
| `llm_safety_agent` | `safety.py:59` | `[]` / `[]` (`:72-73`) |
| `prompt_injection_agent` | `safety.py:80` | `[]` / `[]` (`:93-94`) |
| `cfo_agent` | `src/skylize/contracts/mvp/finance.py:162` | `[]` / `[]` (`:180-181`) |

**This design does not touch any of them, for three structural reasons:**

1. **It adds no memory read or write of any kind.** Every surface proposed here is
   credential storage (`oauth_grants`), the `ToolProxy` pre-dispatch stage, and OAuth
   endpoints. Memory access is gated in a different component entirely —
   `MemoryGateway`, on the contract's `memory_*_access` lists, where an empty list means
   stateless means denied (`src/skylize/memory/gateway.py:6`).
2. **None of the five can invoke an OAuth-capable tool.** The proposed stage is guarded
   by `if tool.oauth is not None:` and fires only for tools declaring an OAuth profile.
   `cfo_agent`'s entire `invocable_tools` list is `["utility.current_datetime"]`
   (`finance.py:175`), and its comment states the rule directly: "Still stateless … only
   `utility.current_datetime` is invocable, never `memory.search`, per the CFO/Safety
   statelessness rule" (`finance.py:159-161`).
3. **The mechanism is opt-in and additive.** Exactly like `spend`, the new field
   defaults to `None` (`base.py:72-74` precedent), so no existing contract or tool
   changes behaviour.

⚠️ **Naming precision for future sessions** `[CODE-VERIFIED]`: there are **two distinct
CFO contracts**, and only one is stateless. `cfo_agent` (`finance.py:162`) is stateless.
`cfo` (`finance.py:15`) is **not** — it holds `memory_read_access=["finance:*",
"org:decisions", "strategy:*"]` and `memory_write_access=["finance:decisions"]`
(`:30-31`). The task named `cfo_agent`, which is correct. A future session reasoning
about "the CFO agent" must not conflate them.

---

## 8. Open questions requiring owner decision

| # | Question |
|---|---|
| **Q8.1** | **Is Drive org-level or platform-level?** Still unanswered from `audit_gdrive_readiness.md` §D.2, and it remains upstream of everything here — this entire design applies **only** if org-level. A platform-level Drive looks like Slack: a `Settings` env var and no table at all. |
| **Q8.2** | Q3.0a (`integration_inputs.md:473-479`) — separate table vs extend. §6 proposes separate; the decision is still formally open. |
| **Q8.3** | Q4.2a — re-auth as a new `HumanInLoopTrigger` + `hitl_queue` row, or a distinct connection-state surface? §4.2 recommends the latter and explains why the queue's replay semantics do not fit. |
| **Q8.4** | Does a long-lived refresh token to a customer's document store change the encryption posture? One platform-wide Fernet key, no KMS, no key-ref column, no rotation (`integration_inputs.md:468-471`; `bootstrap.py:322-323`). The §1.3 sketch has **no** `key_id` column — adding one later is a rewrite of every row, so this is cheapest to decide **now**. |
| **Q8.5** | Refresh retry policy (§2.3) — reuse the HubSpot 429/5xx exponential shape, or something stricter given a live request is blocked? |
| **Q8.6** | Does §4.0's precondition #1 (`integration_inputs.md:478-479`, "Section 1.1 resolved and implemented") gate a non-spend connector? Carried forward unresolved from `audit_gdrive_readiness.md` §D.8. |
| **Q8.7** | Should the latent `sweep_expired` RLS defect (§2.2, `[INFERRED]`) be confirmed and tracked as its own defect? It is pre-existing, unrelated to OAuth, and untouched here. |

---

## 9. Verification log

All searches at commit `0e34060`, scoped away from vendored `.claude/skills/`.

| Check | Result |
|---|---|
| `oauth` in `src migrations tests policy scripts infra` | 1 hit, prose only (`0007:8`). **`src/` still zero.** Unchanged from `0e34060` |
| Migration chain | 0001–0020; **no migration alters `org_credentials` after 0007** (only hit is a comment, `0019:233`) |
| `CredentialVault` API | Unchanged: `store`/`retrieve`/`rotate`/`delete`/`delete_by_id`/`list_providers` (`vault.py:31-144`) |
| `@workflow.defn` repo-wide | **Zero** |
| Temporal in `infra/` | **Zero** |
| GIN index on a JSONB column | **Zero** (only GIN is a tsvector expression, `0001:255`) |
| `sweep_expired` callers in `src/` | **Zero** |

**Not verified — declared, not assumed:** no tests were run; no migration was applied;
no live Google/Notion/Asana documentation was fetched, so **no provider endpoint URL,
scope string, or error-code claim is asserted as verified** — §4.3's `invalid_grant`
and §5's field names are standard OAuth 2.0 vocabulary that must be confirmed against
each provider's current docs before implementation, exactly as `integration_inputs.md:356-368`
requires for Slack's `chat:write`. §2.2's `sweep_expired` finding is `[INFERRED]` from
policy text and role configuration, **not executed**.

## 10. Changes made

**None.** Design document only. No code, no migration file, no `CredentialVault`
change, no schema change, no Drive-specific logic, no `integration_inputs.md` edit.
The latent defect in §2.2 and the naming hazard in §7 are **reported, not fixed** —
both are outside a design-only pass.
