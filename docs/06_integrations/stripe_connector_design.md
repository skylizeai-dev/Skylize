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
> **How to read this file.** Every concrete claim is marked with one of:
> - `[CODE-VERIFIED]` - extracted from the current codebase. Ground truth. Cites `file:line`.
> - `[STRIPE-DOC-VERIFIED]` - read from Stripe's live documentation on 2026-08-28. Cites a URL.
> - `[RESEARCH-SUGGESTED]` - a defensible default. **NOT yet approved.**
> - `[OWNER-DECISION-REQUIRED]` - a design choice only the owner can make.
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

## 7.0 - Spend wiring and idempotency (unchanged by this decision)

> **Section status: carried forward. Both findings are account-model-independent - they hold
> identically under Standard + OAuth and under Accounts v2. Referenced, not re-derived.**

`[CODE-VERIFIED]` **The spend profile is already money-denominated.** `ToolSpendProfile`
(`src/skylize/tools/base.py:38-59`) carries `currency` and `amount_field`, and `:47-49` states
the field holds "the amount in integer MINOR units (cents), the same unit `SpendEnvelope` and
`budget_ledger` use." `SpendLedger.reserve` takes `amount_minor: int`
(`src/skylize/app/principal/spend.py:116-125`). Stripe's `amount` is in the currency's minor
unit; the units already agree. The gate is genuinely wired at
`src/skylize/tools/proxy.py:203-254`.

Three gaps block a refund tool specifically. All three were derived in the prior pass and stand:

1. **Currency mismatch.** `ToolSpendProfile.currency` is frozen per tool (`base.py:56-58`), but a
   refund's currency belongs to the charge. `CeilingExceeded` reports `envelope.currency`
   (`spend.py:157`) with no cross-currency reconciliation. Must fail closed on mismatch - a
   100 JPY ceiling silently authorising a 100 EUR refund is the failure to prevent.
2. **Full refunds are not expressible.** Stripe permits omitting `amount`; `proxy.py:304-309`
   denies anything that is not a positive `int`. The tool must resolve a full refund to an
   explicit cent amount before the gate.
3. **The reservation key is deliberately non-idempotent.** `proxy.py:322` hardcodes
   `f"tool:{tool.tool_id}:{uuid4()}"`, and the comment at `:314-321` ends: "Idempotent replay
   needs a caller-supplied key, which this signature does not accept." **Q2.1d's answer cannot
   be implemented through `ToolProxy.invoke` as it stands.**

`[STRIPE-DOC-VERIFIED]` On Stripe's side (https://docs.stripe.com/api/idempotent_requests):
keys are pruned after **24 hours**, are up to 255 characters, and Stripe errors if a key is
reused with different parameters. The 24-hour window means the derivation proposed at
`integration_inputs.md:229-236` protects in-run retries but **not** a later replay; a durable
local dedupe record is what actually makes replay safe. Stripe advises against sensitive data in
keys, so `[RESEARCH-SUGGESTED]` derive as
`sha256(correlation_id || tool_id || charge_id || amount_minor)`, hex-encoded.

`[OWNER-DECISION-REQUIRED]` Q2.1c (the refund ceiling number, or "refunds always defer to a
human") remains open and is **not** resolved by this document. Until 1.1 is resolved and a number
exists, a Stripe refund tool must not be registered as spend-capable.

`[CODE-VERIFIED]` Registration note: `stripe.refund` already exists as a scope string in the same
vocabulary as `ToolGrant.tool_id` (`src/skylize/app/principal/models.py:54-56`, used at `:74`,
`tests/unit/test_principal_authority.py:44`, `tests/contract/test_cowork_contract.py:89`).
Registering the real tool under exactly that id keeps the existing authority fixtures meaningful.

---

## 8.0 - Preconditions before Stripe connector code

`[RESEARCH-SUGGESTED]` Superset of `integration_inputs.md` 4.0, Stripe-specific:

1. 1.1 resolved - a spend-capable tool call reaches a synchronous ceiling check before egress.
2. Q2.1c answered - refund ceiling number, or "always defer to a human."
3. Q2.1d approved **and** `ToolProxy.invoke` extended to accept a caller-supplied idempotency
   key (7.0, item 3). Account-model-independent; should be its own reviewed change.
4. Q2.1h answered - the product answer for platform-controlled customer accounts (1.0.3).
5. Q2.1i confirmed - the discard of `access_token` / `refresh_token` (4.0.1).
6. Q3.0b answered - `SECURITY DEFINER` resolver vs directory table, with
   `chief_security_officer` sign-off on the RLS carve-out (5.0).
7. Direct-charge enforcement (2.0) specified as a checked invariant, not a convention.
8. Section 2.1 of `integration_inputs.md` reads `[APPROVED]`.

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

---

## Sign-off

- 1.0 Standard + OAuth decision (Q2.1e, Q2.1f): __________  (owner, date)
- 1.0.3 Q2.1h platform-controlled accounts: _____________  (owner, date)
- 2.0 Direct-charge hard constraint: ____________________  (owner, date)
- 3.0 OAuth flow: _______________________________________  (owner, date)
- 4.0 `org_stripe_accounts` schema + Q2.1i discard: _____  (owner, date)
- 5.0 Q3.0b RLS resolver: _______________________________  (owner, date)
- 6.0 Webhook design (answers Q2.1b): ___________________  (owner, date)
- 7.0 Spend wiring and idempotency: _____________________  (owner, date)
