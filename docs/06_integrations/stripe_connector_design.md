# Skylize - Stripe Connector Design (Standard + OAuth)

> **Status: DRAFT - AWAITING OWNER APPROVAL (Mr. Ozkan)**
> **Date compiled:** 2026-08-28
> **Resolves:** Q2.1e (account model - FINAL), Q2.1f (pre-existing account requirement).
> **Supersedes:** the Accounts v2 draft of the same filename, 2026-08-28. That draft is
> withdrawn in full, not appended to. Its reasoning is preserved only where 1.0.2 records
> why the v2 path was rejected.
> **Depends on:** `integration_inputs.md` 1.1 (spend ceiling), 2.1 (Stripe), 3.0 (grant storage).
> **Design only. No code, no migration, in this pass.**
>
> **REVISED 2026-09-09 - owner decisions R1 and R2. Two sections are corrected IN PLACE;
> the rest of the 2026-08-28 draft stands and is not re-litigated.**
> - **R1 - fifth profile.** Stripe is NOT forced through `OAuthCredentialService`. A new
>   `ToolStripeProfile` gates it, mirroring the `ToolWifProfile` precedent
>   (`src/skylize/tools/base.py:137-176`): a trust relationship, not a stored or refreshed
>   grant. `OAuthCredentialService`, the `oauth_credentials` table, and
>   `ensure_fresh` / `evaluate_grant` are **untouched** by this design. **New section 4.5.**
> - **R2 - HITL-replay-safe idempotency.** 7.0 item 3 predated `ToolContext.hitl_id`
>   (`src/skylize/tools/base.py:72`), which now exists and is threaded through
>   `ToolProxy.invoke` to the handler (`src/skylize/tools/proxy.py:160-167,326-329`).
>   **7.0 item 3 and the derivation that follows it are rewritten.**
> - **New section 7.5** specifies the per-org refund limit tables and the interim
>   large-refund review trigger. Its numbers stay `[OWNER-DECISION-REQUIRED]`.
>
> **How to read this file.** Every concrete claim is marked with one of:
> - `[CODE-VERIFIED]` - extracted from the current codebase. Ground truth. Cites `file:line`.
> - `[STRIPE-DOC-VERIFIED]` - read from Stripe's live documentation on 2026-08-28. Cites a URL.
> - `[RESEARCH-SUGGESTED]` - a defensible default. **NOT yet approved.**
> - `[OWNER-DECISION-REQUIRED]` - a design choice only the owner can make.
> - `[INTERIM-RULE]` - a deliberate placeholder standing in for a capability that does
>   not exist yet. **Never to be read as the real thing.** Used once, in 7.5.4.
>
> Nothing here is `[APPROVED]`. This document does not by itself unblock connector code;
> `integration_inputs.md` 4.0 preconditions still govern.

---

## 1.0 - Decision: Standard accounts via OAuth (final)

> **Section status: owner-decided 2026-08-28. Rationale recorded, not re-litigated.**

### 1.0.1 The decision

Skylize connects **Standard connected accounts through the Connect OAuth flow**, on the
Accounts v1 API. The connected account is the merchant of record and bears dispute and fraud
liability for direct charges; Stripe, not Skylize, carries loss liability and KYC.

### 1.0.2 The deciding constraint

Q2.1f asked whether Skylize must connect a customer's **pre-existing** Stripe account. The owner
answered yes. That single answer forecloses the Accounts v2 path:

`[STRIPE-DOC-VERIFIED]` **OAuth requires Accounts v1.** From "Accounts API v2 limitations"
(https://docs.stripe.com/connect/accounts-v2): "You must use Accounts v1 in the following
cases: Using OAuth to authenticate connected accounts."

`[STRIPE-DOC-VERIFIED]` **Only OAuth links an account the customer already has.** From
https://docs.stripe.com/connect/oauth-standard-accounts: "The process of creating a Stripe
account is incorporated into our authorization flow. You don't need to worry about whether or
not your users already have accounts. The user is logged in and can choose an account to
connect to your platform directly." The redirect step is described as the user connecting
"their existing or newly created account."

So: existing-account linking requires OAuth; OAuth requires v1; v1 Standard is the account type
OAuth serves. The chain is forced. **Q2.1f is resolved and moot** - the capability it asked
about is intrinsic to the chosen flow rather than an open question.

`[RESEARCH-SUGGESTED]` The cost accepted with this decision, recorded so it is not rediscovered
later as a surprise: Stripe states "OAuth isn't recommended for new Connect platforms," and the
legacy Standard/Express/Custom taxonomy carries a deprecation banner. This is a deliberate
trade of a deprecated-but-supported flow for a product requirement that the recommended flow
cannot satisfy. It should be revisited if Stripe ever brings existing-account linking to v2.

### 1.0.3 Operational restriction that follows from OAuth

`[STRIPE-DOC-VERIFIED]` "Starting in June 2021, Platforms using OAuth with `read_write` scope
won't be able to connect to Standard accounts that are controlled by another platform."

**This is a real business constraint, not a footnote.** A prospective customer whose Stripe
account is already controlled by another platform cannot connect to Skylize at all. This repo
already contemplates Shopify (`docs/06_integrations/shopify.md`), and a merchant on Shopify
Payments is a plausible instance of exactly this case. The connector must surface this as a
clean, explained refusal at connect time rather than an opaque OAuth error.

**Q2.1h `[OWNER-DECISION-REQUIRED]`** What is the product answer for a customer whose Stripe
account is platform-controlled? Options include refusing the connection with an explanation, or
directing them to a separate un-controlled account. No default is safe to assume.

---

## 2.0 - HARD CONSTRAINT: direct charges only

> **Section status: `[RESEARCH-SUGGESTED]` - constraint, not preference.**

`[STRIPE-DOC-VERIFIED]` (https://docs.stripe.com/connect/integration-recommendations) The
account type does not by itself place dispute liability. The **charge type** does:

- **Direct charges** - the customer transacts with the connected account, which is the merchant
  of record and bears dispute and fraud liability.
- **Destination / separate charges and transfers** - the customer transacts with the *platform*.
  "Stripe always applies negative transactions, such as refunds and disputes, to the account
  where the associated charge was made," so refunds and disputes reduce **Skylize's** balance.
  Stripe further notes that in this model the platform "can't easily recover those funds from
  connected accounts."

`[STRIPE-DOC-VERIFIED]` The legacy account-type matrix is consistent: Standard's supported
charge type is **direct only**.

**Therefore: Skylize uses direct charges. Destination and separate charges are forbidden on the
Stripe connector.** Using them would silently transfer dispute and fraud liability from the
connected account to Skylize while every other part of this design still asserted the opposite.
That is a liability inversion with no error message attached to it - the worst failure shape
available here.

`[RESEARCH-SUGGESTED]` **Enforcement point.** This must be a checked invariant, not a
convention:

1. Any Stripe tool that creates a charge is registered only in a direct-charge form, and the
   tool's input schema must not expose `on_behalf_of`, `transfer_data`, or
   `application_fee_amount` in the destination-charge sense - fields whose presence converts a
   direct charge into another kind.
2. Every outbound call carries the `Stripe-Account` header (3.0.3). A charge created *without*
   that header is a platform charge by definition, so an adapter-level assertion that the header
   is present on every call is the same check expressed once, centrally.
3. Refunds inherit the charge they target, so a refund of a direct charge reduces the connected
   account's balance - the intended behaviour, and the reason the spend ceiling in 6.0 is
   measuring the right pool of money.

---

## 3.0 - The OAuth flow

> **Section status: `[RESEARCH-SUGGESTED]`**

### 3.0.1 Authorize

`[STRIPE-DOC-VERIFIED]` The platform sends the user to:

```
https://connect.stripe.com/oauth/authorize
  ?response_type=code
  &client_id=ca_...
  &scope=read_write
  &state=<single-use CSRF token>
  &redirect_uri=<one of the pre-registered URIs>
```

- `scope=read_write` is required. `read_only` is the default and, since the June 2021 change,
  can only be specified for extensions.
- `state` is the CSRF defence and Stripe returns it unmodified: "Your site should confirm the
  `state` parameter hasn't been modified."
- `redirect_uri`, if sent, "must exactly match one of the comma-separated `redirect_uri` values
  in your application settings," and in live mode "must use a secure HTTPS connection."

`[RESEARCH-SUGGESTED]` **There is no PKCE in Stripe's Connect OAuth,** and none is needed: this
is a confidential-client flow where the platform authenticates the token exchange with its
secret key. `integration_inputs.md:74-76` lists "no state/PKCE handling" among the missing
broker pieces; for Stripe, only `state` is applicable. Do not build a PKCE path for this
provider and then assume other providers match.

`[RESEARCH-SUGGESTED]` `state` must be single-use, expiring, bound to the initiating org and
user, and stored server-side - not a signed cookie alone. It is the only thing standing between
an attacker and grafting their own Stripe account onto a victim's Skylize org, which would route
that org's agent-issued refunds to an account the attacker controls. Treat it as a security
control with its own tests, not as a passthrough parameter.

### 3.0.2 Callback and token exchange

`[STRIPE-DOC-VERIFIED]` Stripe redirects back with `scope`, `state`, and a `code`. The platform
then exchanges it, authenticating with its own secret key:

```
POST https://connect.stripe.com/oauth/token
  -u <platform secret key>:
  -d code=ac_...
  -d grant_type=authorization_code
```

`[STRIPE-DOC-VERIFIED]` Two hazards on this single call, both of which must shape the code:

1. **The authorization code is single-use and expires in 5 minutes**, and - from the OAuth
   reference (https://docs.stripe.com/connect/oauth-reference) - "Per OAuth v2, this endpoint
   isn't idempotent. **Consuming an authorization code more than once revokes the account
   connection.**"
2. Therefore **the token exchange must never be retried.** This is the opposite of the retry
   posture the HubSpot precedent takes (`[CODE-VERIFIED]`
   `src/skylize/tools/builtin/hubspot_tools.py:95-100,111-116` retries on 429/5xx). A generic
   retry decorator applied here does not merely fail - it *destroys the connection it is trying
   to establish*. `[RESEARCH-SUGGESTED]` the exchange runs with retries explicitly disabled, and
   a timeout is treated as indeterminate: surface it to the user as "reconnect", never re-POST.

### 3.0.3 Authenticating as the connected account

`[STRIPE-DOC-VERIFIED]` (https://docs.stripe.com/connect/authentication) Server-side calls use
**the platform's own secret key plus a `Stripe-Account: acct_...` header**:

```
curl https://api.stripe.com/v1/refunds \
  -u "<platform secret key>:" \
  -H "Stripe-Account: acct_..."
```

Every example on that page uses the platform key with the header. No OAuth access token appears
anywhere in the modern authentication path.

---

## 4.0 - What gets stored: no bearer token

> **Section status: `[RESEARCH-SUGGESTED]` - schema proposal only. No migration this pass.**
> **Revised 2026-09-09 (R1):** this section says what is STORED. What CHECKS it before a
> tool dispatches is `ToolStripeProfile`, specified in **4.5** - not
> `OAuthCredentialService`, which this design does not use at all.

### 4.0.1 The deprecated fields are discarded. This is deliberate.

`[STRIPE-DOC-VERIFIED]` The token response can carry `access_token`, `refresh_token`, and
`stripe_publishable_key`, and the OAuth reference marks **all three (Deprecated)**, directing
integrators to "the `Stripe-Account` header with your platform's secret key." Stripe's own
worked example on the Standard OAuth page shows a response containing only `token_type`,
`scope`, `livemode`, and `stripe_user_id`, with the instruction: "**Store the `stripe_user_id`
in your database.**"

**Confirmed: nothing in Skylize's use of Stripe requires either token.** Checked against every
operation this connector performs:

| Operation | What it needs | Needs a token? |
|---|---|---|
| Refunds, charges, reads | platform secret key + `Stripe-Account` header (3.0.3) | No |
| Webhook verification | platform signing secret (6.0) | No |
| **Disconnect an account** | `client_id` + `stripe_user_id`, authenticated with the platform secret key | **No** |

`[STRIPE-DOC-VERIFIED]` The disconnect case was the one worth verifying explicitly, and it is
clean - the deauthorize call takes only `client_id` and `stripe_user_id`:

```
POST https://connect.stripe.com/oauth/deauthorize
  -u <platform secret key>:
  -d client_id=ca_...
  -d stripe_user_id=acct_...
```

**One documented tension, recorded honestly.** The Standard OAuth page says of the refresh
token: "You should hold on to this value, too, as you're only able to get it after this initial
POST request." Its two stated uses are generating *test* access tokens for a production
`client_id`, and rolling an access token. **Both presuppose using access tokens at all**, which
this design does not. The page's advice conflicts with the OAuth reference's own deprecation
marking; we follow the deprecation marking.

`[RESEARCH-SUGGESTED]` **Decision: discard `access_token` and `refresh_token` at the callback.
Persist only `stripe_user_id`, `scope`, and `livemode`.** The reasoning:

- Storing a bearer token we never use is pure liability. It would drag the whole
  encrypted-secret-at-rest problem back onto Stripe's critical path - `integration_inputs.md`
  3.0 gaps 2 and 5, i.e. `metadata_json` is plaintext JSONB
  (`[CODE-VERIFIED]` `migrations/versions/0007_org_credentials.py:43`) and there is a single
  platform-wide Fernet key with no rotation and no key-id column
  (`[CODE-VERIFIED]` `src/skylize/bootstrap.py:322-323`, `src/skylize/config.py:80`).
  Not storing the token means those gaps stay off Stripe's path entirely.
- **The cost is real and one-way:** the refresh token is obtainable *only* at that initial POST.
  Discarding it cannot be undone without the customer reconnecting. Accepted, because reconnect
  is a supported flow and the alternative is holding a credential we have no use for.

**Q2.1i `[OWNER-DECISION-REQUIRED]`** Confirm the discard. If the owner would rather retain the
refresh token against an unforeseen need, that is a different design: it reintroduces a
per-tenant bearer secret, and `org_credentials` plus the 3.0 gaps come back into scope.

### 4.0.2 Proposed table: `org_stripe_accounts`

`[RESEARCH-SUGGESTED]` Deliberately lightweight. Not `org_oauth_grants` - it stores no grant.
Modelled on migration 0007's RLS pattern.

```sql
CREATE TABLE org_stripe_accounts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            TEXT NOT NULL REFERENCES tenants(org_id),
    stripe_account_id TEXT NOT NULL,   -- acct_...; identifier, NOT a bearer secret
    livemode          BOOLEAN NOT NULL,
    scope             TEXT NOT NULL,   -- as GRANTED by Stripe, not as requested
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deauthorized_at   TIMESTAMPTZ
);
```

Every column earns its place; nothing beyond the owner's four is added without a reason:

- `livemode` - required. Q2.1a becomes a first-class boolean rather than the free-text `label`
  convention proposed at `integration_inputs.md:219-222`, and the webhook path needs it (6.0.2
  step 6) because production endpoints receive both live and test events. Resolution rule: a run
  resolves the row matching the running environment's mode and **denies if absent**. No fallback
  between modes, in either direction.
- `scope` - required by the platform's own attenuation invariant
  (`integration_inputs.md:31-35`): the effective permission is an intersection that includes
  "the provider grant actually held." Storing what Stripe *granted*, rather than what we
  requested, is the only way that intersection can be evaluated. This closes gap 3 of 3.0.
- `deauthorized_at` - closes gap 4 (revocation state). Rows are **never hard-deleted**: deleting
  the row destroys the audit trail of an account that once held authority. Contrast
  `CredentialVault`, where revocation is only a row delete
  (`[CODE-VERIFIED]` `src/skylize/app/credentials/vault.py:102-141`).

Deliberately **absent**: `charges_enabled`, `payouts_enabled`, capability mirrors. Mirroring live
Stripe account state invites drift between our copy and Stripe's truth, and this table's job is
identity and authority, not account status. Read that state from Stripe when a decision needs it.

Indexes and policy:

- unique on `(stripe_account_id)` - `acct_` ids are globally unique at Stripe across live and
  test, so this is a safe natural key and it also prevents one Stripe account being claimed by
  two orgs.
- partial unique on `(org_id, livemode) WHERE deauthorized_at IS NULL` - at most one live and one
  test connection per org, a constraint the `label` proposal could not express.
- RLS `ENABLE` + `FORCE`, `tenant_isolation` on `current_setting('skylize.org_id')` for both
  `USING` and `WITH CHECK`, and `skylize_app` grants - the same shape as
  `[CODE-VERIFIED]` `migrations/versions/0007_org_credentials.py:59-72`.

### 4.0.3 `acct_` is sensitive, though not a secret

`[RESEARCH-SUGGESTED]` The account id is an identifier, not a bearer credential - possessing it
grants nothing without the platform secret key. But it is not public either, and it is a direct
tenant-identifying handle. So:

- It lives in an RLS-protected table, not a config file or a platform-level directory.
- It is **not written in plaintext to audit records, logs, or events.** Where an audit trail must
  reference the account, it references the `org_stripe_accounts.id` surrogate, or a truncated
  form. This is weaker than the vault's rule for secrets - `retrieve` "never appears in logs"
  (`[CODE-VERIFIED]` `src/skylize/app/credentials/vault.py:69`) - and deliberately so: the
  requirement here is minimising exposure, not preventing it absolutely.

**Blast radius, stated plainly.** Because authentication is platform-key-plus-header rather than
per-tenant tokens, compromising the platform secret key exposes **every connected account at
once**. Under a per-tenant-token design it would expose one tenant. This is the correct trade
only if the platform key is held to a higher standard than a vault row would be. Flagged for
`chief_security_officer` review, which `docs/06_integrations/stripe.md:52` already requires.

---

## 4.5 - `ToolStripeProfile` - the fifth declarative gate

> **Section status: `[RESEARCH-SUGGESTED]` - schema and gate proposal only. No code, no
> migration, this pass.**
> **Owner decision R1 (2026-09-09):** Stripe gets its OWN profile. It does **not** go
> through `OAuthCredentialService`, `oauth_credentials`, `ensure_fresh`, or
> `evaluate_grant`. Those are untouched by this design. This section supersedes any
> reading of 4.0 that implied otherwise.

### 4.5.1 Why a fifth profile, and not `ToolOAuthProfile`

`[CODE-VERIFIED]` `ToolDefinition` carries four nullable, opt-in gate profiles today
(`src/skylize/tools/base.py:211,215,221,224`): `spend` (`base.py:78-99`), `oauth`
(`base.py:179-196`), `permission` (`base.py:102-134`), `wif` (`base.py:137-176`). Each is
checked in its own stage of `ToolProxy.invoke`, and each fails closed when its backing
dependency is unwired (`src/skylize/tools/proxy.py:122-141`).

`ToolOAuthProfile` gates a tool on a **live stored grant**. The proxy's OAuth stage calls
`_ensure_oauth_credential` (`[CODE-VERIFIED]` `proxy.py:266-270`, defined at `:386`), whose
docstring states its job is to establish that a usable grant EXISTS, "refreshing on demand if
needed" (`proxy.py:395-400`). Every part of that is inapplicable to Stripe:

`[CODE-VERIFIED]` + 4.0.1 above - **Skylize stores no Stripe bearer token.** There is nothing
to hold, nothing to refresh, and no expiry to evaluate. Server-side authentication is the
platform's own secret key plus a `Stripe-Account: acct_...` header (3.0.3). What must be
established before dispatch is a **trust relationship** - is this org still connected, in
this mode, with this scope - not a credential's freshness.

`ToolWifProfile` records exactly this argument for exactly this reason: "a federation trust
is not a stored grant. There is no token to hold, nothing to refresh, and no expiry"
(`[CODE-VERIFIED]` `base.py:138-146`), and `gcp_wif_connections` is a separate table from
`oauth_credentials` on the same grounds (`base.py:142-144`, migration 0024). **Stripe is the
second instance of that shape**, and `org_stripe_accounts` (4.0.2) is the third such table.
The precedent is followed.

`[RESEARCH-SUGGESTED]` **Rejected alternative: resolve the connection inside the handler.**
Rejected for the reason `docs/06_integrations/gcp_wif_killswitch_design.md:1083-1088` already
rejected it for WIF: it puts the check somewhere that is neither inspectable in a registry
entry nor deny-by-default, and all four existing profiles "refuse callable predicates on
purpose, so a gate stays inspectable" - the phrasing `ToolWifProfile` itself uses at `base.py:158`, over
`ToolSpendProfile.:91` ("Deliberately NOT a callable estimator") and
`ToolPermissionProfile.:120` ("Deliberately NOT a callable predicate")
(`[CODE-VERIFIED]` `base.py:91,120,158`). A gate
that lives only inside a function body is one refactor from being skipped.

### 4.5.2 `ToolWifProfile` is NOT extended or generalized - and why that is not a defect

`[CODE-VERIFIED]` Checked explicitly, because forcing a shared abstraction here would be the
cheap mistake. `ToolWifProfile` needs **no change** to accommodate Stripe: nothing in this
design touches it. The two profiles share a *rationale* - a trust, not a stored grant - and
share **no fields**:

| | `ToolWifProfile` (`base.py:167-176`) | `ToolStripeProfile` (proposed, 4.5.3) |
|---|---|---|
| Selector | `label` - which connection, when an org has several | none: mode is environment-derived (4.5.4), and 4.0.2's partial unique index permits at most one live and one test row per org |
| Per-resource allow-list | `project_field` / `zone_field` / `instance_field`, checked against enabled `gcp_wif_targets` rows (`proxy.py:706-720`) | **none exists.** Stripe has no per-resource allow-list table, and inventing one would be a second allow-list for a question `org_stripe_accounts.scope` already answers |
| Third check | the target is an enabled row | the direct-charge invariant of 2.0, which has no WIF analogue |

The field sets are disjoint, so a shared base class would carry nothing but `model_config`.
**No incompatibility surfaced, and no shared abstraction is proposed.** Recorded here so a
later session does not re-open this as unnoticed duplication.

### 4.5.3 Proposed profile

`[RESEARCH-SUGGESTED]` Declared alongside the other four in `src/skylize/tools/base.py`, and
added as a fifth nullable field on `ToolDefinition` (`base.py:211-224`) defaulting to `None`,
so every tool registered before it existed is unaffected - the additive discipline `wif` used
(`base.py:221-224`).

```python
class ToolStripeProfile(BaseModel):
    """Declares a tool DEPENDENT ON A LIVE STRIPE CONNECT TRUST.

    The fifth opt-in gate on `ToolProxy.invoke`. A separate profile from
    `ToolOAuthProfile` for the same reason `ToolWifProfile` is one: a Connect
    trust is not a stored grant. No token is held, nothing is refreshed, and
    there is no expiry - every call authenticates with the PLATFORM secret key
    plus a `Stripe-Account` header naming the connected account.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The Stripe OAuth scope this tool REQUIRES, checked against the scope
    #: Stripe actually GRANTED (org_stripe_accounts.scope), never against the
    #: scope we requested. 'read_write' for any mutating verb.
    required_scope: str = Field(min_length=1)
    #: True for any verb that creates or modifies a charge, refund, or transfer.
    #: Turns on the direct-charge invariant check of 2.0 at the gate.
    mutates_money: bool = True
```

Deliberately **absent**:

- **No `label`.** 4.0.2's partial unique index `(org_id, livemode) WHERE deauthorized_at IS
  NULL` already permits at most one live and one test connection per org, and mode is
  resolved from the running environment (4.0.2; 6.0.2 step 6), never chosen by a tool. A
  `label` here would be a second, weaker way to express the same selection.
- **No `livemode` field.** A tool must never name its own mode. Mode is a property of the
  running process, and letting a registry entry override it is how a test-mode tool reaches
  live money.
- **No amount or charge-id fields.** The amount already belongs to
  `ToolSpendProfile.amount_field` (`[CODE-VERIFIED]` `base.py:99`), and 7.5's review trigger
  reads that same validated input. Naming the amount twice invites the two readings to
  diverge, which on a refund path is a money bug.

### 4.5.4 What the gate checks

`[RESEARCH-SUGGESTED]` `ToolProxy._authorize_stripe`, mirroring `_authorize_wif`
(`[CODE-VERIFIED]` `proxy.py:637-720`) in structure: every exit that is not a silent return
denies, and **each denial is audited before it is raised**, so a refused Stripe call leaves
the same trail a refused scope check does (`proxy.py:660-667`).

0. **The repository is unwired** -> `ToolStripeNotConnected`. Fail closed, exactly as
   `_authorize_wif` does at `proxy.py:668-676`: a tool that moves a customer's money must
   never dispatch because the check itself was not wired.
1. **No `org_stripe_accounts` row** for `(org_id, livemode = the running environment's mode)`
   with `deauthorized_at IS NULL` -> `ToolStripeNotConnected`, naming "connect Stripe" as the
   remedy. **No fallback between modes, in either direction** (4.0.2). A live process must
   never silently act through a test connection, and a test process must never reach live
   money.
2. **`row.scope` does not cover `profile.required_scope`** -> `ToolStripeScopeInsufficient`.
   This is what `org_stripe_accounts.scope` exists for (4.0.2), and it is the platform's own
   attenuation invariant applied at the gate: the effective permission is an intersection
   that includes the provider grant actually held (`integration_inputs.md:31-35`).
   `read_only` covers no mutating verb.
3. **The direct-charge invariant of 2.0**, when `profile.mutates_money` is true ->
   `ToolStripeChargeTypeForbidden`. The validated input must carry none of `on_behalf_of`,
   `transfer_data`, or `application_fee_amount` in their destination-charge sense. 2.0 calls
   for "a checked invariant, not a convention"; **this is where it becomes one.** A
   destination charge silently inverts dispute and fraud liability onto Skylize, and 2.0
   names that as the worst failure shape available here.
4. **The 7.5 review trigger** (large-refund / fraud-signal interim rule), for refund verbs
   only. Last check in the stage, and still before the spend reservation. Specified in 7.5.4.

`[RESEARCH-SUGGESTED]` Distinct error types, not one, for the reason `_authorize_wif` gives
at `proxy.py:652-656`: collapsing them "would hand an operator the wrong fix during an
incident". The remedies genuinely differ - connect the account, re-consent for a wider scope,
fix the tool registration, route to a human.

### 4.5.5 Where the stage sits in the chain

`[CODE-VERIFIED]` The existing order in `ToolProxy.invoke` is OAuth (`proxy.py:266`) ->
permission (`:288`) -> WIF (`:306`) -> spend (`:319`) -> dispatch (`:326-331`).

`[RESEARCH-SUGGESTED]` **The Stripe stage goes after WIF and before spend**, for the reason
the WIF stage's own comment gives at `proxy.py:294-305`: a broken trust IS a denial, and
ordering it after the spend hold "would reserve budget against the customer's ceiling only to
discover the call could never run, leaving a hold to unwind". Every check in 4.5.4 is a
tenant-scoped DB read or a field inspection - all cheaper than a hold on the shared mutable
ceiling, which "must be placed as late as possible" (`proxy.py:313-318`).

### 4.5.6 The tripwire that must be edited, deliberately

`[CODE-VERIFIED]` `tests/contract/test_stateless_agents_no_oauth_access.py` asserts against
the fully wired registry that the set of profile-gated tools is EXACTLY an expected set:
`EXPECTED_OAUTH_TOOL_IDS` (`:48`), `EXPECTED_PERMISSION_TOOL_IDS` (`:68`), and the exactness
tests at `:177` and `:191`. The WIF gate got the same treatment (`:296`,
`test_the_gcp_verb_is_gated_by_the_federation_profile`).

`[RESEARCH-SUGGESTED]` A `ToolStripeProfile` needs a matching `EXPECTED_STRIPE_TOOL_IDS` set
**and** an exactness assertion, plus inclusion in the stateless-agent overlap check at
`:161-170`. Adding the set without the assertion would leave the strongest guard in the repo
half-applied - the same warning `gcp_wif_killswitch_design.md:1120-1129` records for WIF.

---

## 5.0 - The RLS circularity (Q3.0b) - re-examined, still applies

> **Section status: `[OWNER-DECISION-REQUIRED]`**

`[CODE-VERIFIED]` Re-examined against the lighter table, as instructed. **The problem is
unchanged**, because it never depended on the table's width - only on the fact that the mapping
is behind RLS and the webhook has no tenant session yet.

RLS binds on `skylize.org_id`, set per transaction by `tenant_session`
(`src/skylize/dal/connection.py:71-80`). `admin_session` exists but is documented as: "RLS tables
return nothing here by design" (`:83-85`).

An inbound Stripe webhook carries `account: acct_...` and **no `org_id`**. The handler must map
`acct_ -> org_id` *before* it can open a tenant session - but the mapping lives in an
RLS-protected table it cannot read without already knowing the answer. Circular. Making the
table lighter removes columns; it does not remove the circle.

`[RESEARCH-SUGGESTED]` The same two resolutions carry forward, unchanged:

- **(A) A narrow `SECURITY DEFINER` resolver**, owned by the table owner, granted `EXECUTE` to
  `skylize_app`, taking `acct_id` and returning one `TEXT` `org_id` and nothing else, with
  `search_path` pinned to `pg_catalog, public`. Single source of truth; minimal, auditable
  carve-out.
- **(B) A second, non-RLS `acct_id -> org_id` directory table** read via `admin_session`. Avoids
  `SECURITY DEFINER` but creates two sources of truth that can drift - and a drifted directory
  routes a tenant's webhook to the wrong org, which is a tenancy breach rather than a bug.

`[RESEARCH-SUGGESTED]` (A) is preferred on the drift argument. Either way this is an RLS
carve-out and needs `chief_security_officer` sign-off; it is the same class of decision as the
`skylize.rehydrate` read carve-out already precedented in migration 0002
(`[CODE-VERIFIED]` `connection.py:93-95`).

---

## 6.0 - Webhooks

> **Section status: `[RESEARCH-SUGGESTED]` - carried forward unchanged; the account model does
> not affect it.**

### 6.0.1 Endpoint shape and the signing secret

`[STRIPE-DOC-VERIFIED]` (https://docs.stripe.com/connect/webhooks) A Connect webhook endpoint is
configured with `connect: true` ("Events from: Connected accounts") and receives events for
**all** connected accounts through **one endpoint with one signing secret**. Each such event
carries a top-level `account` property naming the connected account.

**This answers Q2.1b with evidence.** The signing secret is **platform-level** - one secret in
the secrets manager, not a per-org `org_credentials` row. The question as posed at
`integration_inputs.md:224-227` presupposed a per-org secret that Stripe's model does not have.

`[RESEARCH-SUGGESTED]` Two distinct endpoints, with different secrets and different trust
meanings:

| Route | Scope | Purpose |
|---|---|---|
| `/webhooks/stripe/connect` | `connect: true` | connected-account events: `account.application.deauthorized`, refunds, disputes, charges |
| `/webhooks/stripe/platform` | `connect: false` | Skylize's own billing of its customers - the platform-level Stripe use in the 2.0 classification table |

Collapsing these into one route would let an event about Skylize's own billing be processed as
though it were a tenant's.

### 6.0.2 Verification order

`[RESEARCH-SUGGESTED]` The order is load-bearing; each step must precede the next:

1. **Read the raw body.** Signature verification is over exact bytes; any parse-then-reserialize
   breaks it. `await request.body()` before any JSON parsing.
2. **Verify `Stripe-Signature`** (HMAC-SHA256 over `timestamp.payload`) against the platform
   signing secret, in constant time, with a timestamp tolerance to bound replay.
3. **Reject on failure with `401`** and emit `governance.integration_bad_signature` - already
   specified at `docs/06_integrations/stripe.md:31-32`, and now implementable.
4. **Only then parse.** An unverified payload is attacker-controlled input and must not reach the
   JSON decoder before step 2 passes.
5. **Resolve `account` -> `org_id`** via 5.0. Unknown account: `2xx` and drop with an audit
   record - a customer may have disconnected, and retry storms help nobody.
6. **Check `livemode`** against the running environment and drop on mismatch.
   `[STRIPE-DOC-VERIFIED]` "your production webhook URLs receive both live and test webhooks."
   A test-mode event must never mutate live tenant state.
7. **Open `tenant_session(org_id)`** and handle the event inside it, so RLS applies to every
   write exactly as on the request path.
8. **Deduplicate on the event id.** Stripe retries; handlers must be idempotent.

`[STRIPE-DOC-VERIFIED]` `account.application.deauthorized` "occurs when a connected account
disconnects from your platform" and is available for accounts with Dashboard access, which
includes Standard. It sets `deauthorized_at` and is the mechanism that makes gap 4 closable.

### 6.0.3 Testing without live Stripe

`[RESEARCH-SUGGESTED]` Three layers, none requiring a live Stripe account:

1. **Signature unit tests, no network.** HMAC-SHA256 with a known secret, so fixtures are
   *constructed* in-test: valid signature, tampered body, stale timestamp, malformed header,
   wrong secret, multiple `v1` schemes in one header. Fully testable offline with `cryptography`,
   already a dependency (`[CODE-VERIFIED]` `pyproject.toml:17`).
2. **Routing/tenancy integration tests** against Postgres with two orgs and two `acct_` ids:
   an event for org A's account must never write org B's rows; an unknown account drops cleanly;
   a `livemode` mismatch drops. These must **run**, not skip - and per CLAUDE.md must run as the
   non-superuser `skylize_app` role, or they prove nothing about RLS.
3. **Stripe CLI**, developer-local only (`stripe listen --forward-connect-to`,
   `stripe trigger --stripe-account`), never a CI gate - it needs credentials CI must not hold.

**`[RESEARCH-SUGGESTED]` No live-Stripe test may be a CI gate.** A test that silently skips
without credentials is precisely the failure mode CLAUDE.md warns about.

---

## 7.0 - Spend wiring and idempotency

> **Section status: gaps 1 and 2 carried forward unchanged.** They are
> account-model-independent and hold identically under Standard + OAuth and under
> Accounts v2.
> **Gap 3 and the idempotency derivation are REWRITTEN (R2, 2026-09-09):** the August
> draft predated `ToolContext.hitl_id`.

`[CODE-VERIFIED]` **The spend profile is already money-denominated.** `ToolSpendProfile`
(`src/skylize/tools/base.py:78-97`) carries `currency` and `amount_field` (`:98-99`), and
`:86-89` states
the field holds "the amount in integer MINOR units (cents), the same unit `SpendEnvelope` and
`budget_ledger` use." `SpendLedger.reserve` takes `amount_minor: int`
(`src/skylize/app/principal/spend.py:116-125`). Stripe's `amount` is in the currency's minor
unit; the units already agree. The gate is genuinely wired: `ToolProxy.invoke` runs the spend
stage at `src/skylize/tools/proxy.py:319-324`, calling `_reserve_spend` (`:723`).

**Citation note (2026-09-09):** the three `file:line` references in this paragraph and in
gaps 1-2 below were re-verified at this HEAD and four had drifted since 2026-08-28. They are
corrected in place. The claims themselves are unchanged.

Three gaps block a refund tool specifically. All three were derived in the prior pass and stand:

1. **Currency mismatch.** `ToolSpendProfile.currency` is frozen per tool (`base.py:96,98`), but a
   refund's currency belongs to the charge. `CeilingExceeded` reports `envelope.currency`
   (`spend.py:157`) with no cross-currency reconciliation. Must fail closed on mismatch - a
   100 JPY ceiling silently authorising a 100 EUR refund is the failure to prevent.
2. **Full refunds are not expressible.** Stripe permits omitting `amount`; `proxy.py:767-773`
   denies anything that is not a positive `int` (and excludes `bool`, an `int` subclass). The tool must resolve a full refund to an
   explicit cent amount before the gate.
3. **The two keys are different keys, and the August draft conflated them.** `[CODE-VERIFIED]`
   `proxy.py:788` hardcodes the SPEND-LEDGER reservation key as
   `f"tool:{tool.tool_id}:{uuid4()}"`, and the comment at `:779-787` explains why that is
   correct and must stay: `try_reserve` treats a repeated key as a retry and returns the
   ORIGINAL hold, so a key shared by two distinct calls "would let the second spend against
   the first's reservation." **That key is internal to the ledger. It is not, and must not
   become, the Stripe `Idempotency-Key`.** The August draft read the comment's closing line
   ("Idempotent replay needs a caller-supplied key, which this signature does not accept")
   as blocking Q2.1d. It does not: Q2.1d is about the key on the outbound Stripe POST, which
   the handler owns.

### 7.0.1 The idempotency key, corrected (R2)

`[CODE-VERIFIED]` **`correlation_id` is the wrong seed, and the codebase already says so in
its own words.** `ToolContext.hitl_id` exists precisely to close this gap
(`src/skylize/tools/base.py:60-72`). Its comment is worth quoting because it is the whole
argument: `HitlQueueService.approve` "mints a FRESH correlation id on every approval attempt
(app/hitl/service.py:160), and a transient failure after the claim releases the row back to
'pending' (app/hitl/service.py:234-246) so a human can retry - which re-executes the whole
agent run. An idempotency key derived from `correlation_id` would therefore differ on every
retry and defeat the provider-side deduplication it exists to trigger. `hitl_id` is stable
across those retries; that is the entire point of threading it here."

`[CODE-VERIFIED]` The plumbing is already in place. `ToolProxy.invoke` accepts
`hitl_id: UUID | None` (`proxy.py:160-167`) and threads it onto the `ToolContext` handed to
the handler (`proxy.py:326-329`), annotated as "LOAD-BEARING FOR EXTERNALLY-MUTATING
HANDLERS" (`base.py:60-62`). **A Stripe refund handler is exactly such a handler.** No
signature change to `ToolProxy.invoke` is required for this - which retires precondition 3
of 8.0 as originally written.

`[STRIPE-DOC-VERIFIED]` On Stripe's side (https://docs.stripe.com/api/idempotent_requests):
keys are pruned after **24 hours**, are up to 255 characters, and Stripe errors if a key is
reused with different parameters. Stripe also advises against putting sensitive data in a
key.

`[RESEARCH-SUGGESTED]` **Derive in the handler, from `hitl_id`:**

```
Idempotency-Key = uuid5(SKYLIZE_NAMESPACE, f"{hitl_id}:{charge_id}:{amount_minor}")
```

Stable across every replay of one approved decision; distinct across different decisions and
different refund targets; carries no customer data. This is the same derivation the GCP
kill-switch design adopted for Google's `requestId`, for the identical reason
(`docs/06_integrations/gcp_wif_killswitch_design.md:1134-1140`) - so the two externally-
mutating connectors in this repo share one idempotency discipline rather than inventing two.

`[RESEARCH-SUGGESTED]` **When `hitl_id` is `None`.** It is `None` on the ordinary,
non-deferred request path (`[CODE-VERIFIED]` `base.py:63-64`). Two consequences, and neither
may be left implicit:

- If Q2.1c resolves to "refunds always defer to a human", every refund executes on the
  replay path and `hitl_id` is always present. The derivation above is then total.
- If some refunds may execute without HITL, those calls have no retry-stable seed at all,
  and a UUID minted per attempt would be worse than useless - it would look like an
  idempotency key while guaranteeing a duplicate refund on a timeout-then-success. **A
  refund handler must therefore REFUSE to build a key from anything per-attempt.** The
  durable local dedupe record below is what covers this case.

`[RESEARCH-SUGGESTED]` **The durable local dedupe record is still required, and the 24-hour
prune is why.** Stripe's key window is 24 hours; a HITL ticket can sit in a queue longer
than that. After the prune, a replayed refund with the same key is a NEW refund to Stripe.
Only a local, tenant-scoped record of "(org, charge, amount) was already refunded, here is
the resulting `re_...` id" makes a late replay safe. It is checked in the handler, which is
the layer that can return the prior refund rather than deny.

`[OWNER-DECISION-REQUIRED]` **Q2.1l - the residual the dedupe record exposes.**
`[CODE-VERIFIED]` `ToolProxy` commits the hold with the full reserved amount unconditionally
(`proxy.py:378-382`, `actual_minor=reservation.amount_minor`), even though `commit` accepts
a lower actual (`app/principal/spend.py:161-172`). A replayed refund that the dedupe record
short-circuits moves **no new money at Stripe**, but still commits a second full reservation
against the org's envelope - overstating spend. Three candidate fixes, and only the owner
should pick: (a) let the handler report an actual of `0` on a deduped replay, which requires
a proxy change to read the actual from the handler's output; (b) check the dedupe record at
the gate, before the reservation, which turns a gate into a control-flow branch that returns
a prior result; (c) accept the overstatement and reconcile from the audit trail. **This is
the only part of the idempotency design that is not resolvable inside the connector.**

`[OWNER-DECISION-REQUIRED]` Q2.1c (the refund ceiling number, or "refunds always defer to a
human") remains open and is **not** resolved by this document. **7.5 specifies the machinery
its answer would be configured through; it does not answer it.** Until 1.1 is resolved and a number
exists, a Stripe refund tool must not be registered as spend-capable.

`[CODE-VERIFIED]` Registration note: `stripe.refund` already exists as a scope string in the same
vocabulary as `ToolGrant.tool_id` (`src/skylize/app/principal/models.py:54-56`, used at `:74`,
`tests/unit/test_principal_authority.py:44`, `tests/contract/test_cowork_contract.py:89`).
Registering the real tool under exactly that id keeps the existing authority fixtures meaningful.

---

## 7.5 - Per-org refund limits and the large-refund review trigger

> **Section status: `[RESEARCH-SUGGESTED]` schema + `[INTERIM-RULE]` policy. No code, no
> migration, this pass. The NUMBERS are `[OWNER-DECISION-REQUIRED]` and the tables are
> seeded EMPTY, so no number is baked in by this design.**
> Added 2026-09-09. This is the machinery Q2.1c's answer would be configured through; it
> does not itself answer Q2.1c.

### 7.5.0 UNIT WARNING - read before touching either table

**Both tables in this section are denominated in currency MINOR units (cents). They are NOT
micro-units.** Stated first, in its own subsection, because the mistake is a silent 10,000x.

`[CODE-VERIFIED]` The three ledgers ADR-0006 forbids conflating do not share a unit:

| Object | Unit | Cite |
|---|---|---|
| `SpendEnvelope.ceiling_minor`, `SpendLedger.reserve(amount_minor=...)` | currency **MINOR** units (cents) | `src/skylize/app/principal/models.py:208`; `src/skylize/app/principal/spend.py:116-125` |
| `ToolSpendProfile.amount_field` | currency **MINOR** units (cents) - "the same unit `SpendEnvelope` and `budget_ledger` use" | `src/skylize/tools/base.py:86-89,99` |
| `org_spend_ceiling.ceiling_micros` | **micro-USD** - millionths of one USD, "NOT cents, NOT minor currency units" | `migrations/versions/0014_org_spend_ceiling.py:21-24,86-92` |

A refund limit is compared against a refund amount, which reaches the gate through
`ToolSpendProfile.amount_field` in minor units and is reserved through
`SpendLedger.reserve(amount_minor=...)`. **Therefore the limits here are minor units.**

`[CODE-VERIFIED]` **`org_spend_ceiling` (migration 0014) is a STRUCTURAL TEMPLATE for this
design, and is NOT the table to extend.** Borrowed from it: `ENABLE` + `FORCE ROW LEVEL
SECURITY` so even the table owner is a policy subject (`0014:99-100`), the single
`tenant_isolation FOR ALL` policy on `current_setting('skylize.org_id')` (`0014:101-106`),
`SELECT, INSERT, UPDATE` for `skylize_app` with no DELETE path (`0014:113`), the empty seed
(`0014:48-50,116-120`), and the fail-closed-on-missing-row rule (`0014:44-46`). Borrowed from
it and then **inverted**: the unit. Its `ceiling_micros` is micro-USD LLM spend; these
columns are minor-unit business money. Adding a refund column to `org_spend_ceiling` would
put two different units in one table, which is the exact confusion ADR-0006 exists to
prevent.

### 7.5.1 Two tables, not one table with two columns

`[RESEARCH-SUGGESTED]` **Decision: two tables. Stated with the reason, because the owner left
the choice open.**

The two numbers answer different questions and have **different natural keys**:

- "How large a refund may an agent at authority level L issue?" is keyed
  `(org_id, currency, authority_level)`.
- "Above what size does ANY refund go to a human regardless of who asked?" is keyed
  `(org_id, currency)`.

Folding the second into the first forces one org-scoped value to be duplicated across up to
five authority rows, which can then **disagree**. A disagreement there does not fail loudly:
it means an `executive`-level row can carry a review threshold high enough to disable the
review the low rows still think is running. A control that can be silently switched off by
editing an unrelated row is not a control. Two tables make that unrepresentable.

### 7.5.2 `org_refund_authority_limits`

`[RESEARCH-SUGGESTED]` The per-authority-level refund cap.

```sql
CREATE TABLE org_refund_authority_limits (
    org_id           TEXT NOT NULL REFERENCES tenants(org_id),
    currency         TEXT NOT NULL,        -- ISO-4217, 3 chars, matching ToolSpendProfile.currency
    authority_level  TEXT NOT NULL,        -- 'worker'|'manager'|'director'|'vp'|'executive'
    max_refund_minor BIGINT NOT NULL CHECK (max_refund_minor >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, currency, authority_level),
    CONSTRAINT authority_level_known CHECK (
        authority_level IN ('worker','manager','director','vp','executive')
    )
);

COMMENT ON COLUMN org_refund_authority_limits.max_refund_minor IS
  'currency MINOR units (cents), the SAME unit as SpendEnvelope.ceiling_minor and '
  'ToolSpendProfile.amount_field. NOT micro-units, NOT the unit of '
  'org_spend_ceiling.ceiling_micros.';
```

`[CODE-VERIFIED]` The five `authority_level` values and their order are the canonical ladder
`AUTHORITY_RANK` (`src/skylize/contracts/base.py:41-47`: `worker`=1, `manager`=2,
`director`=3, `vp`=4, `executive`=5). The `CHECK` deliberately enumerates them rather than
referencing a lookup table, matching how migration 0024 constrains its own enums
(`migrations/versions/0024_gcp_wif_connections.py:152-163`). **The ladder is not redefined
here** - `contracts/base.py:35-40` warns that "a second, privately-defined copy of this
ladder is exactly the drift that would make 'the agent can never exceed the human' silently
false in one of the two". This `CHECK` is a value constraint, not a second ranking.

`[RESEARCH-SUGGESTED]` **No monotonicity constraint across levels is enforced in SQL**, and
that is deliberate: a `manager` cap above a `director` cap is a misconfiguration, but
expressing it as a table-level constraint requires a cross-row check Postgres cannot do
cheaply. It is instead an assertion in the DAL setter (7.5.5) and a test.

`[RESEARCH-SUGGESTED]` **Currency is part of the key, and there is no cross-currency
fallback.** 7.0 item 1 already requires the refund path to fail closed on a currency mismatch
- "a 100 JPY ceiling silently authorising a 100 EUR refund is the failure to prevent". A
missing `(org, currency, level)` row is a refusal, never a fall back to another currency's
row.

RLS and grants: `ENABLE` + `FORCE`, one `tenant_isolation FOR ALL` policy on
`current_setting('skylize.org_id', true)` for both `USING` and `WITH CHECK`, and
`GRANT SELECT, INSERT, UPDATE TO skylize_app` with no DELETE - byte-for-byte the shape at
`[CODE-VERIFIED]` `migrations/versions/0014_org_spend_ceiling.py:99-113`.

**Seeded EMPTY.** A missing row denies. There is deliberately no platform-wide default
refund cap, for the reason `0014:44-46` gives for the spend ceiling: "an implicit global is
unauditable".

### 7.5.3 `org_refund_review_thresholds`

`[RESEARCH-SUGGESTED]` The org-scoped, authority-independent review trigger.

```sql
CREATE TABLE org_refund_review_thresholds (
    org_id                    TEXT NOT NULL REFERENCES tenants(org_id),
    currency                  TEXT NOT NULL,
    review_above_minor        BIGINT NOT NULL CHECK (review_above_minor >= 0),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, currency)
);

COMMENT ON COLUMN org_refund_review_thresholds.review_above_minor IS
  'currency MINOR units (cents). A refund at or above this amount is routed to a human '
  'regardless of the requesting agent authority level. NOT micro-units.';
```

Same RLS, grants, empty seed, and fail-closed-on-missing-row rule as 7.5.2.

`[RESEARCH-SUGGESTED]` **Fail-closed here means "route to a human", not "deny".** A missing
threshold row must not silently mean "no refund is ever large enough to review". The absence
of a configured threshold is treated as "review everything", which is the conservative
reading and matches the option Q2.1c already offers ("refunds always defer to a human"). An
org that has configured nothing therefore gets the safest behaviour by default rather than
the loosest.

### 7.5.4 The interim rule, and where it hooks in

> **`[INTERIM-RULE]` - RULE-BASED INTERIM, NOT THE EVENTUAL `fraud_detection_agent`
> INTEGRATION.** Labelled explicitly so a later session does not mistake it for fraud
> detection.

`[CODE-VERIFIED]` **`fraud_detection_agent` is contract-only and not wired.** The contract
exists (`src/skylize/contracts/mvp/security.py:10`) and declares
`output_schema="skylize.schemas.agents.security.FraudVerdictOut"` (`:16`), but no module
under `src/` outside that declaration references `FraudVerdictOut` at all - the schema is
declared and never consumed. There is no fraud signal available to a refund gate today, and
designing against one would be designing against something that does not exist.

`[RESEARCH-SUGGESTED]` **The interim rule, in full:**

> A refund is routed to a human when **either**
> (a) `refund_amount_minor > org_refund_authority_limits.max_refund_minor` for
> `(org_id, currency, the requesting contract's authority_level)`, **or**
> (b) `refund_amount_minor >= org_refund_review_thresholds.review_above_minor` for
> `(org_id, currency)`.
> A missing row on either lookup routes to a human. Neither lookup ever falls back to
> another currency, another authority level, or a platform default.

`[RESEARCH-SUGGESTED]` **Why an absolute per-org threshold and NOT "N% of the original
charge".** The owner offered both. The percentage rule is rejected on a concrete defect, not
a preference:

- To evaluate `refund > N% of original charge`, the gate needs the **original charge
  amount**. It has two ways to get it, and both are bad.
- **Take it from the tool input.** The input is agent-authored. An agent that wants to clear
  the rule states a larger original amount. The control is then defeated by the party it
  exists to constrain, which is worse than having no control, because it reads as enforced.
- **Read it from Stripe** (`GET /v1/charges/{id}`). This puts a network round trip inside a
  gate whose whole ordering argument is that cheap local denials run before expensive ones
  (`[CODE-VERIFIED]` `proxy.py:262-265,313-318`), and it makes the review trigger fail
  *open* or *hard* on a Stripe outage - a new failure mode on the money path.

An absolute minor-unit threshold read from a tenant-scoped table has neither problem: it is a
local read, it cannot be influenced by the agent, and its unit already matches the amount it
is compared against. **A percentage rule remains a defensible LATER refinement once a trusted
charge-amount read exists** - most naturally as part of the real `fraud_detection_agent`
integration, where a Stripe read is already justified.

`[RESEARCH-SUGGESTED]` **Hook point: check 4 of the Stripe stage (4.5.4), before the spend
reservation.** Specifically:

`[CODE-VERIFIED]` The spend stage runs at `proxy.py:319-324` and calls `_reserve_spend`
(`:723`), which places a hold on the shared mutable ceiling before any of this design's
refund logic could otherwise run. The refund review decision must be reached **before that
hold exists**, for the reason the proxy states in its own words at `proxy.py:313-318`: the
hold "must be placed as late as possible - after every cheaper denial has had its chance".
Routing a refund to a human *after* reserving against the ceiling would leave a hold to
unwind for a call that was never going to execute - the same waste the OAuth and WIF stages
were ordered to avoid (`proxy.py:254-265`, `:294-305`).

Placing it here has a second, larger consequence worth stating plainly: **the review decision
is reached before `ToolSpendProfile`'s ceiling math runs at all.** A refund that is too large
for its requester's authority never touches the org's spend envelope, so a rejected-then-
retried refund cannot churn the ceiling.

`[RESEARCH-SUGGESTED]` **What "route to a human" means mechanically.** It raises a denial of
the `ToolSpendDeferredToHuman` family rather than a hard denial, so it lands on the existing
deferred-to-human path rather than a new one. `[CODE-VERIFIED]` that type already exists
(`src/skylize/tools/base.py:310`) and is already raised by the spend gate for the
`defer_to_human` ceiling disposition (`proxy.py:809-812`).

`[RESEARCH-SUGGESTED]` **The containment auto-hook must NOT fire for this rule.**
`[CODE-VERIFIED]` `_schedule_containment` fires on `CeilingExceeded` for BOTH dispositions
on purpose (`proxy.py:792-808`) - "the ceiling was breached either way, and that is the fact a
containment responds to" - and is deliberately not fired for `EnvelopeNotFound` or
`ToolSpendUnavailable`, because those mean "we could not check", not "the customer is
overspending" (`proxy.py:801-807`). A large refund awaiting review is neither: no ceiling has
been breached. Firing containment here would propose shutting things down every time an
agent asked for a refund above a routine threshold, which is precisely the "safety control
[that] starts stopping healthy machines" that comment warns against.

### 7.5.5 DAL: `OrgRefundLimitsDAL`

`[RESEARCH-SUGGESTED]` One DAL over both tables, modelled on `OrgSpendCeilingDAL`
(`[CODE-VERIFIED]` `src/skylize/dal/org_spend_ceiling.py`) for the audited-setter pattern and
on `PgPermissionGrantRepository` (`src/skylize/dal/permission_grants.py:71-101`) for the
explicit-org-predicate discipline.

Every read and write runs inside `Database.tenant_session(org_id)` **and** carries an
explicit `org_id` predicate, "so the RLS policy and the query agree rather than the query
relying on RLS alone" (`[CODE-VERIFIED]` `dal/permission_grants.py:5-8`).

```
read_authority_limit_minor(org_id, currency, authority_level) -> int | None
read_review_threshold_minor(org_id, currency)                 -> int | None
set_authority_limit(*, org_id, currency, authority_level, max_refund_minor,
                    audit, correlation_id, source_agent_id=None,
                    governance_token_id=None) -> None
set_review_threshold(*, org_id, currency, review_above_minor,
                     audit, correlation_id, source_agent_id=None,
                     governance_token_id=None) -> None
```

`[RESEARCH-SUGGESTED]` Rules the setters carry, each mirroring a cited precedent:

1. **Audited on every change.** `[CODE-VERIFIED]` `OrgSpendCeilingDAL.set_ceiling` reads the
   previous value, upserts, then records an `AuditService` action carrying `before -> after`
   (`dal/org_spend_ceiling.py:122-156`), because "a ceiling change is a GOVERNANCE EVENT, not
   silent config" (`:110-113`). Same shape, with action types
   `governance.refund_authority_limit_set` and `governance.refund_review_threshold_set`.
2. **Validate before writing.** `set_ceiling` rejects a negative value before the write
   (`dal/org_spend_ceiling.py:118-121`); these reject negative amounts, an unknown
   `authority_level`, and a `currency` that is not three characters - the same length
   constraint `ToolSpendProfile.currency` and `SpendEnvelope.currency` carry
   (`[CODE-VERIFIED]` `tools/base.py:98`, `principal/models.py:207`).
3. **Reject a non-monotonic ladder.** `set_authority_limit` reads the org's other rows for
   the same currency and refuses a value that would leave a lower authority level with a
   higher cap than a higher one. This is the constraint 7.5.2 declines to express in SQL.
4. **`None` is never fabricated into a default.** A read returning `None` is the honest "not
   configured" signal the caller acts on, exactly as `read_ceiling_micros` documents
   (`dal/org_spend_ceiling.py:26-28`). For 7.5.2 the caller denies; for 7.5.3 the caller
   routes to a human (7.5.3).
5. **No effective-dating.** `org_spend_ceiling` is effective-dated because it is keyed by
   billing period (`dal/org_spend_ceiling.py:19-22`). These tables have no period dimension:
   a refund limit is current config, and the audit trail from rule 1 is what reconstructs
   history.

### 7.5.6 Open questions this section raises

- **Q2.1j `[OWNER-DECISION-REQUIRED]`** The refund cap numbers per authority level, per
  currency. **The design does not need them to be complete** - both tables are seeded empty
  and a missing row fails closed, exactly as `org_spend_ceiling` does (`0014:48-50`). These
  are an ops/owner input at configuration time, in the same class as Q2.1c, not a design
  blocker.
- **Q2.1k `[OWNER-DECISION-REQUIRED]`** Does an approved large refund, once a human has
  approved it, still consume the org's spend envelope? This design assumes **yes** - approval
  changes who authorized the money movement, not whether it moved - but the alternative
  (approved refunds bypass the ceiling) is a coherent business position and only the owner
  can pick.

---

## 8.0 - Preconditions before Stripe connector code

`[RESEARCH-SUGGESTED]` Superset of `integration_inputs.md` 4.0, Stripe-specific:

1. 1.1 resolved - a spend-capable tool call reaches a synchronous ceiling check before egress.
2. Q2.1c answered - refund ceiling number, or "always defer to a human."
3. Q2.1d approved. **No longer blocked on a `ToolProxy.invoke` change** - `hitl_id` is
   already threaded to the handler (`proxy.py:160-167,326-329`), so the handler can derive
   the Stripe `Idempotency-Key` itself (7.0.1). What remains is the durable local dedupe
   record, and Q2.1l (the double-commit residual, 7.0.1).
4. Q2.1h answered - the product answer for platform-controlled customer accounts (1.0.3).
5. Q2.1i confirmed - the discard of `access_token` / `refresh_token` (4.0.1).
6. Q3.0b answered - `SECURITY DEFINER` resolver vs directory table, with
   `chief_security_officer` sign-off on the RLS carve-out (5.0).
7. Direct-charge enforcement (2.0) specified as a checked invariant, not a convention.
8. `ToolStripeProfile` (4.5) approved, and the exactness tripwire in
   `tests/contract/test_stateless_agents_no_oauth_access.py` extended for it (4.5.6).
9. The two refund-limit tables (7.5) approved. **Their numbers are NOT a precondition** -
   both are seeded empty and fail closed, so an unconfigured org simply cannot refund.
10. Q2.1k answered - whether a human-approved refund still consumes the spend envelope
    (7.5.6).
11. Section 2.1 of `integration_inputs.md` reads `[APPROVED]`.

---

## 9.0 - Corrections owed to existing documents

`[CODE-VERIFIED]` If this design is ratified, these become stale and should be corrected in the
same pass, so the repo's documented failure mode of stale claims propagating does not recur:

- `integration_inputs.md:395-435` - 3.0's Q3.0a should record that **Stripe does not need
  `org_oauth_grants`**: no bearer token is stored, so the question narrows to GitHub and
  whichever of AWS/GCP 2.2 resolves to. Gaps 3 and 4 are closed for Stripe by the `scope` and
  `deauthorized_at` columns in 4.0.2; gaps 1, 2, and 5 do not arise.
- `docs/06_integrations/stripe.md:24-27` - section 3 describes a single platform API key with no
  Connect model at all. It needs the platform-key + `Stripe-Account` header model, the
  `connect: true` webhook scope, and the direct-charges constraint from 2.0.
- `docs/06_integrations/stripe.md:39` - "create/adjust subscriptions or usage records" predates
  the refund/spend path and should name the spend ceiling as a precondition.
- `integration_inputs.md:71-95` - 1.0.2's "no OAuth broker" inventory stays accurate as a
  statement of fact, but should note that the Stripe broker, when built, has no token store and
  no refresh loop, so it is materially smaller than the Faz A-D plan assumed.
- `integration_inputs.md` 2.1 Q2.1d - the citation `src/skylize/tools/proxy.py:322` is stale
  in two ways. The line is now `:788`, and the conclusion drawn from it was wrong: that key
  is the ledger reservation key, not the Stripe `Idempotency-Key` (7.0 item 3). **Corrected
  in the same 2026-09-09 pass as this revision.**
- `docs/06_integrations/gcp_wif_killswitch_design.md:1090-1094` describes `ToolWifProfile`
  as "a fourth opt-in profile". Once 4.5 is ratified there is a fifth, and the count in any
  doc that enumerates the gates goes stale. `base.py:137-140` carries the same count in a
  code comment and would need the same edit at implementation time.

---

## Sign-off

- 1.0 Standard + OAuth decision (Q2.1e, Q2.1f): __________  (owner, date)
- 1.0.3 Q2.1h platform-controlled accounts: _____________  (owner, date)
- 2.0 Direct-charge hard constraint: ____________________  (owner, date)
- 3.0 OAuth flow: _______________________________________  (owner, date)
- 4.0 `org_stripe_accounts` schema + Q2.1i discard: _____  (owner, date)
- 5.0 Q3.0b RLS resolver: _______________________________  (owner, date)
- 6.0 Webhook design (answers Q2.1b): ___________________  (owner, date)
- 7.0 Spend wiring and idempotency, incl. Q2.1l residual: _  (owner, date)
- 4.5 `ToolStripeProfile` (R1, fifth profile): __________  (owner, date)
- 7.5 Refund limit tables + interim review rule (R2): ___  (owner, date)
- 7.5.6 Q2.1j refund cap numbers / Q2.1k envelope rule: _  (owner, date)
