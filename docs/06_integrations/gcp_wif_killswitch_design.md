# Design - GCP kill-switch via Workload Identity Federation

> **Type:** architecture design. **Pass discipline: DESIGN ONLY.**
> No code, no migration, no API route, no IAM policy, and no key material was
> written or generated in this pass.
>
> **Commit designed against:** `628671f` (branch `main`, clean tree).
> **Date:** 2026-09-04. Every codebase claim is cited `file:line` and was
> re-verified at this commit. Every live claim carries a URL and fetch date.
>
> **Predecessor:** `docs/audits/audit_gcp_killswitch_readiness.md` (`628671f`).
> That audit resolved nothing; this document resolves the design questions the
> owner's decisions below leave open, and names what remains open after it.
>
> **Owner decisions this design takes as settled and does not re-litigate:**
> - **GA-1:** the target is the CUSTOMER's real GCP project (audit path A).
> - **GA-2:** the action is VM stop only, scoped to specific instances, never
>   project-wide, never billing-disable.
> - **Mechanism:** Workload Identity Federation, not service-account key files.
> - `oauth_credentials` is incompatible; a new table is required.
> - The governance P-384 key is NOT reused for the JWKS.
>
> **Naming.** This document never calls the GCP action a "kill switch" without
> qualification. In this repository "kill switch" already means Skylize revoking
> its OWN governance tokens (`app/governance/authority.py:619-649`,
> `edge/routes/kill_switch.py:16`,
> `docs/04_decision_engine/kill_switch_protocol.md`). That mechanism is
> unrelated to this work and is deliberately not extended, renamed, or reused
> here. Where the two interact (section 6.6) they are named separately:
> **platform kill switch** (internal, existing) and **compute stop action**
> (external, designed here).

---

## 0. Read this first: two drifts and one gate

The brief set two hard exit gates and made assumptions that live verification
partly contradicts. Reporting that comes before designing against it.

### 0.1 DRIFT: this brief's GA-3..GA-9 are NOT the audit's GA-3..GA-9

The brief instructs "walk through GA-3 through GA-9" and, for GA-9, "check
`audit_gcp_killswitch_readiness.md` directly for the full GA-9 content, don't
guess what it contains." Doing exactly that reveals the two numbering schemes
diverge from GA-3 onward. The audit's GA-* namespace is at
`audit_gcp_killswitch_readiness.md` section E.

| Brief's GA-n | What the brief asks | Nearest audit GA-n | Match? |
|---|---|---|---|
| GA-3 | OIDC IdP surface to publish | audit GA-5 (first half) | renumbered |
| GA-4 | new table schema + revocation detection | audit GA-4 + audit GA-6 | merged |
| GA-5 | the signing key | audit GA-5 (second half) | renumbered |
| GA-6 | Google-side trust policy / attribute condition | audit GA-3 (resource+verb allowlist) | different question |
| GA-7 | the trigger path | audit GA-7 | **match** |
| GA-8 | failure modes | audit GA-6 (proactive health check) | renumbered |
| GA-9 | "anything else the audit flagged" | audit GA-9 = the missing `Skylize_Design_Specification.md` | **different** |

Two audit questions are therefore orphaned by the brief's numbering and would
have been silently dropped by following it literally:

- **audit GA-3** - the resource-and-verb allowlist, GCP-specific. Answered in
  section 8.1 of this document.
- **audit GA-8** - which spend concept triggers this at all, given ADR-0006
  forbids conflating the three ledgers. **Still open**; see section 8.2. This
  design is deliberately trigger-source-agnostic because of it.

Sections below are numbered by the **brief's** scheme (it is the deliverable
spec), and section 8 sweeps up every orphan.

### 0.2 DRIFT: per-tenant issuer URLs are a BEST PRACTICE, not a Google requirement

The brief's exit gate names this explicitly ("if per-tenant issuer URLs are no
longer required"). Live verification, 2026-09-04:

Google's SaaS-vendor guide puts per-tenant issuers in its **Best practices**
section, not its **Requirements** section:

> "If your product or service supports multi-tenancy, then several of your
> customers might share a single instance of your product or service, and their
> workloads' ID tokens might use the same issuer URL." ... "avoid using the same
> issuer URLs across multiple tenants and embed a unique tenant ID in the issuer
> URL, for example `https://saas.example.com/tenant-123/`."

The actual stated **requirements** are narrower: publicly accessible OpenID
provider metadata discoverable from the ID token, and a publicly accessible
JWKS discoverable from `jwks_uri`.

The predecessor audit stated this as a hard requirement ("The vendor must 'use
tenant-specific issuer URLs'", `audit_gcp_killswitch_readiness.md` B.4). That
overstates Google's text.

**Effect on this design: none to the recommendation, material to the reasoning.**
Section 2 still recommends per-tenant issuers, but as a Skylize security choice
whose cost/benefit Skylize owns, not as a compliance obligation. That matters:
if per-tenant endpoints later prove operationally expensive, a shared issuer
plus a mandatory `skylize_org_id` claim and customer attribute condition is a
**legitimate** fallback, not a violation. Section 5.4 states precisely how much
security the per-tenant issuer actually buys, which is less than it appears.

### 0.3 DRIFT: Google accepts RS256 or ES256 - which independently forecloses key reuse

The brief instructs "verify against live Google docs, don't assume" for the key
type. Verified 2026-09-04: workload identity pool OIDC providers accept tokens
"signed using the `RS256` or `ES256` algorithm."

The governance key is **P-384** (`app/governance/keys.py:71-76` asserts
`key_size == 384`; `contracts/token.py` `GOVERNANCE_CURVE`), which signs as
**ES384**. ES384 is not in Google's accepted set.

So the owner's decision to use a separate key is not only correct on trust-domain
grounds - it is **technically forced**. The governance key could not be used for
this even if someone wanted to. Recording it because a future session may
rediscover the temptation and this is the argument that ends it in one line.

### 0.4 GATE 2 (HITL/Decision-Engine reuse): did NOT trip - but the license is action-specific

The brief's second exit gate: stop if reusing the existing execution patterns is
structurally impossible, e.g. if the path assumes idempotent replay in a way a
VM stop cannot support.

**It is not impossible, and the gate does not trip.** But the reason it does not
trip is worth stating precisely, because it is a property of GA-2's chosen
action rather than of the execution path:

`HitlQueueService.approve` claims the row exactly once, then replays the ORIGINAL
request through `AgentExecutionService.execute()`
(`app/hitl/service.py:170-203`). On a **transient** failure after that claim, the
row is RELEASED back to `pending` (`app/hitl/service.py:236-247`) so approved
work is never lost - and a human retry then re-executes **the entire agent run**,
including any external call that already succeeded.

For `instances.stop` this is safe, for two independent reasons:

1. Stop converges on a state rather than incrementing one. Re-stopping a stopped
   instance reaches the same desired state.
2. Compute Engine accepts a `requestId` query parameter. Google's request-ID
   convention across Cloud APIs is that the server ignores duplicates for at
   least 60 minutes and requires a valid non-zero UUID.

**A non-idempotent action would NOT be safe on this path** without a compensating
design: billing-disable, instance delete, and snapshot delete would each be
re-issued by an ordinary transient-failure retry. GA-2's VM-stop decision is what
makes this reuse legal, and any future widening of the verb set must re-open this
analysis. That constraint belongs in the eventual migration/ADR text, not only
here.

**One concrete correctness requirement falls straight out of this** and is easy
to get wrong: the replay generates a FRESH correlation id
(`fresh_correlation = uuid4()`, `app/hitl/service.py:160`). A `requestId` derived
from the per-attempt correlation id would therefore differ on every retry and
defeat Google's deduplication entirely. **`requestId` MUST be derived
deterministically from a value stable across replays - `hitl_id` or
`decision_id` - never from `correlation_id`.** See section 6.5.

### 0.5 One thing the brief's premise gets right and one it leaves implicit

**Right:** the stateless invariant extends here unchanged. Confirmed in section 1.5.

**Implicit, and load-bearing:** a stopped VM is reversible but **does not zero the
bill**. Google: stopping preserves "attached disks, configuration, IP addresses,
MAC addresses, and instance metadata" and "you keep incurring charges for any
resources that remain attached to it". A compute stop is a *blast-radius*
control, not a *spend* control. Anyone framing this as "the CFO watches the bill
stop" is describing something this action does not do. That is not an argument
against GA-2 - the reversibility is exactly why VM stop is the defensible ask -
but the framing must be honest, and it feeds directly into the still-open audit
GA-8 (section 8.2).

---

## 1. REVIEW findings, re-verified at `628671f`

### 1.1 No OIDC discovery or JWKS serving surface exists - confirmed

`src/skylize/edge/routes/` contains 16 route modules; none is an OIDC provider
surface. A repo-wide grep for `well.known|jwks|openid|oidc|discovery` over `src/`
returns only **consumer**-side hits: `edge/auth.py:52-66` fetches an EXTERNAL
IdP's JWKS and verifies an inbound token; `config.py:93-94` holds
`oidc_jwks_url` / `oidc_audience` for that inbound path; `tenants.oidc_issuer`
(`dal/ports.py:146`, `dal/repositories.py:280`) records which external IdP a
tenant authenticates against.

**Skylize is today an OIDC relying party in every direction.** This design makes
it, for the first time, an OIDC *issuer*. The direction reversal is the whole
cost, and none of the existing OIDC config is reusable for it - a point worth
stating because the config names look reusable and are not.

Corollary, also verified: the gateway (`edge/gateway.py:56-90`) mounts one
FastAPI app with `install_error_handlers`, CORS, `/health`, and 16 authenticated
routers. There is **no unauthenticated public route today except `/health`**.
The discovery and JWKS endpoints will be the first genuinely public, externally
consumed surface on this app. Section 2.5 treats that as its own problem.

### 1.2 `oauth_credentials` shape - confirmed, and the incompatibility restated in one line

Migration chain through latest: `0021_oauth_credentials.py` creates the table;
`0023_oauth_credentials_nullable_expiry.py` relaxes `expires_at` to nullable.
`0022` is `org_permission_grants` and does not touch it. Latest revision is
`0023`, so the new table lands at **`0024`** (verify at implementation time).

The columns at HEAD (`0021:84-102`, as amended by `0023`):
`cred_id, org_id, provider, label, provider_account_id, key_id,
encrypted_access_token TEXT NOT NULL, encrypted_refresh_token TEXT,
expires_at TIMESTAMPTZ NULL, scopes TEXT[], connection_state, state_reason,
created_at, updated_at, refreshed_at`.

The audit's column-by-column analysis (B.2) holds and is not repeated. The
one-line version: **the table's mandatory central column is a stored secret, and
WIF stores no secret at all.** `encrypted_access_token TEXT NOT NULL`
(`0021:91`) has no honest value when the token is minted per call at STS and
discarded. Every non-secret field WIF does need - project number, pool id,
provider id, audience, service-account email, instance allowlist - would then
have to be smuggled through a schema whose freshness evaluator
(`app/credentials/oauth.py:267-297`) runs on **every governed tool call**
(`tools/proxy.py:243-247`) and would be answering a question that does not apply.

The precedent for refusing exactly this is already in the tree, in nearly the
same words: `0021:10-20` refused to widen `org_credentials` for OAuth because
"an OAuth grant is not one string". A WIF trust relationship is not a grant.

What IS reused from `0021`, verbatim: the `(org_id, ..., label)` identity
cardinality (`0021:107-110`), the `tenant_isolation` RLS policy with
`ENABLE` + `FORCE` (`0021:128-136`), the `skylize_app` grant, and the
deliberate exclusion from migration `0002`'s cross-tenant read carve-out.

### 1.3 Key-custody patterns - suitable for custody, insufficient for rotation

Two established patterns, and they are the same pattern:

| | governance signing key | credential encryption key |
|---|---|---|
| Generated | offline, `scripts/gen_governance_key.py` | offline, one-liner in the error message |
| Type | ECDSA P-384, PKCS8 PEM | Fernet, urlsafe base64 32 bytes |
| Transported | `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` | `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY` |
| Resolved | `app/governance/keys.py:33-67` | `bootstrap.py:91-149` |
| Missing in prod | **fails closed, refuses to start** | **fails closed, refuses to start** |
| Missing in dev | ephemeral, logged loudly, `backend == "memory"` only | same |
| Validated at boot | curve asserted (`keys.py:71-76`) | parsed eagerly (`bootstrap.py:118-126`) |
| Rotation | **none** | `key_id` column exists; one key in use (`0021:40-47`) |

`bootstrap.py:94` states the second was written to mirror the first. The new WIF
key follows it as a third instance, unchanged in every respect above.

**Where it is insufficient, and why.** Neither key has a working rotation story,
and the governance key does not need one: governance tokens live 5 minutes
(`config.py:89-90`, `token_ttl_minutes: int = 5`) and are verified only inside
the platform, so a hard swap costs one short window. A **published** JWKS cannot
be hard-swapped - the verifier is Google, its JWKS cache duration is undocumented
(checked 2026-09-04), and a customer may have uploaded the key set into their
provider config, in which case rotation is an action only the *customer* can
take. Section 4.4 extends the pattern with a two-slot keyring and a `kid`, which
the governance path has no concept of. That extension is the only place this
design departs from an established pattern, and it departs because the trust
boundary is genuinely different, not for convenience.

**No new dependency is needed.** `python-jose[cryptography]` is already a
first-party dependency (`pyproject.toml:27`, currently used only for inbound
verification at `edge/auth.py:59`) and signs as well as verifies. `ECCService`
already supports P-256 (`security/ecc_service.py:49,55`) with PEM
serialisation and load helpers. See section 4.1.

### 1.4 How an approved decision causes a real external action today

Traced end to end, because GA-7 must reuse it rather than invent a fifth path.

**There is exactly ONE way a real external side effect happens in this codebase.**
`ToolProxy.invoke` has a single application call site: `app/agents/execution.py:949`,
inside the agent tool loop. (`edge/routes/workflows.py:57` is
`orchestrator.invoke`, a different method on a different object.) Nothing else -
no worker, no route, no event handler - dispatches a tool.

The chain:

1. `POST /api/v1/agents/execute` -> `AgentExecutionService.execute()`.
2. `_govern` (`execution.py:515`) runs the **inline** decision evaluator
   (ADR-0004; `app/decision_engine`, the engine wired at bootstrap). Three
   outcomes only (`execution.py:85-86`): `approved`, `rejected`,
   `deferred_to_human`.
3. `rejected` -> `AgentGovernanceRejected` (`execution.py:162-166`, raised at
   `:596`), 403. Nothing executes.
4. `deferred_to_human` -> the `hitl_queue` row is written BEFORE the terminal
   event (ordering decision D3, `execution.py:537-544`), then
   `AgentDeferredToHuman` -> 202. The Slack notifier fires if configured.
5. A human calls `POST /api/v1/hitl/{id}/approve`. `HitlQueueService.approve`
   claims the row through a conditional `UPDATE ... WHERE status='pending'
   ... RETURNING` - "the ONLY mutation path ... never a read-then-write"
   (`app/hitl/service.py:17-21`) - then validates `HitlReplayEnvelope` and calls
   **`AgentExecutionService.execute()` again** with a `HitlApprovalContext`, so
   the evaluator is not re-consulted (`hitl/service.py:183-203`; re-running it
   "would defer forever", `execution.py:189-190`).
6. The run mints a signed governance token scoped to the contract's
   `ToolGrant.tool_id` list, then the tool loop dispatches through
   `ToolProxy.invoke` (`execution.py:949`).
7. `ToolProxy` validates the token through `validate_tool_call` - signature,
   expiry, **revocation, scope, live-state** - then runs three opt-in gates in a
   deliberately ordered chain (`tools/proxy.py:243-283`): OAuth credential
   state, then elevated-action permission, then spend reservation, each with its
   ordering justified in-line. Then, and only then, the handler runs.
8. `ToolProxy` emits one `audit.action_recorded` (`action_type="tool.invoked"`)
   per call (`tools/proxy.py:14-17`).

**Two structural consequences for GA-7, both real:**

- **Everything is agent-shaped.** There is no "execute a bare action" seam. The
  approve path is typed to return a `DeliverableRow`
  (`edge/routes/hitl.py` `HitlApproveResponse` requires
  `deliverable_id`, `agent_id`, `title`). A compute stop must therefore be an
  agent run that produces a deliverable. Section 6 argues this is a benefit, not
  a workaround.
- **A T4 hard-deny cannot *cause* an action.** The audit records Q2.2c's
  requirement that the trigger be "the Decision Engine's T4-class hard-deny path
  reaching the existing kill-switch surface". Read against the code that is
  **not implementable as stated**: a `rejected` outcome terminates a proposal and
  has no side-effect channel anywhere in the engine. Section 6.1 resolves this
  by inverting it - the compute stop is itself a proposal that goes THROUGH the
  gate, not a consequence of a denial - which satisfies Q2.2c's actual intent
  (one governed path, never a connector-owned second mechanism) while being
  buildable.

### 1.5 The stateless invariant extends here unchanged - confirmed

`cfo_agent` (`contracts/mvp/finance.py:183-184`) and the four safety agents
(`contracts/mvp/safety.py:28-29,50-51,72-73,93-94`) carry
`memory_read_access=[]` / `memory_write_access=[]`, and
`tests/contract/test_stateless_agents_no_oauth_access.py` asserts against the
**fully wired** registry that none of their tools carries a `ToolOAuthProfile` or
`ToolPermissionProfile`.

The tripwire is `EXPECTED_OAUTH_TOOL_IDS` (`:48-57`) and
`EXPECTED_PERMISSION_TOOL_IDS` (`:68-71`) - explicit allowlists so "a new
connector has to be added here consciously" (`:46-47`). A compute-stop tool wired
into `cfo_agent` fails this test, correctly.

**The invariant extends, and section 6 is built to honour it:** the proposing
agents stay stateless and credential-free; a separate stateful executor contract
holds the one tool. Section 6.4 records the specific test change the new gate
requires, so it is a conscious edit rather than a surprise red build.

---

## 2. GA-3 - what Skylize must publish as an OIDC identity provider

### 2.1 What Google actually requires (verified 2026-09-04)

| Requirement | Source | Note |
|---|---|---|
| OpenID provider configuration document at `{issuer}/.well-known/openid-configuration`, publicly accessible over the internet from any IP | SaaS-vendor guide | **hard requirement** |
| JWKS document at a publicly accessible endpoint discoverable from `jwks_uri` | SaaS-vendor guide | **hard requirement**; RFC 7517 shape |
| ID tokens signed, not encrypted; OIDC 1.0 conformant | SaaS-vendor guide | hard requirement |
| Signature algorithm `RS256` or `ES256` | other-providers guide | **hard requirement** |
| ID token lifetime <= 60 min | SaaS-vendor guide | stated as a **recommendation**; STS caps the federated token at the input token's `exp`, max 1 hour, regardless |
| Per-tenant issuer URLs | SaaS-vendor guide | **best practice, not a requirement** - see 0.2 |
| Let the workload specify the audience rather than a static `aud` | SaaS-vendor guide | best practice |
| Include context claims for the customer's conditions | SaaS-vendor guide | best practice; section 5.2 exploits this |

### 2.2 The minimum viable surface is much smaller than "an OIDC provider"

This is the single most important scoping finding in this section, because
"become a public OIDC IdP" reads as an enormous project and the required subset
is not.

**Skylize never performs an OIDC flow.** There is no browser, no user, no
consent, no client. Skylize *signs assertions about itself* and hands them to
Google. So the following - all of which a real OIDC Provider needs - are **out of
scope entirely**: authorization endpoint, token endpoint, userinfo endpoint,
dynamic client registration, refresh tokens, session management, front/back
channel logout, PKCE, nonce replay storage, consent UI.

What is in scope is **two static GET endpoints and a JWT signer**:

```
GET  {issuer}/.well-known/openid-configuration   -> JSON, public, cacheable
GET  {issuer}/jwks.json                          -> JSON, public, cacheable
```

plus an in-process signer that mints a short-lived JWT per outbound call. That is
the whole IdP. It is a genuinely large *trust boundary* change and a genuinely
small *implementation*. Both halves of that sentence should survive into the
implementation brief.

**Recommended discovery document** (modelled on how other WIF-consumed issuers
publish, and on OIDC Discovery 1.0's required fields, minus what cannot honestly
be claimed):

```json
{
  "issuer": "https://oidc.<skylize-domain>/w/<issuer_slug>/",
  "jwks_uri": "https://oidc.<skylize-domain>/w/<issuer_slug>/jwks.json",
  "subject_types_supported": ["public"],
  "response_types_supported": ["id_token"],
  "id_token_signing_alg_values_supported": ["ES256"],
  "scopes_supported": ["openid"],
  "claims_supported": [
    "iss", "sub", "aud", "exp", "iat", "jti",
    "skylize_org_id", "skylize_purpose", "skylize_env", "skylize_decision_id"
  ]
}
```

`authorization_endpoint` is deliberately **absent**. OIDC Discovery lists it as
required, but Skylize has no authorization endpoint and publishing a URL that
404s is worse than omitting the field. Google's documented need is the metadata
being discoverable and the JWKS being reachable; nothing in the WIF path
dereferences `authorization_endpoint`. **Flag:** if a future Google validation
tightens to full Discovery conformance this must be revisited; it is listed in
section 10.

### 2.3 Issuer scheme - RECOMMENDATION: per-tenant issuer, opaque slug

Three options considered.

**(a) One shared issuer, org identified only by a claim.**
`https://oidc.<domain>/` for every customer. Cheapest. Google permits it. The
cost is that every customer's pool provider trusts the same issuer, so the ONLY
thing separating tenants is the customer's own attribute condition on
`skylize_org_id` - and a customer who omits or mis-writes that condition accepts
tokens minted for any other tenant. Making one customer's security depend on
another customer's CEL expression is the wrong default for a governance product.
Rejected as the default; retained as the documented fallback per 0.2.

**(b) Per-tenant issuer keyed on `org_id`.**
`https://oidc.<domain>/orgs/{org_id}/`. Fixes (a). Costs almost nothing to serve
- it is one FastAPI route with a path parameter, not N deployments. But it puts
`org_id` into public URL space and, worse, makes the endpoint a **tenant-existence
oracle** if it 404s on unknown ids. Rejected in favour of (c), which costs one
column.

**(c) RECOMMENDED - per-tenant issuer keyed on an opaque `issuer_slug`.**

```
issuer    https://oidc.<skylize-domain>/w/{issuer_slug}/
discovery https://oidc.<skylize-domain>/w/{issuer_slug}/.well-known/openid-configuration
jwks      https://oidc.<skylize-domain>/w/{issuer_slug}/jwks.json
```

`issuer_slug` is a high-entropy random string (recommend 26 chars base32, ~128
bits) generated once at onboarding and stored on the new table. Properties:

- No `org_id` in public URL space, and no correlation between the public issuer
  and any tenant identifier.
- **No tenant-existence oracle**, for a reason that also removes a database
  problem - see 2.4.
- Stable for the life of the connection (the customer has pasted it into their
  IAM config; changing it breaks their provider), but re-issuable by
  re-onboarding if it is ever believed exposed.
- Is **not a secret**. Possession of the URL grants nothing; only the signing key
  mints tokens. It is an anti-enumeration measure. Documenting that distinction
  prevents a future session treating it as credential material and encrypting it.

**Google's URL-length note applies to the audience, not the issuer** (the default
audience URL should stay under 180 characters), but keep the issuer host short
anyway - the slug already costs 26 characters and the audience is the customer's
own resource name.

### 2.4 The routing decision that removes a cross-tenant read

This is the non-obvious payoff of 2.3(c) and it should not be lost.

The discovery and JWKS endpoints are **unauthenticated** and carry **no org
context** - Google fetches them with no credentials. A naive implementation looks
up the connection row by `issuer_slug` to serve them. That is a cross-tenant read
on a tenant-scoped table, and migration `0021` deliberately refused exactly such
a carve-out: granting one "just in case" on the table holding customer
credentials "is the opposite of fail-closed" (`0021:63-68`). Adding
`gcp_wif_connections` to `0002`'s carve-out list would reverse that precedent on
its first application.

**It is not necessary.** Every field in both documents is derivable from the slug
plus platform configuration:

- `issuer` = `{base}/w/{slug}/` - string concatenation.
- `jwks_uri` = `{base}/w/{slug}/jwks.json` - string concatenation.
- algorithms, subject types, claims - static.
- the key set - the platform keyring (section 4), identical for every tenant.

So **both endpoints serve correct documents with zero database access, for any
well-formed slug**, whether or not it exists. Three benefits at once: no RLS
carve-out, no tenant-existence oracle, and two endpoints that cannot be made to
fail by a database outage - which matters, because if Google cannot fetch the
JWKS the STS exchange fails with `invalid_grant` at precisely the urgent moment
(section 7).

**Forward compatibility.** If per-tenant signing keys are ever adopted
(section 4.3), `jwks.json` becomes slug-dependent and this property is lost. The
path out is a small non-RLS projection table mapping slug -> public JWK, which
holds no tenant data beyond that mapping - a far narrower carve-out than the
connection table. **Serve `jwks_uri` from the per-tenant path from day one**
(as above) even though it returns the platform key set, so that migration needs
zero customer reconfiguration. This costs nothing now and is the difference
between a key-isolation upgrade being a routing change and being a re-onboarding
of every customer.

### 2.5 Hosting the public surface - RECOMMENDATION: a separate hostname

The gateway today has exactly one genuinely public route, `/health`
(`edge/gateway.py:76-78`), and `dev_auth` defaults to `True` (`config.py:92`),
meaning a misconfigured deployment trusts `X-Dev-*` headers. Adding the first
externally-consumed public routes to that app deserves care.

Recommend serving the OIDC surface on a **separate hostname**
(`oidc.<domain>`), even if initially the same process:

- The issuer URL becomes independent of API gateway routing, CORS policy, and
  any future auth middleware default. A middleware added later that authenticates
  "everything except /health" cannot silently break Google's fetch.
- It can be fronted, cached, and rate-limited differently: these are two static,
  publicly cacheable documents with no tenant data, ideal for a CDN and hostile
  to the gateway's per-org `RateLimiter` (`edge/rate_limit.py`), which is
  org-keyed and has no org here.
- Set explicit `Cache-Control` on both. The discovery document is static; the
  JWKS changes only on rotation, and its cache lifetime interacts with the
  rotation procedure in 4.4 - shorter cache, faster safe rotation.
- Because both documents are DB-free (2.4) and secret-free, exposure risk is an
  availability question, not a confidentiality one. **Availability of this
  endpoint is on the critical path of the compute stop action** and belongs in
  the same monitoring tier as the API itself.

---

## 3. GA-4 - the new table

### 3.1 Shape

Proposed migration `0024` (**not written this pass**). Two tables: a connection
and its target allowlist.

```
gcp_wif_connections
  conn_id                        UUID PK DEFAULT gen_random_uuid()
  org_id                         TEXT NOT NULL REFERENCES tenants(org_id)
  label                          TEXT NOT NULL DEFAULT ''
  issuer_slug                    TEXT NOT NULL              -- UNIQUE, global
  gcp_project_id                 TEXT NOT NULL              -- human-facing, used in API URLs
  gcp_project_number             TEXT NOT NULL              -- used in the audience + principal
  workload_identity_pool_id      TEXT NOT NULL
  workload_identity_provider_id  TEXT NOT NULL
  audience                       TEXT NOT NULL              -- stored verbatim, not reassembled
  access_mode                    TEXT NOT NULL              -- 'direct' | 'impersonation'
  service_account_email          TEXT                       -- NULL iff access_mode='direct'
  connection_state               TEXT NOT NULL DEFAULT 'unverified'
  state_reason                   TEXT
  last_probe_at                  TIMESTAMPTZ
  last_probe_result              TEXT
  last_success_at                TIMESTAMPTZ
  created_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()

  CHECK (connection_state IN ('unverified','valid','revoked','misconfigured'))
  CHECK (access_mode IN ('direct','impersonation'))
  CHECK ((access_mode = 'direct') = (service_account_email IS NULL))

  UNIQUE (org_id, label)          -- mirrors 0021:107-110
  UNIQUE (issuer_slug)

gcp_wif_targets
  target_id      UUID PK DEFAULT gen_random_uuid()
  conn_id        UUID NOT NULL REFERENCES gcp_wif_connections(conn_id) ON DELETE CASCADE
  org_id         TEXT NOT NULL REFERENCES tenants(org_id)   -- denormalised for RLS
  gcp_project_id TEXT NOT NULL
  zone           TEXT NOT NULL
  instance_name  TEXT NOT NULL
  enabled        BOOLEAN NOT NULL DEFAULT true
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()

  UNIQUE (conn_id, gcp_project_id, zone, instance_name)
```

Both tables: `ENABLE` + `FORCE ROW LEVEL SECURITY`, a `tenant_isolation` policy
copied verbatim from `0021:128-136`, `GRANT SELECT,INSERT,UPDATE,DELETE` to
`skylize_app`, and **deliberate exclusion from `0002`'s cross-tenant carve-out**
- which section 2.4 makes costless.

`org_id` is denormalised onto `gcp_wif_targets` on purpose: RLS policies are
per-table and a policy that had to join to the parent to find `org_id` is both
slower and easier to get wrong. This mirrors how `0019`'s tenant tables carry
their own `org_id`.

### 3.2 Why no encrypted column and no `key_id`

**Nothing here is secret.** Every field is either a value the customer types into
their own IAM configuration (project, pool, provider, audience, service account)
or a value Skylize publishes (the slug, in a public URL). Encrypting it would
route non-secret configuration through a path built for secrets and would bury
values the dispatch path reads on every call inside ciphertext - the exact
mistake `0021:24-33` argues against for `expires_at`.

`key_id` (`0021:40-47`) exists to record which encryption key produced a row's
ciphertext. With no ciphertext there is nothing to record. Omitting it is not the
"cheap now, expensive later" trap that comment warns about, because the trap only
applies to a column that would later become necessary; this one never can.

The **signing** key's `kid` is a different thing entirely and lives in the JWT
header and the JWKS, not in this table (section 4.4).

### 3.3 `audience` stored verbatim, not reassembled

The audience *defaults* to
`//iam.googleapis.com/projects/{N}/locations/global/workloadIdentityPools/{POOL}/providers/{PROVIDER}`,
which is derivable from four other columns. But the customer may configure up to
10 custom allowed audiences (256 chars each), and Google's own best practice is
that the workload specifies the audience rather than assuming a static one. A
derived audience would silently diverge from a customer who configured a custom
one, and the failure would be an STS rejection at the urgent moment. Store what
the customer confirmed. The derivable columns stay because the principal
identifier and the API URLs need them independently.

### 3.4 `connection_state` - four values, and why not three

`oauth_credentials` has three (`valid` / `expired` / `revoked`, `0021:35-52`).
This table needs a different vocabulary because the failure modes are different,
and inventing a different set is a decision that has to earn itself. Each value
below earns it by mapping to a **different remedy**:

- **`unverified`** - configured, never successfully exchanged. Not the same as
  `valid`: onboarding is multi-step (section 9) and a customer will routinely
  save the connection before finishing the IAM bindings. Without this state, a
  half-onboarded connection would either look healthy or look revoked, and both
  are lies. *Remedy: finish onboarding.*
- **`valid`** - a probe or a live call exchanged successfully within the freshness
  window. *Remedy: none.*
- **`revoked`** - an **unambiguous** signal that the trust relationship is gone:
  pool or provider deleted or disabled. *Remedy: the customer re-creates the
  federation.*
- **`misconfigured`** - an **unambiguous** signal that the trust exists but this
  path cannot use it: audience mismatch, attribute condition no longer satisfied,
  IAM binding removed. *Remedy: the customer edits one setting.* Telling such a
  customer to "reconnect" would be actively wrong advice.

The alternative - collapse to three by folding `misconfigured` into `revoked` -
is cheaper and loses the single most likely real-world failure (a removed IAM
binding, section 7) into a remedy that does not fix it. Recommend four.

**The discipline from `oauth.py` transfers unchanged and is the important part:**
only an unambiguous signal may write a non-`valid` state. `_classify_failure`
"refuses to mark a grant dead on a transient network fault" (`0021:50-52`), and
`mark_revoked_by_provider`'s docstring makes the caller responsible for the same
asymmetry: "a 401 that could equally mean ... a transient auth blip must NOT be
reported through this method, because marking a grant revoked is destructive to
the customer" (`app/credentials/oauth.py:430-437`). Section 7 applies that to STS
by classifying every error whose meaning is not documented as **transient**,
never as revoked.

### 3.5 Detecting that the customer revoked - reactive, mirroring Notion

The Notion pattern is the right model and transfers almost exactly. Notion funnels
every API call through one `_check_response`
(`tools/builtin/notion_tools.py:400-462`) so "there is exactly one place where a
dead grant can be observed and exactly one place that records it. A new Notion
verb added later inherits the wiring by construction rather than by remembering
to copy it" (`:409-413`). It calls `mark_revoked_by_provider` **before** raising,
best-effort, and the write is "one-directional and eventual": this call still
fails, and the NEXT gated call reads the terminal state and denies with
`ToolCredentialReconnectRequired` (`:415-420`).

**Mirror it exactly**, with one funnel per external boundary - the STS exchange
and the Compute call are two boundaries with different error vocabularies and
each needs its own classifier - both writing through a
`mark_wif_state(org_id, label, state, reason, correlation_id)` primitive shaped
after `mark_revoked_by_provider` (`oauth.py:399-464`), including its idempotency
(a row already non-`valid` is left untouched and `False` returned, so a retrying
connector cannot spam the audit log).

### 3.6 Detecting it BEFORE the urgent moment - the proactive probe

This is the audit's sharpest finding (B.5, audit GA-6) and reactive detection
alone does not answer it:

> "A kill switch that discovers it has lost its authority at the instant of use
> is worse than no kill switch, because it was relied upon."

Under WIF this is structural, not incidental: there is no stored credential, so
there is nothing to go stale that anyone would notice. The customer can delete
the pool, disable the provider, or remove the IAM binding, and nothing in
Skylize changes until a call is attempted.

**RECOMMENDATION: a proactive probe worker.**

- **What it does, and this is the load-bearing detail:** mint an ID token,
  exchange it at STS, **and then make one read against the target** -
  `compute.instances.get` on an enabled target row. **An STS exchange alone is
  not sufficient.** Removing the IAM binding leaves the STS exchange succeeding
  and only the Compute call failing (section 7). A probe that stops at STS
  reports `valid` for a connection that cannot stop anything. That single fact
  should decide the probe's design.
- **What it must never do:** issue a stop, or any mutation. A health check that
  can stop a VM is a new incident class.
- **Cadence:** recommend hourly, aligned with the 1-hour STS ceiling. Not decided
  here (section 10); the trade is probe cost and log noise against staleness of
  the `valid` claim.
- **Where it runs:** a dedicated worker entrypoint following
  `skylize.decision_engine.worker`'s shape. It is emphatically **not** on the
  request path, so owner decision K3 (the request path must not import
  `skylize.decision_engine`, `dal/hitl.py:11`) is respected by construction - the
  probe imports neither engine.
- **RLS:** enumerate org ids from `tenants`, then run **each probe inside
  `tenant_session(org_id)`**. This needs **no cross-tenant carve-out** and
  therefore does not make the probe a second `outbox_poller` - which is today
  documented as "the ONLY component that reads decision_outbox without tenant
  RLS" (`decision_engine/outbox_poller.py:3`). Keeping that "only" true is worth
  the enumerate-then-scope shape.
- **Permission note:** `compute.instances.get` must be in whatever role the
  customer binds. `roles/compute.instanceAdmin.v1` contains it. A narrower custom
  role (section 8.1) must include it explicitly, or the probe fails and reports a
  healthy connection as broken.

**What happens to the governance posture when the probe fails** (the audit's
explicit sub-question). **RECOMMENDATION: alert and raise the tier for the
dependent actions; do NOT auto-engage the platform kill switch.**

Rationale: the compute stop protects the *customer's* cloud. Auto-killing
Skylize's agents because the customer edited their own IAM turns a customer-side
configuration change into a Skylize-side outage, and it is self-amplifying -
exactly the "pending -> approve -> fail -> pending loop" failure shape
`hitl/service.py:29-35` was written to eliminate, one layer up. The proportionate
response is to gate the agents whose overspend the net was protecting against, by
requiring HITL for them, and to make the degraded state loud.

The fail-closed variant ("if the net is gone, stop the agents that relied on it")
is a defensible opposite reading of the same safety argument and is **an owner
decision, listed in section 10**. Whichever is chosen, it must be *explicit
configuration*, not an emergent property.

---

## 4. GA-5 - the signing key

### 4.1 Type - RECOMMENDATION: ES256 (ECDSA P-256)

Google accepts `RS256` or `ES256` (verified 2026-09-04). Both are fine. Choose
ES256:

- `ECCService` already implements P-256 end to end - `Curve.P256`
  (`security/ecc_service.py:49`), `_CURVE_MAP` (`:55`), `_HASH_MAP` SHA-256
  (`:60-63`), PKCS8 PEM serialise (`:80-91`), PEM load (`:195`). RS256 would
  introduce the platform's first RSA key into a codebase that has none, with new
  generation, serialisation, and validation code for no benefit.
- One crypto family across the platform, one set of review knowledge.
- Smaller keys and signatures; irrelevant at this volume but not a negative.
- No new dependency either way: `python-jose[cryptography]` (`pyproject.toml:27`)
  already signs.

**It is a different key on a different curve from the governance key, and both
halves matter.** Governance is P-384/ES384 (`app/governance/keys.py:71-76`);
this is P-256/ES256. Beyond the trust-domain argument the owner already decided
on, **ES384 is not in Google's accepted algorithm set at all** (section 0.3), so
reuse is foreclosed technically as well as by policy. Two independent reasons is
the right number for a decision a future session will be tempted to undo.

`ECCService.sign_governance_token` (`ecc_service.py:406`) must **not** be reused:
it is governance-token framing, not a JWS. Sign with `jose.jwt.encode`, which
already sits in the dependency set.

### 4.2 Generation and custody - the established pattern, third instance

Follow `scripts/gen_governance_key.py` exactly:

- **Generate offline**, in a new `scripts/gen_wif_signing_key.py`: prints a PKCS8
  PEM to stdout, optional `--password`, "Do NOT commit the output. Store it in
  your secrets manager."
- **Transport** as `SKYLIZE_WIF_SIGNING_KEY_PEM` on `Settings`, injected from the
  secrets manager - identical to `governance_signing_key_pem` (`config.py:85-86`).
- **Resolve at boot** in a `resolve_wif_signing_keys(settings)` in `bootstrap.py`
  mirroring `load_signing_key` (`app/governance/keys.py:33-67`) and
  `resolve_credential_encryption_key` (`bootstrap.py:91-149`):
  - key present -> parse eagerly, **assert curve is P-256** (a P-384 PEM here is
    a configuration mistake that must fail the boot, not produce tokens Google
    silently rejects at the urgent moment);
  - absent and `backend != "memory"` -> **refuse to start**, with a message
    naming the variable and the generation command;
  - absent and `backend == "memory"` -> ephemeral, logged loudly.
- **No key material this pass.** Nothing is generated here.

**A boot-time refusal specific to this feature.** Unlike Drive/Asana/Notion,
where "both empty = provider not registered, fail closed at the ToolProxy OAuth
stage" (`config.py:133-138`), the correct posture here is:

> if any `gcp_wif_connections` row exists and no WIF signing key is configured,
> **refuse to start**.

A deployment holding live customer federation trusts but unable to sign is a
kill-switch that is silently dead. That is precisely the class of failure
`resolve_credential_encryption_key` exists to prevent -
"a corruption message for what was really a configuration mistake made one
restart earlier" (`bootstrap.py:104-108`).

### 4.3 One platform keyring, not per-tenant keys - and what that costs

**RECOMMENDATION: one platform key set, shared by all tenants.** Per-tenant
issuers, per-tenant audiences, per-tenant claims - one key.

The honest cost, stated plainly because section 5.4 depends on it: **under a
shared keyring, compromise of the signing key is a cross-tenant compromise.** An
attacker with the key can mint a token bearing any tenant's claims. Per-tenant
issuers do not fix that (they raise the bar to also needing the victim's slug);
only per-tenant keys would.

Per-tenant keys are rejected **for now**, not in principle: N private keys is N
custody problems, N rotations, and N boot-time validations, against a Tier 0
customer count in the low single digits. What makes deferring safe is the routing
decision in 2.4 - `jwks_uri` is already per-tenant in the published discovery
document, so adopting per-tenant keys later is a server-side change with **zero
customer reconfiguration**. Take that option now; it is free.

The containment that actually bounds a key compromise is the customer's own IAM
binding: a forged token can only stop the instances the customer bound
(section 5.4).

### 4.4 Rotation - where the existing pattern must be extended

The governance key has no rotation mechanism and does not need one (5-minute
tokens, internal verification only). A published JWKS is different in three ways:

1. Google caches the JWKS; the cache duration is **undocumented** (checked
   2026-09-04).
2. A customer may have **uploaded** the key set into their provider
   (`--jwk-json-path`), in which case Skylize rotating its key **breaks that
   customer until they re-upload**. Uploaded key sets **replace** rather than
   merge, and are capped at 8 keys.
3. Tokens already minted stay valid until `exp`.

Therefore:

- **Every ID token carries a `kid` header**, and the JWKS publishes matching
  `kid`s. The governance path has no `kid` concept; this is new.
- **The keyring holds two slots**: `SKYLIZE_WIF_SIGNING_KEY_PEM` (active, signs)
  and `SKYLIZE_WIF_SIGNING_KEY_PEM_NEXT` (published, does not sign). Both appear
  in the JWKS whenever `NEXT` is set.
- **Rotation procedure**, to be written as a runbook:
  1. Generate a new key offline. Set it as `NEXT`. Deploy. The JWKS now serves 2
     keys; nothing has changed about signing.
  2. Wait beyond the JWKS cache lifetime plus margin. Because the duration is
     undocumented, this wait is a **guess until measured**; measure it once
     against a real provider and record the number (section 10).
  3. Promote `NEXT` to active. Deploy. New tokens carry the new `kid`.
  4. Wait beyond the maximum ID-token lifetime (5 minutes by section 5.1's
     recommendation, 60 minutes worst case) so no in-flight token references the
     old key.
  5. Remove the old key. Deploy. The JWKS serves 1 key again.
- **RECOMMENDATION: tell customers to configure `--issuer-uri` only, never
  `--jwk-json-path`.** A served JWKS makes rotation a Skylize-side operation; an
  uploaded one makes it a customer-side operation that must be coordinated with
  every customer simultaneously. Document the upload path only as a fallback for
  a customer whose egress policy forbids Google fetching Skylize's endpoint,
  with an explicit warning that they must re-upload on every rotation - and
  record which customers chose it, because they become a rotation blocker.
- **Emergency rotation** (key believed compromised) has no clean fast path: step
  2's wait cannot be skipped safely, so a forced immediate rotation will break
  exchanges until Google's caches expire. The faster containment for a suspected
  compromise is not rotation at all - it is asking customers to **disable their
  provider** (which they can do independently of the pool, verified 2026-09-04),
  because that revokes the trust from their side immediately. Put that in the
  incident runbook, not the rotation runbook.

---

## 5. GA-6 - the customer's trust policy, and what Skylize hands them

### 5.1 The ID token Skylize mints

| Claim | Value | Why |
|---|---|---|
| `iss` | `https://oidc.<domain>/w/{issuer_slug}/` | must exactly match the customer's `--issuer-uri` |
| `sub` | `org:{org_id}:killswitch` | stable; the customer binds IAM to it. **Must be <= 127 bytes** - a longer value is a documented STS error, so validate `org_id` length at onboarding, not at the urgent moment |
| `aud` | the `audience` column, verbatim | per-customer, not static, per Google's best practice |
| `exp` | **`iat` + 5 minutes** | see below |
| `iat`, `nbf`, `jti` | standard; small `nbf` leeway for clock skew | |
| `skylize_org_id` | the tenant | the claim the customer's condition pins |
| `skylize_purpose` | `killswitch.compute.stop` | a constant naming what this token may be used for |
| `skylize_env` | `production` / `staging` | prevents a staging incident reaching production |
| `skylize_decision_id` | the governing decision's id | context; see 5.3 |

**5 minutes, not 60.** Google's ceiling is 60 and the STS token inherits the input
token's `exp` (capped at 1 hour). Five minutes matches `token_ttl_minutes`
(`config.py:89-90`), is ample for one API call plus operation polling, and
minimises the window in which a leaked token is useful. The trade is clock skew,
handled by `nbf` leeway. If operation polling ever needs longer than the token
lives, poll with a token minted for the poll, not by widening the mint.

### 5.2 What the customer configures - attribute mapping and condition

Attribute mapping (required by Google; CEL; <= 50 custom attributes, expressions
<= 2048 chars, evaluated attributes <= 8KB total):

```
google.subject=assertion.sub
attribute.skylize_org_id=assertion.skylize_org_id
attribute.skylize_purpose=assertion.skylize_purpose
attribute.skylize_decision_id=assertion.skylize_decision_id
```

Attribute condition (optional to Google, **mandatory in Skylize's onboarding
documentation**; CEL, <= 4096 chars):

```
assertion.skylize_org_id == "ORG_ID" &&
assertion.skylize_purpose == "killswitch.compute.stop" &&
assertion.skylize_env == "production"
```

**Answering the brief's question directly - "how narrow can the trust condition
be made, e.g. can it be scoped to only accept tokens for a specific org_id
claim?"** Yes to the mechanism; see 5.4 for what it is actually worth.

### 5.3 The decision id must be an attribute, never the subject

Google recommends including context claims so customers can use them in
conditions and principal identifiers. Mapping `skylize_decision_id` into an
attribute means **the customer's own Cloud Audit Log records which Skylize
governance decision authorised the action**. That is the audit-provenance story
of the "CFO Test" (`docs/09_development/audit_feed_endpoint.md:43-44`) extended
across the trust boundary into the customer's own logs, and it is the strongest
product argument in this design.

**It must be a custom attribute, not `google.subject`.** Putting a per-call
decision id in the subject makes every call a different principal, which makes
the identity unbindable - `principal://.../subject/{X}` cannot be granted a role
for an unbounded set of X. The binding must be on a **stable** identity; the
varying value rides alongside as an attribute. Getting this backwards produces a
design that passes review and cannot be granted IAM.

### 5.4 What the narrowing actually buys - stated honestly

Three layers, in increasing order of how much they are worth:

**Layer 1 - the attribute condition.** Google evaluates it against the claims
*as asserted*. If Skylize's signing key is stolen, the attacker mints whatever
claims they like, including a victim's `skylize_org_id`. So:

> The attribute condition is a defense the customer controls against **Skylize's
> bugs** - a mis-populated claim, a crossed-wires tenant lookup, a staging build
> pointed at production. It is **not** a defense against Skylize's compromise.

Say that to customers. It is still worth configuring - Skylize's bugs are far
likelier than Skylize's key theft - but selling it as compromise containment
would be false.

**Layer 2 - the per-tenant issuer.** The customer's provider is pinned to one
issuer URI containing an unguessable slug. Key-only compromise is therefore
contained: the attacker cannot target a customer whose slug they do not know.
Compromise of the key **and** the database defeats it, since the slugs are stored
there. Real, partial, worth having, not sufficient.

**Layer 3 - the customer's IAM binding. This is the containment that matters.**
Even a perfectly forged token, accepted by a perfectly bypassed condition, can
only do what the binding permits: stop the specific instances the customer bound
the role on. Nothing else in the project, nothing in any other project, nothing
billing-related. **The blast radius is bounded by the customer's own IAM, not by
Skylize's correctness** - which is exactly the property that makes GA-1 (act on
the customer's real project) defensible, and exactly why GA-2's resource-level
scoping is not a nicety.

Onboarding documentation should present it in that order, because a security team
that reads layer 3 first will approve the layers above it far more easily.

### 5.5 Direct resource access vs service-account impersonation

**RECOMMENDATION: direct resource access** - grant the role to the federated
principal on the instance, with no intermediate service account. Fewer identities,
no `roles/iam.workloadIdentityUser` binding, no service account whose keys could
later be created by someone else, and Google itself recommends providing "access
directly to a Google Cloud resource".

**UNVERIFIED and gating:** Google states "most Google Cloud APIs support Workload
Identity Federation, some APIs have limitations" without enumerating them, and
nothing fetched on 2026-09-04 confirms `compute.instances.stop` works with a
direct federated principal. **This must be tested empirically before the
onboarding documentation is written**, because it is the difference between a
2-step and a 4-step customer setup.

**This uncertainty is why `access_mode` is a column** (section 3.1) rather than an
assumption. If direct access turns out to be unsupported, the impersonation path
is a configuration change plus a service-account email, not a redesign. Designing
the escape hatch before knowing whether it is needed is cheap here and expensive
later.

The binding, direct form, on the instance resource:

```
principal://iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/
  workloadIdentityPools/{POOL_ID}/subject/org:{ORG_ID}:killswitch
```

`principal://` by subject is narrower than `principalSet://` by attribute and the
subject is stable by design (5.1), so prefer it. Resource-level IAM on an
individual VM instance is supported (`compute.instances.setIamPolicy`), and IAM
Conditions can be attached to such a binding - so a customer wanting belt and
braces can add a condition as well, though binding at the instance already
achieves the scoping.

---

## 6. GA-7 - the trigger path

### 6.1 The inversion that makes it buildable

Section 1.4 established that a `rejected` verdict terminates a proposal and has
no side-effect channel. So the compute stop cannot be a *consequence* of a
hard-deny. It is **itself a proposal that goes through the same gate**:

```
detection            cfo_agent / an alert / an operator produces a finding.
                     Stateless agents propose. They never hold the credential
                     and never call the tool. (section 1.5)
                            |
                            v
proposal             POST /api/v1/agents/execute
                       agent_id = "infrastructure_executor"
                       input    = {project_id, zone, instance_name,
                                   reason, decision_ref}
                            |
                            v
gate                 AgentExecutionService._govern (execution.py:515)
                       rejected           -> 403, nothing stops       [6.2]
                       deferred_to_human  -> hitl_queue row, 202      [6.3]
                       approved           -> straight through
                            |
                            v
human verdict        POST /api/v1/hitl/{id}/approve
                       exactly-once claim (hitl/service.py:170-178)
                       replay envelope validated
                       execute() with HitlApprovalContext - gate not re-run
                            |
                            v
governed dispatch    mint governance token scoped to the contract
                     ToolProxy.invoke("integration.gcp_stop_instance")
                       (execution.py:949 - the only call site in the app)
                       validate_tool_call: signature, expiry, revocation,
                                           scope, live-state            [6.6]
                       WIF stage:        connection_state + target allowlist [6.4]
                       permission stage: n/a  (see 6.4)
                       spend stage:      n/a  (stopping a VM costs Skylize nothing)
                            |
                            v
handler              mint ID token (5 min, kid, tenant claims)
                     POST https://sts.googleapis.com/v1/token
                       grantType=urn:ietf:params:oauth:grant-type:token-exchange
                       requestedTokenType=...:access_token
                       subjectTokenType=urn:ietf:params:oauth:token-type:jwt
                       audience=<stored audience>
                       scope=https://www.googleapis.com/auth/cloud-platform
                     POST .../instances/{i}/stop?requestId={derived}     [6.5]
                     poll the returned Operation to a bounded deadline
                            |
                            v
record               tool.invoked audit row (tools/proxy.py:14-17)
                     deliverable states the actual outcome
                     customer's Cloud Audit Log carries the federated
                     principal + attribute.skylize_decision_id           [5.3]
```

This satisfies Q2.2c's actual requirement - one governed path, never a
connector-owned second mechanism - while being implementable against the code
that exists. It reuses the fourth mechanism rather than inventing a fifth.

### 6.2 A denied stop must be loud

`rejected` here means the governance system refused to let the safety action
happen. That is not an ordinary 403. It should be alerted on the same footing as
a probe failure (section 3.6), because the operator's mental model is "the
kill-switch will fire" and the system has just decided it will not.

### 6.3 Default posture - RECOMMENDATION: deferred_to_human, not approved

Recommend the executor contract and the org's policy be configured so a compute
stop **defers to a human by default**, with autonomous approval off unless an org
explicitly opts in. Reasons: the action touches the customer's production; it is
reversible but disruptive; and the deferral path already carries a Slack notifier
(`SlackApprovalNotifier`, wired via `resolve_slack_notifier_config`,
`bootstrap.py:152+`) so the human is reachable in seconds rather than discovering
a queue item later.

The counter-argument is real and should be recorded: an overspend happening
*right now* is exactly when a human in the loop costs money. The answer is that a
VM stop does not stop the bill anyway (section 0.5), so the marginal cost of a
few minutes of human latency is bounded and small, while the cost of an
unreviewed automated stop of the wrong production instance is not.

### 6.4 The ToolProxy gate - RECOMMENDATION: a fourth declarative profile

`ToolOAuthProfile` gates a tool on a live OAuth grant (`tools/base.py:124-141`);
WIF is not an OAuth grant and must not be forced into it for the same reasons the
table is separate. Two options:

**(a) Resolve WIF inside the handler.** Cheapest. Rejected: it puts the
credential-state check somewhere that is neither inspectable in a registry entry
nor deny-by-default. The entire argument for the existing three profiles is that
a gate must be visible in the registry - all three "refuse callable predicates on
purpose, so a gate stays inspectable" (`tools/base.py:78-81,107-110`).

**(b) RECOMMENDED - a fourth opt-in profile, `ToolWifProfile`**, declared in the
registry entry, checked in `ToolProxy.invoke` in the same slot the OAuth stage
occupies and for the same documented reason ("a dead or unrefreshable credential
IS such a denial" and must be found before a spend hold is placed,
`tools/proxy.py:230-241`). It checks:

1. a `gcp_wif_connections` row exists for the org -> else a `GrantNotConnected`
   analogue;
2. `connection_state == 'valid'` -> else `ToolCredentialReconnectRequired` for
   `revoked`, and a distinct misconfiguration error for `misconfigured`, so the
   message names the right remedy (section 3.4);
3. the requested `(project_id, zone, instance_name)` is an **enabled row in
   `gcp_wif_targets`** -> else denied.

The profile is additive and opt-in exactly like the other three, so the
deny-by-default chokepoint the existing tests pin keeps its shape - but it does
touch `ToolProxy.invoke`'s gate chain, which is the most heavily-pinned code in
the tool path. That is the main implementation risk in this design and should be
scoped as such.

**On `ToolPermissionProfile`: recommend NOT using it here.** Its fields are
`action_class`, `grantee_field`, `role_field` - shaped for "who is the agent
handing this data to". The question here is "which instance", and mapping
`grantee_field="instance_name"` is a semantic stretch that would mislead every
future reader of `org_permission_grants`. More importantly, using both would
create **two allowlists for the same question** (`gcp_wif_targets` and
`org_permission_grants`), and two allowlists drift. Keep one: the child table,
checked at the WIF stage. If the owner wants a separate org-admin-managed
approval layer, that is a deliberate second control and should be designed as
one, not acquired by accident.

**The tripwire, stated so it is a conscious edit and not a red build:**
`tests/contract/test_stateless_agents_no_oauth_access.py` asserts against the
fully wired registry that no stateless agent's tools carry an OAuth or permission
profile (`:107-120`). A new profile type needs a matching
`EXPECTED_WIF_TOOL_IDS = {"integration.gcp_stop_instance"}` and a third assertion
in that test, so the invariant covers the new gate rather than silently ignoring
it. Adding the set without the assertion would leave the strongest guard in the
repo half-applied.

### 6.5 Idempotency - the concrete requirement

From section 0.4: **derive `requestId` deterministically from `hitl_id` (or
`decision_id`), never from `correlation_id`**, because the replay path mints a
fresh correlation id per attempt (`hitl/service.py:160`) and a per-attempt
`requestId` defeats Google's deduplication precisely on the retry path it exists
to protect.

Recommend `requestId = uuid5(SKYLIZE_NAMESPACE, f"{hitl_id}:{project}:{zone}:{instance}")`
- stable across replays of the same approved decision, distinct across different
decisions and different targets, and a valid non-zero UUID as Google's request-ID
convention requires.

Note the dedup window is documented as "at least 60 minutes" in Google's general
request-ID convention; a human approving a retry hours later will legitimately
re-issue. That is correct behaviour - it is a fresh decision to stop an instance
that should already be stopped, and it converges.

**UNVERIFIED:** whether `instances.stop` on an already-`TERMINATED` instance
returns a successful no-op Operation or an error. The stop/start guide does not
address it (checked 2026-09-04). The handler must treat "already stopped" as
**success** - the desired state is reached - whichever shape Google returns, and
the exact shape must be characterised empirically (section 10).

### 6.6 The platform kill switch as an interlock - and what nobody can promise

The audit's GA-7 extension asks: given the existing kill switch stops only
Skylize's agents, what stops an in-flight action against a *customer's*
production?

**The honest answer is that nothing recalls an issued `instances.stop`.** No
design makes that untrue. What the design does provide:

1. **The platform kill switch is an interlock BEFORE dispatch, and it composes
   correctly.** Engaging it revokes governance tokens in scope
   (`app/governance/authority.py:619-649`), and `validate_tool_call` rejects at
   the revocation / live-state step (`kill_switch_protocol.md:18-27`) - which
   `ToolProxy` runs before any of the three (soon four) gates
   (`tools/proxy.py`). So engaging the platform kill switch makes the compute
   stop tool uninvokable. It fails in the safe direction: **Skylize stops
   touching customer infrastructure.**
2. **The exposure window is one HTTP call**, between the gate passing and the
   Compute API accepting.
3. **The action is reversible.** `instances.start` restores the instance; disks,
   IPs, and metadata are preserved.
4. **Blast radius is bounded by the customer's IAM binding** (section 5.4).

**Documentation gap this design creates and should close:**
`docs/04_decision_engine/kill_switch_protocol.md` states invariants "human can
always stop, kill overrides all authority, never self-clears, fully audited"
(`:117-119`) written entirely about internal agents. Once an external action
exists it must say explicitly that its guarantee is *"Skylize stops acting,
including on customer infrastructure"* and that it makes **no** guarantee about
undoing completed external actions. Leaving that unsaid invites exactly the
over-reading the audit warns about (A.4: the two are "different products with
different failure modes"). That amendment is a separate pass and is listed in
section 10.

---

## 7. GA-8 - failure modes

### 7.1 The requirement

Failure must surface as a visible, actionable state rather than a silence
discovered at the urgent moment. Three surfaces, in order of when they help:

1. **The probe (section 3.6)** - state is fresh *before* the urgent moment. This
   is the one Notion lacks and the audit calls the sharpest gap.
2. **`connection_state` + `state_reason`**, read by the WIF stage, so the next
   call denies with the *right remedy* named (Notion's one-directional eventual
   pattern, reused).
3. **A read surface and an alert on transition.** Note honestly:
   `edge/routes/credentials.py` exposes **no** `connection_state` read today for
   any provider (verified at HEAD), so "visible in the product" needs a route
   that does not exist for OAuth either. That is scope this design adds, not
   scope it inherits.

### 7.2 The failure-mode table

Classification discipline, inherited from `oauth.py` and non-negotiable: **only an
unambiguous signal writes a non-`valid` state.** Anything ambiguous is transient,
because marking a connection dead is destructive to the customer
(`app/credentials/oauth.py:430-437`).

| # | What breaks | Where detected | Signal | Class | State written | What the operator/customer sees |
|---|---|---|---|---|---|---|
| 1 | Customer deleted the pool (30-day soft delete) | STS | `invalid_grant` / not-found | unambiguous | `revoked` | "GCP federation removed - re-run onboarding" |
| 2 | Customer deleted the provider (30-day soft delete) | STS | as above | unambiguous | `revoked` | as above |
| 3 | Customer **disabled** pool or provider | STS | **UNVERIFIED error shape** | treat as ambiguous until characterised | `misconfigured` | "GCP federation disabled or unusable - check pool/provider" |
| 4 | **Customer removed the IAM binding** | **Compute, not STS** | Compute `403` | unambiguous | `misconfigured` | "Skylize can federate but cannot stop the instance - re-add the role binding" |
| 5 | Attribute condition edited so tokens no longer satisfy it | STS | denial, **exact string UNVERIFIED** | ambiguous until characterised | `misconfigured` | "GCP rejected the identity - check the attribute condition" |
| 6 | Audience mismatch (pool re-created, new project number) | STS | **UNVERIFIED**, likely `invalid_request`/`unauthorized_client` | ambiguous until characterised | `misconfigured` | "Audience mismatch - re-copy the provider resource name" |
| 7 | Skylize's JWKS/discovery unreachable to Google | STS | `invalid_grant`, "Error connecting to the given credential's issuer" (documented verbatim) | **Skylize-side fault** | **none** | Skylize alert. **Must never prompt the customer to reconnect** |
| 8 | Clock skew, `exp` already past | STS | `invalid_grant` | Skylize-side | none | Skylize alert |
| 9 | Key rotated faster than Google's cache | STS | `invalid_grant` | Skylize-side; rotation runbook violated | none | Skylize alert naming the rotation step |
| 10 | STS throttling | STS | `quota_exceeded`, HTTP 429 (documented) | transient | none | retry with backoff; alert on sustained |
| 11 | `google.subject` > 127 bytes | STS | `invalid_request`, documented message | config error, **catchable at onboarding** | `misconfigured` | validate `org_id` length at onboarding instead |
| 12 | Instance already `TERMINATED` | Compute | **UNVERIFIED** | **treat as success** | `valid` | "already stopped" in the deliverable |
| 13 | Instance deleted, or wrong zone | Compute | `404` | stale **allowlist**, not a trust failure | connection stays `valid`; flag the `gcp_wif_targets` row | "target no longer exists - remove or correct it" |
| 14 | No connection row for the org | WIF stage | local | n/a | n/a | "GCP is not connected" - denied before anything is minted |
| 15 | `connection_state != 'valid'` at dispatch | WIF stage | local | n/a | unchanged | denied with the remedy for that state |
| 16 | Target not in the allowlist | WIF stage | local | n/a | unchanged | "that instance is not authorised for stop" |
| 17 | No probe success within the freshness window | probe | staleness | n/a | `unverified` after a threshold | degraded banner + alert; **does not** auto-kill (section 3.6) |
| 18 | Skylize WIF signing key absent in production | boot | local | fatal | n/a | **process refuses to start** (section 4.2) |

**Row 4 is the row that shapes the probe.** It is the likeliest real failure - IAM
cleanup is routine, deleting a federation pool is not - and it is invisible to
STS. A probe that stops at the token exchange reports `valid` for a connection
that cannot do the one thing it exists for.

**Rows 3, 5, 6 and 12 are UNVERIFIED error shapes.** Google's troubleshooting page
(fetched 2026-09-04) documents only: `invalid_grant` for issuer/JWKS
unreachability, `invalid_request` for oversized subject, `quota_exceeded`/429,
invalid JWK format, and a 401 for using federated tokens with unsupported
services. It does **not** document disabled pools, failed attribute conditions,
or audience mismatch. Until those are characterised empirically, the classifier
must default them to `misconfigured` or transient - **never `revoked`** - because
a wrong `revoked` sends the customer to re-do their entire federation when the
fix was one CEL expression.

### 7.3 The urgent-moment sequence, stated end to end

An agent is overspending, an operator triggers a stop, and the trust is dead:

1. The WIF stage reads `connection_state`. If the probe already caught it
   (rows 1-6), dispatch is **denied before any token is minted**, with a message
   naming the remedy. Time to a truthful answer: milliseconds.
2. If the probe has not yet run, the handler attempts the exchange, the classifier
   writes state, and the call fails with a message naming the remedy. The
   operator learns the truth in one round trip rather than from silence.
3. Either way the failure is a **loud, typed, actionable denial** - never a
   success that did nothing. That is the whole requirement.

---

## 8. GA-9 - what the audit left open that sections 2-7 do not cover

Per section 0.1, the brief's GA-9 is a catch-all while the audit's GA-9 is a
specific evidence gap. Both are covered, plus the two orphaned audit questions.

### 8.1 Audit GA-3 - the resource-and-verb allowlist (orphaned by the brief)

Q2.2b's demand stands: "an allowlist of resource types **and** verbs is required;
'cloud access' is not a specification."

**Resources:** the enabled rows of `gcp_wif_targets` - explicit
`(project, zone, instance)` triples. Never a project, never a wildcard, never a
label selector. Binding is **resource-level, on the instance**, which is
supported (`compute.instances.setIamPolicy`) and can carry an IAM Condition as
well.

**Verbs, the complete set this design needs:**

| Permission | Why |
|---|---|
| `compute.instances.stop` | the action |
| `compute.instances.get` | the probe (section 3.6) - **without this the probe cannot detect row 4** |
| `compute.zoneOperations.get` | polling the returned Operation to a real outcome |

**A factual note on the granted role, not a re-litigation of GA-2.**
GA-2 names `roles/compute.instanceAdmin.v1`, and Google confirms it contains the
required stop permission. It is a **full instance-administration role**: bound on
an instance it also confers administrative verbs on that instance -
notably delete and reset - which is materially broader than "VM-stop only". A
**custom role** containing exactly the three permissions above, bound at the
instance, implements GA-2's stated intent more faithfully than the predefined
role does, at the cost of the customer creating one custom role during
onboarding.

Recommend offering both in the onboarding path: the predefined role as the
one-command default, the custom role as the least-privilege option, and let the
customer's security team choose. Verify the exact permission list of
`roles/compute.instanceAdmin.v1` against the live role reference before writing
that documentation - it was not enumerated verbatim in what was fetched on
2026-09-04.

### 8.2 Audit GA-8 - which spend concept triggers this (orphaned, STILL OPEN)

Unresolved, and deliberately so.

`org_spend_ceiling` (migration 0014) is **Skylize's own LLM spend** in micro-USD.
`budget_ledger` is business spend and is explicitly unfed in production
(`0014:7-12`). `ai_cost_ledger` is the money value of consumed LLM tokens.
ADR-0006 forbids conflating them. **A customer's GCP bill is a fourth concept
that does not exist in this repository.**

This design does not create it and does not pick one. **The trigger is
source-agnostic by construction:** the executor contract's input carries a
`reason` and a `decision_ref`, and whatever produced the finding - a cfo_agent
observation, a GCP budget notification webhook, an operator - is out of scope.
That keeps ADR-0006 intact and keeps this design useful under any of the possible
answers.

Anyone answering GA-8 later should read section 0.5 first: **stopping a VM does
not stop the bill.** A design that feeds GCP billing data into a ledger to trigger
a VM stop is coupling a spend signal to an action that does not control spend.
That may still be the right product - blast-radius control is valuable - but it
should be chosen with that fact in view.

### 8.3 Audit GA-9 - the missing design specification (STILL MISSING, re-verified)

`Skylize_Design_Specification.md` remains absent from the repository at
`628671f`: `git ls-files | grep -iE "specification|Skylize_"` returns nothing
(re-run this pass).

The audit's Gate 1 conclusion - that no artifact in this repo defines a GCP
kill-switch action - therefore still rests on an absence, and GA-2's VM-stop
decision now supplies the answer by owner fiat rather than by document. That is
legitimate, but the decision should be written into
`docs/06_integrations/integration_inputs.md` section 2.2 (currently
`[OWNER-DECISION-REQUIRED]`, HARD BLOCK, unsigned at `:1115`) so it stops being
carried between sessions in prompts.

### 8.4 New questions this pass surfaces

- **DNS, TLS, and hosting** for `oidc.<domain>` (section 2.5). It is on the
  critical path of the compute stop and needs the same monitoring tier as the
  API.
- **Rate limiting and abuse handling** for two unauthenticated public endpoints.
  The existing `RateLimiter` is org-keyed and there is no org here.
- **Google's JWKS cache lifetime** is undocumented, so step 2 of the rotation
  procedure is a guess until measured once (section 4.4).
- **Whether Skylize should hold `instances.start` at all.**
  `roles/compute.instanceAdmin.v1` grants it; the custom role need not. Not
  holding it means the customer restarts manually - arguably safer (no automated
  flip-flop, and the restart is a human decision) but it makes recovery depend on
  the customer's availability. Not decided here.
- **`kill_switch_protocol.md` needs the amendment in section 6.6.**
- **Which decision tier a compute stop proposal maps to** in the evaluator's
  policy inputs. Section 6.3 recommends the posture (defer to human) but the
  policy expression of it is a decision-engine change, not designed here.

---

## 9. Customer onboarding sketch

What the customer does, in order. Steps 1-2 are Skylize-side; 3-6 are the
customer's; 7 is joint.

**1. Skylize creates the connection row.** Generates `issuer_slug`, sets
`connection_state = 'unverified'`. Nothing is trusted yet and nothing can be
stopped.

**2. Skylize hands the customer a copy-paste block.** Concretely:

```
Issuer URI      https://oidc.<skylize-domain>/w/<issuer_slug>/
Subject         org:<ORG_ID>:killswitch
Purpose claim   killswitch.compute.stop
Signing alg     ES256
Token lifetime  5 minutes
```

**3. Customer creates a workload identity pool** in their project.

**4. Customer creates an OIDC provider in that pool**, with:
- `--issuer-uri` set to the value above (**and no `--jwk-json-path`** - see 4.4);
- the attribute mapping from 5.2;
- the attribute condition from 5.2, with their own `ORG_ID` substituted.

**5. Customer chooses a role.** Either `roles/compute.instanceAdmin.v1` (one
command) or a custom role with the three permissions in 8.1 (least privilege).

**6. Customer binds it, per instance**, to the `principal://` identifier from
5.5, on each instance they want Skylize to be able to stop. Never at project
level.

**7. Customer returns four values to Skylize** - project id, project number, pool
id, provider id - plus the instance list. Skylize stores them, and **runs the
probe immediately**: STS exchange **plus** a `compute.instances.get` on each
target (section 3.6). On success the row flips to `valid` and the connection is
live. On failure it stays `unverified` with a `state_reason` naming the step that
failed - which is the difference between onboarding that debugs itself and
onboarding that generates a support ticket.

**Ordering note that matters:** the probe at step 7 is the *only* thing that
proves steps 3-6 were done correctly. It must be part of onboarding, not a
background job that eventually notices - otherwise a customer completes setup,
believes they are protected, and finds out at the urgent moment. That is the same
failure the audit named, arriving through the front door.

**Sequencing note:** step 5's choice determines whether the probe's
`compute.instances.get` will work. A custom role missing `get` produces a
connection that can stop but cannot be health-checked - which looks like row 4 in
the failure table and is not. Document the three permissions as an inseparable
set.

---

## 10. What this pass does NOT decide

Genuinely open after this design. Everything here is either an owner decision or
an empirical question that documentation could not answer on 2026-09-04.

**Must be answered empirically before the connector is written:**

1. Whether **direct resource access** works for `compute.instances.stop` with a
   federated principal, or whether service-account impersonation is required
   (section 5.5). `access_mode` exists so either answer is a config change.
2. The exact **STS error shapes** for: a disabled pool, a disabled provider, a
   failed attribute condition, and an audience mismatch (rows 3, 5, 6). Until
   characterised, classify as `misconfigured` or transient, **never `revoked`**.
3. Whether **`instances.stop` on an already-`TERMINATED` instance** is a
   successful no-op or an error (row 12). Handler must treat it as success either
   way; the code shape depends on the answer.
4. **Google's JWKS cache lifetime** - determines the wait in rotation step 2
   (section 4.4).
5. The verbatim **Compute Engine `requestId`** semantics. Google's general
   request-ID convention (dedup >= 60 minutes, non-zero UUID) is assumed; the
   Compute v1 `instances.stop` reference page did not render through the fetch
   tool this pass, so the Compute-specific wording is **UNVERIFIED**.
6. The exact permission list of **`roles/compute.instanceAdmin.v1`** (section 8.1).

**Owner decisions:**

7. **Which spend signal triggers a stop** - audit GA-8, section 8.2. Unresolved
   and deliberately not forced by this design.
8. **Probe cadence**, and the **staleness threshold** at which `valid` decays to
   `unverified` (section 3.6).
9. **Posture on probe failure**: the recommended "alert + raise the tier for
   dependent actions", or the fail-closed opposite ("if the net is gone, stop the
   agents that relied on it"). Section 3.6.
10. **Predefined `roles/compute.instanceAdmin.v1` vs a custom three-permission
    role** (section 8.1). This implements GA-2 rather than reopening it.
11. **Whether Skylize holds `instances.start`** (section 8.4).
12. **Per-tenant signing keys** - recommended deferred, with the routing kept
    forward-compatible at zero cost (sections 2.4, 4.3).
13. **Whether the shared-issuer fallback** is ever acceptable, given per-tenant
    issuers are a best practice and not a requirement (section 0.2).
14. **Which decision tier** a compute-stop proposal maps to in policy inputs
    (section 8.4).

**Deferred to other passes:**

15. The **`kill_switch_protocol.md` amendment** distinguishing "Skylize stops
    acting" from "external actions are undone" (section 6.6).
16. Writing **`integration_inputs.md` 2.2**, which remains
    `[OWNER-DECISION-REQUIRED]` and unsigned. This design is input to it, not a
    replacement for it.
17. **DNS, TLS, CDN, and rate limiting** for the public OIDC origin
    (sections 2.5, 8.4).
18. The **connection-state read route** - which does not exist for any provider
    today (section 7.1).
19. The **migration number**: `0024` assumed from the chain at `628671f`;
    re-check at implementation.

---

## Sources

**Codebase**, commit `628671f`, cited `file:line` throughout. Re-verified this
pass: `edge/routes/` (no OIDC surface), `migrations/versions/0021,0023`,
`app/governance/keys.py`, `bootstrap.py:91-149`, `security/ecc_service.py`,
`app/hitl/service.py`, `app/agents/execution.py`, `tools/proxy.py`,
`tools/base.py`, `tools/builtin/notion_tools.py:400-462`,
`app/credentials/oauth.py:399-464`,
`tests/contract/test_stateless_agents_no_oauth_access.py`, `pyproject.toml:27`.

**Predecessor:** `docs/audits/audit_gcp_killswitch_readiness.md` (`628671f`).

**Live, all fetched or re-fetched 2026-09-04:**
- [Let customers access their Google Cloud resources from your product or service](https://docs.cloud.google.com/iam/docs/use-workload-identity-federation-to-let-customers-access-their-cloud-resources) - vendor requirements, per-tenant issuer best practice, 60-minute recommendation, context claims
- [Configure Workload Identity Federation with other identity providers](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-providers) - **RS256 or ES256**, inline JWKS upload, 8-key cap, attribute mapping/condition CEL
- [Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation) - STS exchange, direct resource access recommended, API-support caveat
- [Configure Workload Identity Federation with other clouds](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds) - STS endpoint and parameters, `principal://` / `principalSet://` formats
- [gcloud iam workload-identity-pools providers create-oidc](https://docs.cloud.google.com/sdk/gcloud/reference/iam/workload-identity-pools/providers/create-oidc) - `--issuer-uri` required, `--jwk-json-path` optional, audience and condition limits
- [Manage workload identity pools and providers](https://docs.cloud.google.com/iam/docs/manage-workload-identity-pools-providers) - independent enable/disable, 30-day soft delete
- [Troubleshoot Workload Identity Federation](https://docs.cloud.google.com/iam/docs/troubleshooting-workload-identity-federation) - `invalid_grant` issuer/JWKS unreachable, `invalid_request` 127-byte subject, `quota_exceeded` 429
- [Manage access to other resources](https://docs.cloud.google.com/iam/docs/manage-access-other-resources) - instance-level `setIamPolicy`, conditions on resource bindings
- [Stop or restart a Compute Engine instance](https://docs.cloud.google.com/compute/docs/instances/stop-start-instance) - `compute.instances.stop`, `roles/compute.instanceAdmin.v1`, preserved state, **charges continue for attached resources**
- [Compute Engine IAM roles and permissions](https://docs.cloud.google.com/compute/docs/access/iam)
- [AIP-155: Request identification](https://google.aip.dev/155) - the request-ID convention behind the `requestId` assumption in section 6.5
