# Notion + Asana connector readiness — pre-`integration_inputs.md` audit

> **Status: AUDIT ONLY. Nothing here is approved, designed, or implemented.**
> No code, no migration, no schema change, no `integration_inputs.md` content.
> **Date:** 2026-09-02
> **Commit audited against:** `2448819` (branch `main`, clean tree)
> **Predecessors:** `docs/audits/audit_gdrive_readiness.md`,
> `docs/06_integrations/oauth_provider_infrastructure_design.md`
> **Tier 0 position:** step 4 — after Drive (`2448819`, §2.5 `[APPROVED]` `a9e6547`),
> before AWS/GCP. GitHub (`integration_inputs.md` §2.4) is pre-existing and untouched.
>
> Claim marking follows `integration_inputs.md:14-18`:
> `[CODE-VERIFIED]` = read out of the tree at this commit ·
> `[INFERRED]` = derived by reasoning, not executed ·
> `[LIVE-VERIFIED]` = fetched from the provider's public documentation on the stated
> date, URL cited · `[UNVERIFIED]` = could not be established from either ·
> `[OWNER-DECISION-REQUIRED]` = no safe default exists.

---

## 0. Gate report — read this first

Three hard exit gates were set. **One is TRIPPED, two are clear.**

### 0.1 Gate "OAuth lifecycle incompatible with the f6360b4 refresh primitive" — **TRIPPED FOR NOTION. CLEAR FOR ASANA.**

The instruction was to stop and report rather than silently redesign the primitive.
Reporting, not redesigning. **Two independent incompatibilities, both Notion-only:**

| # | Incompatibility | Primitive's assumption | Notion's actual behaviour |
|---|---|---|---|
| 1 | **Client authentication method** | `client_id`/`client_secret` in the **form body** (`app/credentials/oauth.py:423-429`) `[CODE-VERIFIED]` | **HTTP Basic auth header.** "The request is authorized using HTTP Basic Authentication. The credential is a colon-delimited combination of the connection's `CLIENT_ID` and `CLIENT_SECRET`", base64-encoded into the `Authorization` header `[LIVE-VERIFIED]` |
| 2 | **`expires_in` in the token response** | **Hard-required and numeric**; a missing or non-numeric value raises `RefreshUnavailable` (`oauth.py:114-118`) `[CODE-VERIFIED]` | **Not present in the documented response schema** of either `POST /v1/oauth/token` or the refresh endpoint `[LIVE-VERIFIED]` |

**Why #1 is the serious one.** `OAuthProviderConfig` exposes exactly two variation
hooks — `parse_token_response` and `is_revocation_error` (`oauth.py:167-170`) — plus
`extra_token_params`, which appends to the **body** only (`oauth.py:428`)
`[CODE-VERIFIED]`. There is **no header hook and no auth-style field** on the config
(`oauth.py:150-172`) `[CODE-VERIFIED]`. The module's own docstring frames the two hooks
as "the ONLY places that variation may leak in" (`oauth.py:165-166`). Notion's Basic-auth
requirement is therefore a variation the config surface **cannot currently express**.
Sending client credentials in the body to a Basic-auth-only endpoint fails the exchange;
by `_classify_failure` (`oauth.py:449-487`) that surfaces as `RefreshUnavailable`, which
correctly denies the call and correctly does **not** mark the grant revoked — so it fails
closed, not dangerously. But it fails **always**.

**Why #2 is the softer one.** `parse_token_response` **is** a hook, so a Notion-specific
parser could supply a synthetic `expires_in_seconds` without touching the primitive. That
is a legitimate use of the existing extension point, not a redesign. It does, however,
force a second question the parser hook cannot answer by itself — see §0.1.1.

**Asana, by contrast, fits the primitive as written** `[LIVE-VERIFIED]`: authorization-code
flow, `POST https://app.asana.com/-/oauth_token`, `client_id`/`client_secret` as
**form-encoded body parameters**, and a response carrying `"expires_in": 3600` alongside
`refresh_token`. That is exactly the RFC 6749 §5.1 shape `_default_parse_token_response`
implements. No hook override is required for the token exchange itself.

#### 0.1.1 The `expires_at NOT NULL` consequence — a schema question, not just a parser question

`oauth_credentials.expires_at` is `TIMESTAMPTZ NOT NULL`
(`migrations/versions/0021_oauth_credentials.py:93`) `[CODE-VERIFIED]`, and
`evaluate_grant` treats it as the sole freshness signal: `row.expires_at > now + skew`
→ `VALID`, otherwise refresh or die (`oauth.py:193-198`) `[CODE-VERIFIED]`. A provider
that publishes no token lifetime cannot populate that column truthfully. The options —
a far-future sentinel, a conservative assumed lifetime, or a nullable column meaning
"non-expiring" — each change either the schema or the meaning of an existing column.
**That is exactly the class of change this pass is forbidden from making.** Raised as
[Q-N.2](#qn2).

### 0.2 Gate "cannot determine org-level vs platform-level" — **NOT tripped, for either provider.**

Both resolve cleanly and identically under the Drive test. See §3. **Both are org-level.**
No guess was required.

### 0.3 Gate "do not assume Notion and Asana ship together" — **honoured, and the evidence argues against it.**

§0.1 establishes that Asana needs zero changes to the OAuth primitive and Notion needs at
least one that the current config surface cannot express. That is a material difference in
prerequisite work, and it is an argument for splitting the passes — but the split is an
owner decision, not mine. Raised as [Q-X.1](#qx1). **No sequencing is assumed anywhere in
this document.**

---

## A. What exists and is reusable AS-IS

Everything in this section is `[CODE-VERIFIED]` at `2448819`.

### A.1 From `f6360b4` — the OAuth infrastructure

| Component | Location | Reusable for Notion? | Reusable for Asana? |
|---|---|---|---|
| `oauth_credentials` table (structured columns, RLS enabled + **forced**, `tenant_isolation` policy) | `migrations/versions/0021_oauth_credentials.py:76-140` | **Yes**, except `expires_at NOT NULL` — §0.1.1 | **Yes, unmodified** |
| `(org_id, provider, label)` uniqueness — one live grant per org per provider per named connection | `0021:117-120` | Yes | Yes |
| `encrypted_refresh_token` **nullable** | `0021:92` | Yes | Yes |
| `key_id` column (forward-compatible key rotation) | `0021:91`, `oauth.py:57` | Yes | Yes |
| `connection_state` three-value durable state (`valid`/`expired`/`revoked`) | `0021:98`, `0021:104-105` | Yes | Yes |
| `OAuthCredentialService.ensure_fresh` / `.access_token` — every non-return path is a denial | `oauth.py:241-298` | Yes | Yes |
| On-demand refresh inside `tenant_session(org_id)`; no cross-tenant write carve-out | `oauth.py:8-16`, `oauth.py:332` | Yes | Yes |
| `get_for_update` row lock + re-evaluation under the lock (concurrent-refresh serialisation) | `oauth.py:332-344` | Yes | Yes |
| Revocation persisted in its **own** transaction after the refresh transaction closes | `oauth.py:317-327`, `oauth.py:387-400` | Yes | Yes |
| `_classify_failure` asymmetry — only an unambiguous signal may mark a grant dead; timeouts/5xx/429 deny without touching state | `oauth.py:449-487` | Yes | Yes (but see §B.2.3) |
| `invalid_client` explicitly separated from customer revocation | `oauth.py:478-482` | Yes | Yes |
| `DEFAULT_EXPIRY_SKEW` = 5 minutes | `oauth.py:53` | Yes | Yes — 5 min skew on a 3600 s token is comfortable |
| Refresh-token rotation handled: a response omitting `refresh_token` keeps the stored one | `oauth.py:363-369`, `oauth.py:121-126` | **Yes — and this matters, see §B.1.4** | Yes |
| `provider` as a registry key, never hardcoded in the proxy | `tools/base.py:124-141` | Yes | Yes |
| Fail-closed on an unregistered provider | `oauth.py:310-315` | Yes | Yes |
| Audit on refresh + on every state change | `oauth.py:402-408`, `oauth.py:505-525` | Yes | Yes |

`tools/base.py:130` already names `'notion'` as an example registry key in the
`ToolOAuthProfile` docstring — **prose only, no behaviour** (§B.0).

### A.2 From `f6360b4` — the ToolProxy OAuth stage

`ToolOAuthProfile` (`tools/base.py:124-141`) is opt-in per tool; `ToolProxy` runs
`_ensure_oauth_credential` only when `tool.oauth is not None` (`tools/proxy.py:244-248`,
handler at `:346`), after every local check and before dispatch. With no service wired,
a tool declaring the profile **fails closed** rather than dispatching (`proxy.py:123-127`,
`:377-383`). **Both providers reuse this stage unchanged.**

### A.3 From `2448819` — the ToolPermissionProfile stage

`ToolPermissionProfile` (`tools/base.py:112-121`) is the third opt-in stage, running when
`tool.permission is not None` (`proxy.py:266-270`, handler at `:400`), after the OAuth
stage and before spend reservation (`proxy.py:250-259`). Backed by `org_permission_grants`
(`migrations/versions/0022_org_permission_grants.py:76-97`) with deny-by-default semantics:
an org with no rows for an `action_class` can perform that action with nobody
(`tools/base.py:98-100`).

**Reusable for Asana's high-risk action in mechanism, but not in vocabulary** — see §B.2.4.
**Not needed for Notion at all** — see §C.1.

### A.4 The connector precedent (Drive, `2448819`)

`tools/builtin/drive_tools.py` is the shape to follow, and three of its decisions carry
over to both providers verbatim `[CODE-VERIFIED]`:

1. **Raw `httpx`, no vendor SDK** (`drive_tools.py:20-33`) — following `hubspot_tools.py:76`.
   The stated reasons (sync SDKs block the request event loop; a vendor auth library would
   bypass the RLS-scoped, audited refresh primitive) apply identically to Notion and Asana.
2. **Deny-by-default backstop at the handler** (`drive_tools.py:338-350`): a tool performing
   an elevated action must *also* require `ctx.permission_grant`, so omitting the profile
   fails closed instead of silently skipping the gate.
3. **Execute what the gate authorized, not what the input asked for** (`drive_tools.py:357-368`)
   — the executed action cannot drift from the approved one.

### A.5 Composition-root pattern

`resolve_google_drive_config` (`bootstrap.py:181-199`) + conditional
`register_provider` (`bootstrap.py:491-499`) is a clean, copyable pattern: platform-level
`SKYLIZE_*` client credentials, opt-in (both empty ⇒ provider never registered ⇒ tool calls
fail closed in the proxy OAuth stage), and **setting exactly one of the pair is refused at
boot**. Directly reusable for both providers.

---

## B. What is missing / net-new, per provider

### B.0 Existing references in `src/` — review item 1, answered

`[CODE-VERIFIED]` Repo-wide `grep -ril`, `2026-09-02`:

- **Notion:** exactly **one** hit in `src/` — `src/skylize/tools/base.py:130`, prose inside
  the `ToolOAuthProfile` docstring: "`provider` is a registry key ('google_drive',
  'notion', ...)". **No code, no config var, no dead or partial attempt.**
- **Asana:** **zero** hits in `src/`. Nothing at all.
- Outside `src/`: `docs/06_integrations/oauth_provider_infrastructure_design.md` names both
  as intended consumers (`:8`) and makes three design-time assertions about them
  (`:88-89`) — see §B.3, **two of which this audit contradicts**.
- `.claude/skills/**` hits are vendored third-party skill libraries, unrelated. Excluded.

**No prior work exists to reconcile with, and no dead attempt to clean up, for either provider.**

### B.1 Notion — net-new

#### B.1.1 A client-authentication mechanism the config cannot currently express `[BLOCKING]`
Per §0.1. `OAuthProviderConfig` has no header hook (`oauth.py:150-172`) and
`_post_refresh` hardcodes body credentials (`oauth.py:423-429`). Resolving this is a change
to the shared primitive that affects Drive and every future provider. **Explicitly not
designed here** — [Q-N.1](#qn1).

#### B.1.2 An expiry semantics decision `[BLOCKING]`
Per §0.1.1 — [Q-N.2](#qn2).

#### B.1.3 A `Notion-Version` header on every API call
`[LIVE-VERIFIED]` `2026-09-02`, https://developers.notion.com/reference/post-page — the
`Notion-Version` header is required, latest documented value **`2026-03-11`**. This is a
**connector-side** concern (no OAuth impact) but it is a pinned-version dependency with no
analogue in the Drive connector, and the pinning policy is a real decision — [Q-N.4](#qn4).

#### B.1.4 Refresh-token rotation — the primitive already handles it, but the failure mode is sharp
`[LIVE-VERIFIED]` https://developers.notion.com/page/changelog, entry dated **2026-06-08**:
> "New public connections now mint a fresh `access_token` and `refresh_token` for each
> successful OAuth authorization instead of returning the existing active token."

The primitive persists a rotated `refresh_token` atomically with the new access token
inside the locked transaction (`oauth.py:363-379`) `[CODE-VERIFIED]`, which is the correct
handling. **However**, a web-search summary also asserted a one-step rotation window, a
180-day absolute refresh-token lifetime, a 30-day inactivity expiry, and whole-grant
revocation on replay of a rotated token. **I could not confirm any of those four claims
against Notion's canonical `refresh-a-token` reference page**, which states none of them
`[LIVE-VERIFIED]`. They are recorded here as **`[UNVERIFIED]`** and must not be relied on.
If a refresh-token lifetime cap does exist, it changes the reconnect-prompting design
materially — [Q-N.3](#qn3).

#### B.1.5 No scopes — capabilities instead
`[LIVE-VERIFIED]` https://developers.notion.com/reference/capabilities — Notion has **no
OAuth `scope` parameter**. Capabilities are fixed at integration-registration time in the
developer portal: *Read content*, *Update content*, *Insert content*, *Read comments*,
*Insert comments*, and a three-way user-information setting (*No user information* /
*without email addresses* / *with email addresses*).

Two consequences:
- `OAuthProviderConfig.scopes` (`oauth.py:164`) and `oauth_credentials.scopes TEXT[]`
  (`0021:96`) are **inert for Notion** — the token response carries no `scope` field, so
  `_default_parse_token_response` would record `()` (`oauth.py:119-120`) `[CODE-VERIFIED]`.
  Harmless (the column has a `'{}'` default) but it means **the attenuation invariant the
  `granted_scopes` column exists to make checkable is not checkable for Notion**. That is a
  genuine, if narrow, governance regression relative to Drive.
- **Drive's §2.5 scope-narrowing precedent does not transfer.** There is no `drive.file`
  analogue to argue for; the equivalent decision is *which capabilities to register the
  Skylize Notion integration with*, made once in a portal, not per-authorization. It is
  still a real least-privilege decision — [Q-N.5](#qn5).

#### B.1.6 Extra identity fields in the token response
`[LIVE-VERIFIED]` https://developers.notion.com/reference/create-a-token — the response
carries `bot_id`, `workspace_id`, `workspace_name`, `workspace_icon`, `owner`,
`duplicated_template_id`, `request_id`. `oauth_credentials.provider_account_id`
(`0021:90`) is the natural home for `workspace_id`. The others have **no column**. Whether
any is needed is a design question, not an audit finding; noted so it is not discovered late.

### B.2 Asana — net-new

#### B.2.1 The OAuth exchange itself: nothing net-new
Per §0.1. `[LIVE-VERIFIED]` https://developers.asana.com/docs/oauth: authorization-code
flow, authorize at `https://app.asana.com/-/oauth_authorize`, token at
`POST https://app.asana.com/-/oauth_token`, body-form client credentials, response
`"expires_in": 3600` + `refresh_token`. A `build_asana_provider_config` mirroring
`google_provider.py:35-52` — endpoint, client credentials, scopes, both hooks left at
default — appears sufficient **for the exchange**. Subject to §B.2.3.

#### B.2.2 Refresh-token lifetime `[UNVERIFIED]`
Asana's docs describe refresh tokens as long-lived and usable "for as long as the user has
authorized the application", but **publish no expiration period or rotation policy**
`[LIVE-VERIFIED]`. Lower risk than Notion's equivalent gap because the access-token
lifetime *is* published, so the primitive has a truthful `expires_at` to store either way.

#### B.2.3 `is_revocation_error` may need a provider-specific override `[FLAGGED]`
The default predicate requires `payload["error"] == "invalid_grant"` at 400/401
(`oauth.py:130-138`) `[CODE-VERIFIED]`. **Asana's documented error envelope is
`{"errors": [{"message": "..."}]}` — a message array, not an RFC 6749 `error` code**, and
its docs do not state whether the *token* endpoint uses RFC 6749 codes or this envelope
`[LIVE-VERIFIED]`, https://developers.asana.com/docs/errors.

**The failure is safe but silently degrading:** if Asana returns its own envelope on a
genuinely revoked grant, the default predicate returns `False`, and `_classify_failure`
falls through to `RefreshUnavailable` (`oauth.py:484-487`). The call is denied — correct —
but `connection_state` is **never** written to `'revoked'`, so the customer is never told
to reconnect and every subsequent call re-hits Asana's token endpoint indefinitely. This is
precisely the failure the primitive's own comment at `oauth.py:317-327` was written to
prevent, arriving through a different door. **Must be settled against a live 400 response
before the connector is trusted** — [Q-A.4](#qa4). It cannot be settled from documentation.

#### B.2.4 The `ToolPermissionProfile` vocabulary does not fit Asana's membership model `[FLAGGED]`
`ToolPermissionProfile` requires **both** `grantee_field` and `role_field`, each
`Field(min_length=1)` (`tools/base.py:115-116`) `[CODE-VERIFIED]`, and
`org_permission_grants.max_role` is constrained to `CHECK (max_role IN ('reader',
'commenter', 'writer'))` (`0022:86`) `[CODE-VERIFIED]` — a Drive-shaped access vocabulary,
ordered reader < commenter < writer (`0022:50-51`).

**Asana's `POST /projects/{project_gid}/addMembers` has no role tier at all**
`[LIVE-VERIFIED]` — a project member is a project member; the body is a `members` array of
identifiers. There is no reader/commenter/writer axis to map onto. So the gate's *mechanism*
(deny-by-default allow-list, grantee pattern matching, execute-what-was-authorized) fits
Asana's high-risk action well, while its *role dimension* has nothing to bind to. Inventing
a synthetic role to satisfy `min_length=1`, or widening the CHECK constraint, are both
schema/contract changes — **explicitly out of scope this pass** — [Q-A.3](#qa3).

#### B.2.5 Granular scopes exist and are "subject to revision"
`[LIVE-VERIFIED]` https://developers.asana.com/docs/oauth-scopes — published granular
scopes include `tasks:read`, `tasks:write`, `tasks:delete`, `projects:read`,
`projects:write`, `projects:delete`, `stories:read`, `stories:write`, `users:read`,
`workspaces:read`, `attachments:read/write/delete`, `team_memberships:read`, `teams:read`.
A **"Full permissions"** default also exists, granting access to all endpoints; the docs
state that when it is enabled an app **cannot** request specific scopes and must use
`scope=default` or omit the parameter.

**This is the direct analogue of Drive's Q2.5a decision and it is the single highest-leverage
Asana question.** Granular scopes make Drive's attenuation-only principle enforceable and
keep `oauth_credentials.scopes` meaningful; "Full permissions" discards both. The docs
describe the granular list as "currently available" and "subject to revision" and do not
state a GA/beta status `[UNVERIFIED]` — a stability risk worth recording — [Q-A.1](#qa1).

Note the asymmetry this creates: **Asana can honour the attenuation invariant that Notion
structurally cannot** (§B.1.5).

### B.3 Design-doc claims this audit contradicts `[CODE-VERIFIED]` + `[LIVE-VERIFIED]`

`oauth_provider_infrastructure_design.md:567` states plainly that "no live
Google/Notion/Asana documentation was fetched" when it was written. Three of its
Notion/Asana assertions are now testable, and **two are wrong**:

| Design-doc claim | Location | Verdict |
|---|---|---|
| "Drive, Notion, and Asana all use [the OAuth 2.0 authorization-code flow]" | `:73` | **Correct** for all three `[LIVE-VERIFIED]` |
| Notion's provider-specific extras are "`workspace_id` and `bot_id`" | `:88-89` | **Incomplete.** Those exist, but the load-bearing Notion variance is Basic-auth client authentication and an absent `expires_in` — neither anticipated |
| "Asana's differing scope encoding" | `:89` | **Not supported by the live docs.** Asana returns a standard RFC 6749 §5.1 response; its scopes are conventional space-delimited `<resource>:<action>` strings. The real Asana variance is its **error** envelope (§B.2.3), not its scope encoding |

This is a documentation-drift finding against a **design** doc, not against shipped code —
the design doc marked its own uncertainty honestly at `:567`. Recorded so the next author
does not inherit the two wrong assumptions. **Not corrected here** (this pass writes no
design content).

---

## C. Write actions and severity classification

Classification follows Drive's Q2.5b test as stated in code
(`drive_tools.py:328-333`) `[CODE-VERIFIED]`: **high-risk = "the action where data leaves
Skylize's custody to a party the agent chooses at run time"**; everything else is routine
and rides the OAuth stage plus contract/scope/`max_calls_per_run`/convergence controls.

### C.1 Notion — **no action meets the high-risk test. Stated explicitly, as instructed.**

| Candidate write action | Endpoint | Severity | Reasoning |
|---|---|---|---|
| Create a page | `POST /v1/pages` `[LIVE-VERIFIED]` | **Routine** | Writes into a workspace location already shared with the connection |
| Append block children (content append) | Blocks children endpoint `[LIVE-VERIFIED]` — capability *Insert content* | **Routine** | Same |
| Update page properties | *Update content* capability `[LIVE-VERIFIED]` | **Routine** | Same |
| Create a comment | *Insert comments* capability `[LIVE-VERIFIED]` | **Routine** | Content, not access |
| Create a database / data source | `parent.data_source_id` accepted on `POST /v1/pages` `[LIVE-VERIFIED]` | **Routine** | Structure, not access |
| **Share a page / change permissions / make public** | **Does not exist** | **N/A** | See below |

**`[LIVE-VERIFIED]` `2026-09-02`, https://developers.notion.com/reference/capabilities:
there is no sharing, permission-changing, or external-invitation capability in Notion's
public API.** The connection's reach is bounded by what a human has already shared with it
in the Notion UI, and the API cannot widen that boundary. **Notion therefore has no
`permissions.create` analogue and needs no `ToolPermissionProfile`.**

I am not manufacturing a high-risk action to mirror Drive's shape. The nearest genuine
concern is different in kind and lower in severity: *Insert comments* can push
agent-authored text in front of whoever already watches a page (a notification-surface
concern, not a data-custody transfer), and the *user information with email addresses*
capability is a **read**-side privacy decision, not a write action. Both are worth an owner
glance; neither is a `ToolPermissionProfile` case on the Drive test.

### C.2 Asana — **one action meets the high-risk test squarely, and one exceeds it.**

| Candidate write action | Endpoint | Severity | Reasoning |
|---|---|---|---|
| Create a task | `POST /tasks` `[INFERRED]` from `tasks:write` | **Routine** | Content creation inside an authorized workspace |
| Update a task (status, due date, fields) | `PUT /tasks/{gid}` `[INFERRED]` | **Routine** | Same |
| Assign a task to a user | `assignee` field on task write `[INFERRED]` | **Routine** | Assignment inside the workspace transfers no access; the assignee already has it |
| Add a comment / story | `stories:write` `[LIVE-VERIFIED]` scope exists | **Routine** | Content, not access |
| Delete a task | `tasks:delete` `[LIVE-VERIFIED]` scope exists | **Elevated — different axis** | Destructive, not disclosing. Drive's audit deliberately shipped **no** deletion tool (`drive_tools.py:17-18`). The precedent is *omit the verb*, not gate it — [Q-A.2](#qa2) |
| **Add members to a project** | **`POST /projects/{project_gid}/addMembers`** `[LIVE-VERIFIED]` | **HIGH-RISK — direct Drive-sharing analogue** | See below |
| **Add a user to a workspace** | **`POST /workspaces/{workspace_gid}/addUser`** `[LIVE-VERIFIED]` | **HIGHER than Drive sharing** | See below |

**`POST /projects/{project_gid}/addMembers`** `[LIVE-VERIFIED]` `2026-09-02`,
https://developers.asana.com/reference/addmembersforproject. The `members` field is
documented as "An array of strings identifying users. These can either be the string
`"me"`, **an email**, or the gid of a user." **An agent-chosen email address at run time,
granting a named external party access to a customer's project** — structurally identical
to Drive's `permissions.create` (grantee + role), which is exactly why Drive carries the
third gate. It also has a documented side effect worth surfacing: "a user being added as a
member may also be added as a *follower*."

**`POST /workspaces/{workspace_gid}/addUser`** `[LIVE-VERIFIED]`
https://developers.asana.com/reference/adduserforworkspace: "Add a user to a workspace or
organization. The user can be referenced by their globally unique user ID or **their email
address**." This grants access at **organization** scope, not per-object — a blast radius
**wider than anything in the Drive connector**, whose narrowest-scope `drive.file` design
deliberately kept every action to app-created files. Whether Asana's ceiling is set at
per-project membership or extends to workspace membership is a Q2.5a-class decision that
must be made **before** any scope is requested — [Q-A.1](#qa1). Whether the endpoint sends
an invitation to a non-member email is **`[UNVERIFIED]`** — the docs do not say, and it
raises the severity if true.

### C.3 Idempotency — inherited, unresolved, and it bites both

`[CODE-VERIFIED]` Drive's audit D.5 recorded that `ToolProxy.invoke` hardcodes
`f"tool:{tool.tool_id}:{uuid4()}"` and cannot accept a caller-supplied idempotency key.
**Re-verified at `2448819`: still true.** Every routine write above (page create, task
create, comment create) has the same timeout-then-retry duplication hazard as Drive's
`files.create`, and the same non-fix. Not a new finding, but it is not fixed either, and it
now applies to two more providers.

**RESOLVED for Asana at `828c432` (2026-09-09); the shared premise was wrong.** As with
Drive's D.5, the blocker was never `ToolProxy.invoke`'s signature. Asana, however, has
**no** native idempotency mechanism at all -- `[LIVE-VERIFIED]` 2026-09-09: no
`X-Idempotency-Key`, no `requestId`, no client-supplied request id, and the docs are
silent on POST retry semantics -- so unlike Drive there was nothing native to adopt, and
the three verbs are handled differently rather than given one shared fix:

* `create_task` / `create_project`: **5xx is no longer retried**, following the Notion
  precedent (`notion_tools.py:71-79`). A check-before-create was evaluated and
  **rejected** for these verbs: a name is not unique in Asana, two identical tasks are
  legitimate, so a name-match query would suppress real work as often as it caught a
  duplicate. Silently losing a customer's task is worse than a spurious failure.
* `add_project_member`: **5xx still retries**, made safe by a check-before-create --
  `GET /memberships?parent=<project>&member=<user>` (`[LIVE-VERIFIED]` 2026-09-09,
  `developers.asana.com/reference/getmemberships`). Authoritative rather than heuristic
  because membership is a UNIQUE relation. The check fails SAFE: if it cannot complete
  it reports the original failure rather than claiming an unproven grant.

429 is still retried on every verb -- it is refused before Asana does any work.

**Notion needs no change**: it already declined to inherit the hazard.

---

## D. Decision Engine hook point — review item 5

`[CODE-VERIFIED]` **The Drive audit's §C.6 finding holds unchanged at `2448819`.** The
synchronous Decision Engine gate runs **once per `/agents/execute` request**, before the
token mint (`app/agents/execution.py` `_run_decision_gate`); an `agent.execute` proposal
returns terminally at evaluator stage 2.5, ahead of the capital stage. `ToolProxy` holds no
evaluator and calls none — `proxy.py` imports `OAuthCredentialService` (`:29-32`) and
`PermissionGate` (`:35-38`), and **no `DecisionEvaluator`**.

**So, per provider:**

- **Notion:** the per-request gate suffices, and no fourth opt-in stage is warranted. Every
  Notion write is routine (§C.1) and the API cannot grant access at all. Contract
  `allowed_tools`, token/capability bounds, `max_calls_per_run`, and the convergence breaker
  are the operative controls. **Nothing new needed.** `[INFERRED]` from §C.1 + the code facts above.
- **Asana:** the per-request gate **does not** cover `addMembers`/`addUser`, for exactly the
  reason it did not cover Drive's `permissions.create`. But the answer that precedent already
  reached is **not** a new stage — Drive's `2448819` resolved it with the *existing*
  `ToolPermissionProfile` allow-list, and specifically **not** with HITL, because HITL replay
  would re-execute the original action a second time on approval (Q2.1d precedent). That
  reasoning transfers to Asana without modification. **The mechanism exists; only its role
  vocabulary does not fit** (§B.2.4).

**Neither provider is a case for a fourth ToolProxy stage on the evidence available.** The
residual question is not *whether* Asana's membership verbs need gating — they plainly do —
but whether the existing gate can express them, which is [Q-A.3](#qa3).

---

## E. Stateless invariant — review item 7

`[CODE-VERIFIED]` **Confirmed for both providers, and trivially so, as expected.**

`cfo_agent` declares `memory_read_access=[]` and `memory_write_access=[]`
(`contracts/mvp/finance.py:184-185`), with `invocable_tools=["utility.current_datetime"]`
only (`:176`) and the comment "only `utility.current_datetime` is invocable, never
`memory.search`, per the CFO/Safety statelessness rule" (`:159-161`).
`chief_security_officer`, `director_ai_safety`, `llm_safety_agent`, and
`prompt_injection_agent` are covered by `contracts/definitions/security.py:16`: "All are
stateless: memory_read_access and memory_write_access minimal", and
`contracts/mvp/safety.py:4` states the same with `memory_read_access=[]`,
`memory_write_access=[]`. `contracts/mvp/cowork.py:13` names `prompt_injection_agent` among
those declaring empty memory access.

Nothing in either provider's write surface (§C) implies memory access for any of these five
agents. A Notion page write or an Asana task write is an outbound integration call
dispatched through `ToolProxy`; it neither reads nor writes agent memory, and enforcement is
independent of the connector — the memory gateway denies on an empty access list
(`memory/gateway.py:6`). **No design intent in this audit requires relaxing the invariant
for either provider.**

---

## F. Rate limits — review item 6

### F.1 Notion `[LIVE-VERIFIED]` `2026-09-02`, https://developers.notion.com/reference/request-limits

- **Per connection:** "an average of three requests per second, with some bursts beyond the
  average allowed."
- **Per workspace:** a **separate** limit "shared across all of the workspace's connections
  and scaled to the workspace's plan."
- **On exceed:** HTTP **429**, error code `"rate_limited"`, with
  `additional_data.rate_limit_reason` naming which limit was breached (e.g.
  `public_api_request_rate_limit`). `Retry-After` header carries an integer number of
  seconds. HTTP **529** (service overload) gets identical retry treatment.
- **Size limits** (these will bite a content-append connector before the rate limit does):
  **1000 block elements and 500KB per request**; rich-text content and URLs capped at
  **2000 characters**, emails 200, phone numbers 200; multi-select options 100; relations
  100; people arrays 100; block/rich-text arrays 100 elements. The docs note these "cap the
  size of a single request, not how much a property can hold."
- The page warns explicitly that "rate limits may change."

### F.2 **Notion has a recent change comparable to Drive's May 2026 quota-model shift** `[LIVE-VERIFIED]`

https://developers.notion.com/page/changelog, entry dated **2026-06-16**:
> "The Notion API now applies a rate limit per workspace, in addition to the existing
> per-connection limit."

**This is the same class of change as Drive's quota-unit shift and deserves the same
weight.** A per-workspace limit "scaled to the workspace's plan" is **not** under Skylize's
control and is **shared with every other integration the customer runs**. A Skylize connector
can be throttled by a customer's unrelated third-party tools, and 3 req/s per connection is
no longer the binding constraint. Two related changelog entries also land in the audit
window: **2026-06-08** (fresh token pair minted per authorization — §B.1.4) and **2026-07-02**
(personal access tokens gain selectable expirations of 7/30/90/180 days or 1 year; expired
PATs "stop authenticating and return an `unauthorized` error" — PATs are not the OAuth path,
noted only so it is not confused with one).

A further entry dated **2026-07-14** states "Notion **MCP** access tokens now last about
eight hours, up from one hour" and that clients "must continue relying on the `expires_in`
response value." **This concerns Notion's MCP server, not the public-API OAuth path**, and I
am recording it separately rather than using it as evidence about `POST /v1/oauth/token`.
It does suggest Notion issues `expires_in` on at least one token surface, which makes the
public-API reference's silence (§0.1) more likely to be a documentation gap than a
guarantee of non-expiry — **but that is `[INFERRED]`, not verified, and it is exactly why
[Q-N.2](#qn2) needs an empirical answer rather than a documentary one.**

### F.3 Asana `[LIVE-VERIFIED]` `2026-09-02`, https://developers.asana.com/docs/rate-limits

- **Volume:** **150** requests/minute (free tier), **1,500** requests/minute (paid tier).
- **Concurrency:** max **50** simultaneous GET; max **15** simultaneous POST/PUT/PATCH/DELETE.
  Evaluated independently — read and write capacity do not interfere.
- **Cost-based:** "The cost of a request is calculated after the response is built and is
  deducted from a per-minute quota" — heavy requests traversing large object graphs can
  trigger throttling at low request volume.
- **Search:** limited to **60** requests/minute, separate from the volume cap.
- **Concurrent jobs:** duplication, instantiation, and export endpoints limited to **5**
  concurrent jobs per user.
- **On exceed:** HTTP **429** with a `Retry-After` header; the quota "is evaluated more
  frequently than once per minute, so you may not need to wait a full minute before retrying."
- **Allocation:** "Limits are allocated per authorization token. Different tokens have
  independent limits."

### F.4 The asymmetry worth flagging

**Asana's per-token allocation isolates each customer org; Notion's per-workspace limit does
not.** Under the org-level model both providers use (§3), each Skylize org holds its own
grant — so on Asana, one org's burst cannot throttle another's, and the effective ceiling is
per-org. On Notion, a customer's *own* other integrations share their workspace ceiling with
Skylize. **Notion's post-2026-06-16 rate-limit behaviour is therefore partly outside both
Skylize's and the customer's sole control**, which is a materially different operational risk
profile and an argument for treating 429 handling as provider-specific rather than shared.
`[INFERRED]` from the two published policies above.

---

## G. Cross-provider architecture — review item 8

**Two separate connector modules, both riding the shared OAuth infrastructure and both
registered as independent providers.** The instruction was to confirm this rather than
assume it. Confirming, with the reasoning stated:

**Evidence for separate connectors — four independent grounds, not just "different data models":**

1. **Disjoint object models.** Notion writes pages, blocks, and data sources; Asana writes
   tasks, projects, and stories. No shared input schema, output schema, or endpoint shape.
   A merged module's every function would branch on provider from its first line.
2. **Disjoint permission surfaces.** Asana needs a `ToolPermissionProfile` for
   `addMembers`/`addUser`; Notion has no permission-granting API at all (§C.1). A shared
   module would carry a gate that is dead code for half its callers — the precise inverse of
   the deny-by-default discipline `tools/base.py:98-100` establishes.
3. **Disjoint auth mechanics at the token endpoint.** Basic-auth header vs form-body
   credentials (§0.1). This variance belongs in two `OAuthProviderConfig` values, and
   `oauth.py:151` states the design intent directly: "Everything a provider contributes.
   Nothing here is Drive-specific."
4. **Disjoint scope semantics.** Asana can honour the attenuation invariant via granular
   scopes; Notion structurally cannot (§B.1.5, §B.2.5).

**Evidence that this is architecturally clean, not merely convenient:** the tree already
demonstrates the pattern at one provider. `google_provider.py` is 52 lines containing an
endpoint, client credentials, and a scope — with an explicit assertion that "There is no
Google-specific refresh logic, storage, encryption, concurrency control, or revocation
handling here" (`google_provider.py:3-8`) `[CODE-VERIFIED]`. `ToolOAuthProfile.provider` is a
registry key by construction, "never a hardcoded provider inside the proxy"
(`tools/base.py:130-131`), and `register_provider` (`oauth.py:233-234`) takes providers as
data. **The infrastructure was built for exactly this fan-out and needs no change to
accommodate two more providers** — the changes flagged in §B.1.1 are demanded by Notion's
Basic-auth requirement, not by the multi-provider shape.

**One shared cost either way:** §C.3's idempotency gap and §B.1.1's config-surface gap are
both *infrastructure* concerns. Splitting the connectors does not split those; whoever ships
first pays for the primitive change, and the second inherits it.

---

## H. Open questions requiring owner decision

Stated as questions. No recommendation is offered where the audit-only scope forbids one.
**`integration_inputs.md` §2.6/§2.7 cannot be drafted until the blocking ones are answered.**

### Notion

<a id="qn1"></a>**Q-N.1 — How does Basic-auth client authentication reach the token endpoint? `[BLOCKING]`**
`OAuthProviderConfig` cannot express it today (§0.1). Options visible from the code — add an
auth-style field, add a header hook, or make the whole request-construction step overridable —
each widen the shared primitive that Drive and every future provider depend on. **The gate
forbids me from choosing.** Note this is the first real test of whether the two-hook
extension surface (`oauth.py:165-166`) is sufficient, and the answer is currently *no*.

<a id="qn2"></a>**Q-N.2 — What goes in `expires_at` for a provider that publishes no token lifetime? `[BLOCKING]`**
`0021:93` is `NOT NULL` and `evaluate_grant` (`oauth.py:193-198`) treats it as the sole
freshness signal. A sentinel, an assumed lifetime, or a nullable column are all
schema-or-semantics changes. **Prerequisite: an empirical check of a live Notion token
response** — the documentation does not settle it, and §F.2 gives reason to think the docs
may simply be silent rather than authoritative.

<a id="qn3"></a>**Q-N.3 — Do Notion refresh tokens have a lifetime cap? `[UNVERIFIED]`**
Claims of a 180-day absolute cap, 30-day inactivity expiry, one-step rotation window, and
whole-grant revocation on rotated-token replay could **not** be confirmed against Notion's
canonical reference pages (§B.1.4). If real, they force a proactive reconnect-prompting
design that nothing in the current infrastructure provides. Needs an authoritative answer.

<a id="qn4"></a>**Q-N.4 — `Notion-Version` pinning policy.** Pin `2026-03-11` as a constant, and
what is the upgrade process? Notion's versioning has no analogue in the Drive connector.

<a id="qn5"></a>**Q-N.5 — Which capabilities does the Skylize Notion integration register with?**
The least-privilege decision equivalent to Drive's Q2.5a, but made **once in a portal**
rather than per-authorization (§B.1.5). Specifically: is *Read content* needed, or is
insert-only sufficient for the intended use? And which of the three user-information
settings — *with email addresses* is a privacy decision, not a functional one.

### Asana

<a id="qa1"></a>**Q-A.1 — Granular scopes or "Full permissions"? `[BLOCKING]`**
The direct Q2.5a analogue and the highest-leverage Asana question (§B.2.5). "Full
permissions" forecloses the attenuation-only principle and makes
`oauth_credentials.scopes` decorative. If granular: **exactly which**, given `addMembers`
(§C.2) and given that the docs call the granular list "subject to revision"? The scope set
also implicitly answers whether workspace-level `addUser` is in scope at all.

<a id="qa2"></a>**Q-A.2 — Is `tasks:delete` in or out?**
Drive's precedent was to **omit** the destructive verb rather than gate it
(`drive_tools.py:17-18`). Does that transfer, or does Asana's use case need deletion?

<a id="qa3"></a>**Q-A.3 — How does a role-less membership grant fit `ToolPermissionProfile`? `[BLOCKING for the Asana high-risk action]`**
`role_field` is mandatory (`tools/base.py:116`) and `max_role` is CHECK-constrained to
Drive's three-value vocabulary (`0022:86`); Asana project membership has no role axis
(§B.2.4). A synthetic role, a widened constraint, or an optional `role_field` are all
contract/schema changes this pass may not make. **Until this is answered, Asana's
`addMembers` tool cannot be specified** — and shipping Asana *without* it means shipping a
connector that cannot add project members at all, which may be the right answer.

<a id="qa4"></a>**Q-A.4 — Does Asana signal revocation in a way `_default_is_revocation` detects?**
§B.2.3. The failure is safe but silently degrading: a grant that is really revoked would
never be marked `'revoked'`, the customer would never be prompted to reconnect, and every
call would re-hit Asana's token endpoint forever. **Cannot be settled from documentation —
needs one observed live 400/401 from the token endpoint with a deliberately invalidated
grant.**

<a id="qa5"></a>**Q-A.5 — Is workspace-level `addUser` ever in scope?**
Its blast radius exceeds anything in the Drive connector (§C.2) and it may send an
invitation to a non-member email (`[UNVERIFIED]`). The narrow answer — exclude it entirely,
as Drive excluded Shared Drives and deletion — is available but is not mine to give.

### Cross-cutting

<a id="qx1"></a>**Q-X.1 — Do Notion and Asana ship in one pass or two? `[OWNER-DECISION-REQUIRED]`**
The gate forbids assuming they ship together, and the evidence is asymmetric: **Asana needs
zero changes to the OAuth primitive** (§B.2.1) and is blocked only on scope selection and
the `ToolPermissionProfile` vocabulary; **Notion is blocked on two changes to the shared
primitive** (Q-N.1, Q-N.2), at least one of which touches every existing provider including
shipped Drive. Sequencing them differently is a real option. **Not decided here.**

**Q-X.2 — Section numbering.** Drive took `§2.5` (`a9e6547`). Do Notion and Asana become
`§2.6` and `§2.7`, and in which order? Same class of question as the Drive audit's D.1, and
the answer follows from Q-X.1.

**Q-X.3 — Does either provider change the encryption posture?**
Drive's D.4 asked whether a refresh token to a customer's documents justifies revisiting the
single platform-wide Fernet key with no rotation. Two more providers' refresh tokens in the
same table, under the same key, raise the same question with more weight. Still deferred;
`key_id` (`0021:91`) keeps the option open at no cost.

**Q-X.4 — Idempotency.** §C.3 — inherited from Drive's D.5, unresolved at `2448819`, now
applicable to two more providers' create verbs.

---

## I. Review-item coverage

| # | Item | Where | Outcome |
|---|---|---|---|
| 1 | Existing Notion/Asana references in `src/` | §B.0 | Notion: one docstring mention (`tools/base.py:130`). Asana: **zero**. No dead or partial attempts |
| 2 | OAuth shape, live-verified | §0.1, §B.1, §B.2 | Asana fits as-is. **Notion does not — gate tripped** |
| 3 | Org-level vs platform-level | §3 below | **Both org-level.** No guess required |
| 4 | Write actions + severity | §C | Notion: **no high-risk action exists**, stated explicitly. Asana: `addMembers` (Drive-equivalent) and `addUser` (wider than Drive) |
| 5 | Decision Engine hook point | §D | Per-request gate suffices for Notion. Asana needs the **existing** `ToolPermissionProfile`, not a fourth stage |
| 6 | Rate limits, live-verified | §F | Both cited with dates. **Notion has a comparable recent change: per-workspace limits, 2026-06-16** |
| 7 | Stateless invariant | §E | Confirmed for all five agents, both providers |
| 8 | One connector or two | §G | **Two**, on four independent grounds, riding one OAuth infrastructure |

### 3. Org-level vs platform-level — review item 3, answered

**Both providers are ORG-LEVEL. `[INFERRED]`, from the same test Drive used, applied to
provider facts that are `[LIVE-VERIFIED]`.**

The Drive test (audit D.2): does the action need to happen in the **customer's own**
workspace, or in Skylize's? Slack came out platform-level because Skylize posts into its own
workspace — no broker, no `org_credentials` row, no RLS, an env-var token
(`integration_inputs.md:337-355`). Drive came out org-level because a deliverable must land
in the customer's Drive.

- **Notion:** a page created for a customer must exist in **the customer's workspace**, where
  their team reads it. A page in a Skylize-owned Notion workspace is invisible to them. The
  provider's own model confirms it: an OAuth grant is scoped to a single `workspace_id`
  returned in the token response `[LIVE-VERIFIED]`, and a connection can only reach pages a
  human in **that** workspace has shared with it (§C.1). Platform-level is not merely wrong —
  it is unimplementable.
- **Asana:** a task created for a customer must appear in **the customer's** projects,
  assigned to **their** people. Asana reinforces this structurally: "Limits are allocated per
  authorization token" `[LIVE-VERIFIED]`, and `addMembers`/`addUser` operate on the
  authorizing org's own workspace.

**Both therefore need what Drive needs and Slack did not:** the broker, per-org grants in
`oauth_credentials`, RLS, and on-demand refresh. That is precisely the infrastructure
`f6360b4` built (§A.1), which is why §G concludes it needs no structural change for either.

**No WF-06 signal exists to corroborate this** `[CODE-VERIFIED]`: `git grep "WF-06|WF_06|WF06"`
returns **nothing repo-wide** at `2448819`. There is no n8n workflow-library entry for "Deep
Research + Notion" — consistent with `integration_inputs.md:93`, which already records the
same absence for WF-03/WF-04 ("No CFO Finance n8n workflow exists to duplicate or
reconcile"). **The org-level conclusion rests on the Drive test and the live provider
evidence above, not on any n8n signal**, and the requested corroboration is simply not
available. `docs/06_integrations/n8n_reality_map_2026-07-15.md` documents the n8n surface but
names no numbered workflow library.

---

## J. Scope compliance

- No code written. No migration. No schema change. No `integration_inputs.md` edit.
- No design proposed; every unresolved fork is a question in §H, not a recommendation.
- No claim about Notion or Asana rests on training-data memory: every provider claim is
  `[LIVE-VERIFIED]` with URL and date, or explicitly `[UNVERIFIED]`.
- Four search-derived claims about Notion refresh-token lifetimes were **rejected** for
  failing verification against the canonical page (§B.1.4), rather than repeated.
- One live source was **discarded as a confounder**: `developers.notion.com/workers/guides/oauth`
  discusses Notion's Workers runtime connecting to *third-party* providers (its example is
  Salesforce), not Notion's own token lifetime. It is not cited as evidence in §0.1.
- The Notion MCP token-lifetime changelog entry is recorded (§F.2) but deliberately **not**
  used as evidence about the public-API OAuth path.

### Sources (all fetched 2026-09-02)

Notion: [Authorization](https://developers.notion.com/docs/authorization) ·
[Create a token](https://developers.notion.com/reference/create-a-token) ·
[Refresh a token](https://developers.notion.com/reference/refresh-a-token) ·
[Capabilities](https://developers.notion.com/reference/capabilities) ·
[Create a page](https://developers.notion.com/reference/post-page) ·
[Request limits](https://developers.notion.com/reference/request-limits) ·
[Changelog](https://developers.notion.com/page/changelog)

Asana: [OAuth](https://developers.asana.com/docs/oauth) ·
[OAuth scopes](https://developers.asana.com/docs/oauth-scopes) ·
[Rate limits](https://developers.asana.com/docs/rate-limits) ·
[Errors](https://developers.asana.com/docs/errors) ·
[Add members for project](https://developers.asana.com/reference/addmembersforproject) ·
[Add user for workspace](https://developers.asana.com/reference/adduserforworkspace)
