# Tier 0 status audit — ground truth at HEAD `a474c2c`

> **Type:** read-only audit. No code, no migration, and no `integration_inputs.md`
> change was made this pass. This file is the only artifact.
> **Audit date:** 2026-09-06
> **HEAD audited:** `a474c2c83ad6425eeb8187562bd3b0a4e5895906`
> **Method:** every claim below was re-derived this pass from files, `git`, or an
> actually-executed test run. No figure was carried forward from a prior session's
> summary. Where a prior summary conflicts with what was found, the conflict is
> stated in **§0** rather than quietly reconciled.

---

## §0 — MATERIAL DISCREPANCIES against prior session summaries

Five. Two are corrections to previously stated facts; three are things a prior
summary asserted more confidently than the evidence supports.

### 0.1 "4 commits unpushed" was WRONG. It is **26**.

`git rev-list --left-right --count origin/main...HEAD` -> `0	26`. `origin/main`
is at `68bc79b`; HEAD is `a474c2c`, **26 commits ahead, 0 behind**. Nothing has
been pushed since `68bc79b`.

The prior session's closing line ("Four commits are unpushed (`origin/main` is 23
behind)") was internally contradictory and wrong on both halves. It appears to
have counted only *that session's* commits. The full unpushed set spans the entire
Tier 0 build — every OAuth-broker commit from `0e34060` onward, the whole GCP
kill-switch sequence, and the GitHub work. **The entire Tier 0 body of work exists
only in this local clone.** That is the single highest-risk fact in this audit.

### 0.2 §1.1's implementation is `87edd2f`, NOT `928c3d5`/`b8a20e3`.

`git log --oneline -- src/skylize/tools/proxy.py` shows the spend gate was
introduced by **`87edd2f` "feat(tools): enforce the spend ceiling on the tool-call
path via SpendLedger"**, which long predates the OAuth sequence.

`928c3d5` and `b8a20e3` are a *different* mechanism. Their actual diffs
(`git show --stat`) touch `app/gcp/trigger.py`, `dal/gcp_containment.py`, and
migration `0025` — they are the GCP containment proposal *reacting to* a breach,
not the ceiling check itself. Attributing §1.1 to them (as this audit's own
commissioning prompt does, and as a prior session did) is a misattribution.

### 0.3 §1.1's DOC status is NOT resolved, contrary to "§1.1 already resolved".

`integration_inputs.md:108` still reads:

> `> **Section status: `[OWNER-DECISION-REQUIRED]` - this must be resolved BEFORE any spend-capable connector (Stripe above all) is written.**`

All three sub-questions remain open: **Q1.1a** (`:164`), **Q1.1b** (`:168`),
**Q1.1c** (`:172`), each still labelled `[OWNER-DECISION-REQUIRED]`. The sign-off
line is **blank**: `- 1.1 Spend ceiling on tool egress: ______________________`.

So the correct statement is: **the code exists (`tools/proxy.py:313-320`), the
owner decision does not.** §4.0 precondition 1 ("Section 1.1 resolved *and
implemented*") is therefore **half-discharged**. A prior session's "§4.0
precondition §1.1 already resolved (928c3d5/b8a20e3)" is wrong on the commit AND
on the resolution.

### 0.4 Stripe is NOT built. It has ZERO implementation.

The commissioning prompt states "Tier 0 build order now complete for: Slack,
Stripe, Google Drive, Asana, Notion, GCP kill-switch." For Stripe that is false.

- No module: `find src -ipath "*stripe*" -name "*.py"` -> nothing.
- No tool: no `stripe` tool id in `src/skylize/tools/builtin/`.
- No dependency: `grep -i stripe pyproject.toml` -> nothing.
- The only three `stripe` matches in `src/` are **prose in comments**:
  `app/credentials/asana_provider.py:50`, `app/principal/models.py:56` and `:74`
  (`"stripe.refund"` used as an example tool-id string), `bootstrap.py:884`.
- §2.1 status (`integration_inputs.md:209-210`) is
  `[OWNER-DECISION-REQUIRED] - account model DECIDED (Q2.1e, Q2.1f, 2026-08-28);
  remainder still open and BLOCKED additionally on 1.1`, with Q2.1a/b/c/d still
  open (`:220`, `:226`, `:230`, `:236`) and a blank sign-off line.

What exists is a design doc, `docs/06_integrations/stripe_connector_design.md`.
**The CFO Test's "Stripe spend ceiling" pillar is satisfied by the generic
`ToolSpendProfile` mechanism, not by any Stripe connector** — and that mechanism
is currently dormant (§5.1).

### 0.5 A named high-value test FAILED on first run. It is NOT a regression.

`test_an_ungoverned_org_cannot_stop_a_vm_at_all` failed on the first two attempts
this pass. It was **not** a code regression, and the chain of evidence is recorded
here so nobody re-litigates it:

1. First failure was `ConnectionRefusedError` in fixture setup — my env pointed
   alembic at a dead port.
2. Second failure, with the DB corrected, was
   `pydantic ValidationError: 1 validation error for Settings / redis_url / Input
   should be a valid string [input_value=None]`.
3. Root cause: the test builds `Settings(... redis_url=REDIS_URL ...)`
   (`tests/integration/test_gcp_killswitch_e2e_pg.py:185`) where `REDIS_URL` is
   `os.getenv("SKYLIZE_TEST_REDIS_URL")`. With Docker down that is `None`.
4. With the var set but no live Redis:
   `redis.exceptions.ConnectionError: Error 22 connecting to localhost:6379`.
5. **With Redis actually running: the whole file passes, 6/6, including this
   test.** See §3.3.

**The genuine finding underneath it** is a test-hygiene defect, not a regression:
that file imports `REDIS_URL` (`:49`) but carries **no `requires_redis` marker** —
it is gated only on `requires_app_role`. So it **FAILS instead of SKIPPING** when
Redis is absent, violating the convention CLAUDE.md relies on ("Postgres-backed
integration tests SKIP silently without their env vars"). Five of the six tests in
that file share the defect. This is very likely the same phenomenon as the
"28 Redis-dependent test failures" backlog item.

---

## §1 — GIT STATE

| Fact | Value | Evidence |
|---|---|---|
| Branch | `main` | `git branch --show-current` |
| HEAD | `a474c2c83ad6425eeb8187562bd3b0a4e5895906` | `git rev-parse HEAD` |
| `origin/main` | `68bc79b` | `git rev-parse --short origin/main` |
| Ahead / behind | **26 ahead, 0 behind** | `git rev-list --left-right --count origin/main...HEAD` -> `0	26` |
| Working tree | **CLEAN** | `git status --porcelain` -> empty |

### 1.1 The 26 unpushed commits (full list, newest first)

`a474c2c` GitHub App foundation · `bdcd4e9` approve §2.4 · `755e95f` rewrite §2.4 ·
`6ca81d4` fix E.3 flow · `99299dd` verify Q-NEW-2 · `e4d2173`
audit_github_readiness · `b8a20e3` durable containment cooldown · `928c3d5`
containment auto-hook · `ea5db63` GCP kill-switch trigger · `23d339c` GCP WIF
foundation · `6265d5d` WIF design doc · `628671f` GCP audit · `008827b` approve
§2.7 · `92c7706` Notion connector · `8f147d4` OAuth primitive extension ·
`621a87e` approve §2.6 · `273ff61` remove Asana addUser · `2107421` fix CI red ·
`a99edb1` Asana connector · `a8e7328` Notion/Asana audit · `2448819` Drive
connector · `a9e6547` approve §2.5 · `ac679be` draft §2.5 · `f6360b4` OAuth
infrastructure · `d44ec40` OAuth design doc · `0e34060` Drive audit.

### 1.2 Branch hygiene

- **71 local branches.**
- **58 are fully merged into `main`** (`git branch --merged main | grep -v '^\*' | wc -l`)
  and are deletable dead weight.
- **12 are NOT merged** (`git branch --no-merged main`): `audit/decision-consumer-gap`,
  `chore/import-linter-orphan-check`, `feat/capital-budget-reservation`,
  `feat/durable-governance`, `feat/grammar-gateway`, `feat/tool-dedup-convergence`,
  `feat/workflow-repository-postgres`, `fix/c3-investor-status`,
  `fix/knowledge-tenant-identity`, `fix/outbox-canonical-envelope`,
  `release/console-m1`, `worktree-bus-audit-gov`.

Those 12 hold work that is **not** in `main` and were not reviewed this pass. This
audit makes no claim about whether any of them should be merged or abandoned; it
records only that they exist and diverge.

---

## §2 — `integration_inputs.md` GROUND TRUTH

### 2.1 Section status table (verbatim, with line numbers)

| § | Line | Status | Date |
|---|---|---|---|
| 1.0 Current state | `:45` | `[CODE-VERIFIED]` (fact record, no decision needed) | — |
| **1.1** Spend ceiling | `:108` | **`[OWNER-DECISION-REQUIRED]`** | blank sign-off |
| **2.0** Provider classification | `:181` | **`[OWNER-DECISION-REQUIRED]`** | blank sign-off |
| **2.1** Stripe | `:209` | **`[OWNER-DECISION-REQUIRED]`** (account model decided; remainder open, BLOCKED on 1.1) | blank sign-off |
| **2.2** AWS / GCP | `:284` | **`[OWNER-DECISION-REQUIRED]` — HARD BLOCK** | blank sign-off |
| 2.3 Slack | `:328` | `[APPROVED]` | 2026-08-28 |
| 2.4 GitHub | `:412` | `[APPROVED]` | 2026-09-05 |
| 2.5 Google Drive | `:603` | `[APPROVED]` | 2026-08-31 |
| 2.6 Asana | `:726` | `[APPROVED]` | 2026-09-03 |
| 2.7 Notion | `:970` | `[APPROVED]` | 2026-09-04 |
| **3.0** Credential schema | `:1219` | **`[OWNER-DECISION-REQUIRED]`** (recorded, deliberately not implemented) | blank sign-off |

**Five approved, six not.** Note §2.2 is a self-declared HARD BLOCK yet the GCP
kill-switch shipped anyway — see §6.2.

### 2.2 The §2.6/§2.7 lesson, checked for EVERY section

The lesson: a section marked `[APPROVED]` must not leave sub-questions still
labelled `[OWNER-DECISION-REQUIRED]`, because a reader grepping for open questions
is then misled. Result of checking all five approved sections:

| § | Stale `[OWNER-DECISION-REQUIRED]` sub-labels? | Detail |
|---|---|---|
| **2.3 Slack** | **YES — 3** | Q2.3a `:337` (text says "ANSWERED" but label remains), **Q2.3b `:392`**, **Q2.3c `:399`** |
| **2.4 GitHub** | No | all sub-questions relabelled `[DECIDED]` |
| **2.5 Drive** | **YES — 4** | `:609`, **Q2.5b `:641`**, **Q2.5c `:661`**, **Q2.5e `:704`** |
| **2.6 Asana** | No | clean |
| **2.7 Notion** | No | Q2.7g `:1168` correctly `[DEFERRED - follow-up, non-blocking, owner 2026-09-04]` |

**Finding: the lesson was applied to §2.6, §2.7 and §2.4 but never applied
retroactively to §2.3 and §2.5.**

Severity: **documentation hygiene, not a blocking gap.** In both cases the section
*header* carries the resolution explicitly — §2.3's header (`:328-330`) says
"Post-only HITL notifier per Q2.3a/b/c below", and §2.5's (`:603-607`) says "write
actions (Q2.5b), governance narrative (Q2.5c), and the Decision Engine hook
(Q2.5d) decided below". So the decisions were made; only the inline labels are
stale. A reader who reads the header is correctly informed; a reader who greps is
not. **Flagged, not fixed** (this pass is read-only).

### 2.3 §4.0 preconditions — verbatim and current

```
## 4.0 - Preconditions before ANY connector code is written
`[RESEARCH-SUGGESTED]` In order:
1. Section 1.1 resolved and implemented - a spend-capable tool call reaches a
   synchronous ceiling check before egress.
2. Q2.2a answered - AWS/GCP account target fixed in writing.
3. Q3.0a answered - grant storage decided; migration written and reviewed separately.
4. Per-provider section reads `[APPROVED]`.
5. Idempotency strategy (Q2.1d) approved before any non-idempotent verb ships.
```

Discharge status, verified individually:

| Precondition | State | Evidence |
|---|---|---|
| 1. §1.1 resolved **and** implemented | **HALF.** Implemented ✓, resolved ✗ | code `tools/proxy.py:313-320` (`87edd2f`); doc `:108` still `[OWNER-DECISION-REQUIRED]`, Q1.1a/b/c open, sign-off blank |
| 2. Q2.2a answered | **NO** | `:295` still `[OWNER-DECISION-REQUIRED]`; §2.2 header `:284` still "HARD BLOCK"; classification table still `UNRESOLVED` for AWS **and** GCP (`:196`, `:197`) |
| 3. Q3.0a answered | **NO formally** | `:1240` still `[OWNER-DECISION-REQUIRED]`, §3.0 sign-off blank. In practice answered *de facto* three times over by migrations 0021/0024/0026, and §2.5's header (`:606`) asserts "Q3.0a's schema question is now answered by this section's own infrastructure" |
| 4. Per-provider `[APPROVED]` | **YES for the 5 shipped** | §2.3 `:328`, §2.4 `:412`, §2.5 `:603`, §2.6 `:726`, §2.7 `:970` |
| 5. Q2.1d idempotency approved before a non-idempotent verb ships | **NO** | `:236` still `[OWNER-DECISION-REQUIRED]`, and `:274` says "Q2.1c, Q2.1d - unchanged and still open" |

**So 4 of 5 §4.0 preconditions are not formally discharged, yet six connectors'
worth of code has shipped.** This is a real governance-process gap between the
document's own stated rules and what was built. It is recorded here as fact, not
as a recommendation to unship anything — §2.5's header shows at least Q3.0a was
consciously reasoned about rather than forgotten.

---

## §3 — CONNECTOR CODE, VERIFIED IN CODE NOT DOCS

### 3.1 What is actually registered in the tool registry

Enumerated from `grep -rhn 'tool_id="integration\.' src/skylize/tools/builtin/*.py`
plus the one constant-referenced id:

| Tool id | Module | Provider |
|---|---|---|
| `integration.hubspot_create_contact` | `hubspot_tools.py:191` | HubSpot (pre-Tier-0) |
| `integration.hubspot_search_contacts` | `hubspot_tools.py:226` | HubSpot |
| `integration.drive_create_file` | `drive_tools.py:315` | Drive |
| `integration.drive_share_file` | `drive_tools.py:390` | Drive |
| `integration.asana_create_task` | `asana_tools.py:458` | Asana |
| `integration.asana_create_project` | `asana_tools.py:511` | Asana |
| `integration.asana_add_project_member` | `asana_tools.py:575` | Asana |
| `integration.notion_create_page` | `notion_tools.py:530` | Notion |
| `integration.notion_create_database` | `notion_tools.py:587` | Notion |
| `integration.notion_append_blocks` | `notion_tools.py:634` | Notion |
| `integration.gcp_stop_instance` | `gcp_tools.py:54` (constant), `:152` (use) | GCP |

**11 registered tools.** Registry is wired at `bootstrap.py:823-826` with
`credential_vault`, `oauth_credentials`, `wif_repo`, `gcp_executor_factory`.

**No Slack tool, no Stripe tool, no GitHub tool.** All three absences are correct
and deliberate:
- **Slack** is a platform-level *notifier*, not an agent tool
  (`src/skylize/app/notifications/slack.py`, no `tool_id` in it), wired at
  `bootstrap.py:410` + `:840-841`. This matches §2.3's approval as a "post-only
  HITL notifier".
- **Stripe** does not exist at all (§0.4).
- **GitHub** is foundation-only by owner decision (§2.4 Q2.4b.3 / Q2.4e); the
  container carries `github_app_key` / `github_app_repo` (`bootstrap.py:384-385`)
  but they are deliberately **not** passed to `default_tool_registry`.

### 3.2 Test verification — what was ACTUALLY RUN this pass

An attempt to run the entire suite with live PG + Redis **hung** (283 min wall,
131 s CPU — blocked on a socket, not computing) and had to be killed. That is
itself a finding: **some integration test has an unbounded network wait.** I could
not isolate which within this pass. Consequently I verified per-suite rather than
by one whole-suite number, and I do **not** report a full-suite count for HEAD.

| Suite | Result | Infra used |
|---|---|---|
| `tests/unit` + `tests/contract` | **1530 passed, 2 skipped** | none needed |
| `tests/integration/test_github_app_pg.py` | **22 passed** | live PG |
| `tests/integration/test_containment_claims_pg.py` | **11 passed** | live PG |
| `tests/integration/test_gcp_killswitch_e2e_pg.py` | **6 passed** | live PG **+ live Redis** |
| `tests/unit/test_github_app_probe.py` (named) | **29 passed** | none |
| no-delete-branch tripwire (named) | **PASSED** | none |

The 2 skips in unit+contract are long-standing dead-code skips, not new:
`test_llm_agent_runner.py:61` ("runtime alt-stack is dead code") and
`test_memory_gateway.py:79` ("memory gateway is unwired from bootstrap").

**CI gate parity — 6 of 7 gates re-run fresh this pass, all PASS:**

| Gate | Result |
|---|---|
| 1 ruff `check src tests` | `All checks passed!` |
| 2 `lint-imports` | `Contracts: 5 kept, 0 broken` (309 files, 1505 deps) |
| 3 `check_forbidden_imports.py` | `OK: no direct LangChain/CrewAI imports` |
| 4 `check_all_modules_importable.py` | `OK: 252 modules import cleanly` |
| 5 `find_orphan_modules.py` | `OK: no new orphan modules (12 known, allowlisted)` |
| 6 `mypy src` (strict) | `Success: no issues found in 252 source files` |
| 7 `pytest` (no exclusions) | **NOT re-run whole this pass** — see the hang above; run per-suite instead |

**Infrastructure note, so this is reproducible.** The Postgres instance used by
the prior session was gone (machine restarted; no PostgreSQL service is
registered and `C:\Program Files\PostgreSQL\18\data` is an empty, uninitialised
directory). Docker's host port publishing is unreliable on this machine. I stood
up an **isolated cluster** via `initdb` into the scratchpad (`--locale=C` is
required — the Turkish system locale contains non-ASCII and `initdb` refuses it)
and ran it on 5432 so the repo's default DSN resolves to it. **All 26 migrations,
including `0026`, applied cleanly to a virgin PG 18.4 cluster** — which verifies
`0026` works on a fresh database, not merely as an increment. Note CI uses PG 16;
RLS semantics are identical but the version differs.

RLS was proven under the correct role: `skylize_app` with
`rolsuper=f, rolbypassrls=f`, and it is not the table owner — so the isolation
tests prove something, per CLAUDE.md's requirement.

### 3.3 GCP kill-switch — the four commits, and the two named regression checks

All four commits are present and reachable from HEAD (`git log --oneline`):
`23d339c` foundation, `ea5db63` trigger, `928c3d5` auto-hook, `b8a20e3` durable
cooldown.

| Named check | Location | Result at HEAD |
|---|---|---|
| Ungoverned-org fail-closed | `tests/integration/test_gcp_killswitch_e2e_pg.py:451` `test_an_ungoverned_org_cannot_stop_a_vm_at_all` | **PASSES** (with live Redis; see §0.5 for the false alarm) |
| Multi-replica race | `tests/integration/test_containment_claims_pg.py:376` `test_two_replicas_racing_the_same_breach_only_one_wins` | **PASSES** |

Full files: killswitch e2e **6/6 passed**, containment claims **11/11 passed**.

### 3.4 GitHub foundation — intact, with the two named checks

`a474c2c` is HEAD. Both named checks pass:

| Named check | Location | Result |
|---|---|---|
| connection_state probe | `tests/unit/test_github_app_probe.py` | **29 passed** |
| no-delete-branch tripwire | `tests/contract/test_stateless_agents_no_oauth_access.py:421` | **PASSED** |

Key implementation facts re-verified in code:
- The probe reads `current_user_can_bypass`, **not** `bypass_actors` —
  `src/skylize/app/github/probe.py:357`. The empirical justification is recorded
  in that file's docstring at `:19-64`.
- The five approved states are enforced by a DB CHECK —
  `migrations/versions/0026_github_app_installations.py:146-149`.
- The globally-unique installation id (the tenancy control) — `:177`.

---

## §4 — OUTSTANDING MANUAL / OWNER ACTIONS

Split honestly into what the repo *can* answer and what it cannot.

### 4.1 Code-verifiable: GitHub App is NOT registered/configured

- Settings exist but default empty: `config.py:225` `github_app_id: str = ""`,
  `:229` `github_app_private_key_pem: str = ""`, `:231` `github_app_slug`,
  `:233` `github_api_base_url`.
- `grep -c GITHUB_APP .env` -> **0**. `grep -c GITHUB_APP .env.example` -> **0**.

So **no App is configured in this repo**, consistent with registration not having
happened. **Caveat stated plainly:** absence of local config is *not* proof no App
exists on GitHub. Whether a Skylize App has been registered on the
`skylizeai-dev` account is **not verifiable from inside this repo** — an OAuth
token cannot enumerate Apps it does not own, and App registration requires the
browser manifest flow. **Requires owner confirmation.**

**Minor finding:** the four new `SKYLIZE_GITHUB_APP_*` settings were added to
`config.py` but **not** to `.env.example` (0 matches). Every other secret-bearing
setting family is documented there. Worth closing for onboarding hygiene.

### 4.2 NOT code-verifiable — requires owner/platform confirmation

| Item | Why it cannot be verified here |
|---|---|
| Probe repo `skylizeai-dev/ruleset-bypass-probe` deleted? | External GitHub state. Last known: set back to **private** with its probe workflow removed; **not deleted** (the session token lacked `delete_repo` scope). **Requires owner confirmation.** |
| `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY` present on Railway staging | Deployment-environment state, outside this repo. **Requires owner/platform confirmation.** Note the consequence if absent: `bootstrap.py::resolve_credential_encryption_key` **fails the boot** on a non-memory backend rather than minting an ephemeral key — so a missing key is loud, not silent. |
| Whether a real GitHub App exists on the GitHub org/account | See 4.1. |

### 4.3 Every deployment-dependent setting, enumerated rather than assumed

All default to empty/dev values in `config.py`; **none of their real
deployment-environment values are verifiable from this repo.** Listed explicitly
so none is silently presumed set:

Secrets / keys — `governance_signing_key_pem` (`:86`), `jwt_secret` (`:109`),
`credential_encryption_key` (`:121`), `wif_signing_key_pem` (`:182`),
`github_app_private_key_pem` (`:229`), `anthropic_api_key` (`:287`).

OAuth client credentials — `google_oauth_client_id`/`_secret` (`:140-141`),
`asana_oauth_client_id`/`_secret` (`:149-150`),
`notion_oauth_client_id`/`_secret` (`:157-158`).

Slack notifier — `slack_bot_token` (`:130`), `slack_approval_channel_id` (`:131`).
Both empty = feature off; exactly one set = boot refused.

Infrastructure — `db_url` (`:46`), `db_app_url` (`:47`), `redis_url` (`:48`).

GitHub — `github_app_id` (`:225`), `github_app_slug` (`:231`),
`github_api_base_url` (`:233`).

---

## §5 — BACKLOG, RE-VERIFIED

### 5.1 `ToolSpendProfile` assigned to NO tool — **STILL TRUE**

`grep -rn "spend=ToolSpendProfile\|spend=Tool" src/skylize/tools/builtin/*.py` ->
**no match.** Not one of the 11 registered tools declares a spend profile.

The mechanism is fully built (`tools/base.py:78` `ToolSpendProfile`; the gate at
`tools/proxy.py:313-320`, deliberately the **last** gate before dispatch) and is
**dormant** — no tool triggers it.

This has a direct consequence for the CFO Test that should not be glossed: the
"Stripe spend ceiling" pillar depends on a mechanism that **currently fires for
nothing**, because there is no spend-capable tool registered (and no Stripe
connector at all, §0.4). The GCP stop verb deliberately carries no spend profile —
`gcp_tools.py:177` records the reason: the ceiling breach is what *triggers* the
containment, so gating containment on the ceiling could refuse the very action the
breach called for. That reasoning is sound and is not the gap; the gap is that
nothing else declares one either.

### 5.2 `sweep_expired` cross-tenant/RLS defect — **STILL UNFIXED, STILL UNCALLED**

Protocol at `src/skylize/app/principal/spend.py:101`; implementation at `:421`.

Two defects, both confirmed present at HEAD by reading `:421-447`:

1. **No tenant binding.** It opens a raw pool connection —
   `async with self._pool.acquire() as conn` (`:424`) — **not**
   `tenant_session(org_id)`. So `skylize.org_id` is never set.
2. **No `org_id` predicate.** The UPDATE selects
   `WHERE state = 'held' AND expires_at < $1 ORDER BY expires_at LIMIT $2` across
   **all tenants**, then decrements `spend_envelope.reserved_minor` per row with no
   tenancy check.

Under RLS as `skylize_app` with no org bound, `current_setting('skylize.org_id',
true)` is NULL, so the policy matches nothing and the sweep would silently reclaim
**zero** holds. Run as a superuser or the table owner it would sweep **across
every tenant**, ignoring isolation entirely. Either way it is wrong.

**Not called anywhere in `src/`.** `grep -rn sweep_expired src/ tests/` yields only
its own definition plus comments referring to it (`spend.py:25`,
`tools/proxy.py:349`, `:376`, `:824`) and two test docstrings. So it is latent, not
live — the reason this is a backlog item rather than an active incident.

### 5.3 Redis-dependent test failures — **CONFIRMED, and now characterised**

Confirmed as a real defect class, and §0.5 pins the mechanism precisely:
`tests/integration/test_gcp_killswitch_e2e_pg.py` imports `REDIS_URL` (`:49`) and
feeds it to `Settings(...)` (`:185`) but has **no `requires_redis` marker** —
gated only on `requires_app_role`. Without Redis it raises
`pydantic ValidationError` on `redis_url=None`, or `redis.exceptions.ConnectionError`
if the URL is set but nothing listens. **5 of the 6 tests in that file** fail this
way; all 6 pass with Redis up. The fix is a one-line `requires_redis` gate so the
suite skips honestly instead of failing.

I did not enumerate all "28" such tests this pass — the whole-suite run needed to
count them is the one that hung (§3.2). **The count 28 is therefore unverified at
HEAD**; the defect class is verified.

### 5.4 mypy / orphan-module status — **GREEN, re-verified fresh**

Not assumed from a prior state: `mypy src` -> `Success: no issues found in 252
source files`; `find_orphan_modules.py` -> `OK: no new orphan modules (12 known,
allowlisted)`. See §3.2 for all six gates.

### 5.5 New this pass: an integration test with an unbounded network wait

The full-suite run with live PG + Redis hung indefinitely (283 min wall / 131 s
CPU) and required killing. Not isolated to a specific test this pass. This is a
genuine CI-reliability risk and belongs on the backlog.

---

## §6 — TIER 0 COMPLETION MATRIX (CFO Test's four pillars)

Columns: **(a)** documented + approved · **(b)** code-complete · **(c)** test-proven
at HEAD · **(d)** demo-ready today.

| Pillar | (a) | (b) | (c) | (d) |
|---|---|---|---|---|
| **Stripe spend ceiling** | **NO** — §2.1 `[OWNER-DECISION-REQUIRED]` (`:209`); §1.1 also unapproved (`:108`) | **Mechanism yes, connector NO.** Gate at `tools/proxy.py:313-320`; **no Stripe code at all** (§0.4); **no tool declares a spend profile** (§5.1) | Mechanism tested (`test_tool_proxy_spend*.py` exist); **no Stripe path to test** | **NO** |
| **GCP kill-switch** | **Partial** — §2.2 is still a self-declared **HARD BLOCK** (`:284`), Q2.2a unanswered (`:295`), GCP `UNRESOLVED` in the classification table (`:197`). Design doc + audit exist and the build proceeded on those | **YES** — `23d339c`+`ea5db63`+`928c3d5`+`b8a20e3`; verb `integration.gcp_stop_instance` registered (`gcp_tools.py:54`) | **YES** — killswitch e2e 6/6, containment claims 11/11, both named regression checks pass | **YES**, given PG+Redis and a real customer WIF federation |
| **Slack HITL** | **YES** — §2.3 `[APPROVED]` 2026-08-28 (`:328`), with 3 stale sub-labels (§2.2 of this report) | **YES** — `app/notifications/slack.py`, wired `bootstrap.py:410`, `:840-841`. Correctly a notifier, not a tool | **YES** (in unit+contract, 1530 passed) | **YES** if `slack_bot_token` + `slack_approval_channel_id` are set in the deployment (**not repo-verifiable**, §4.3) |
| **GitHub prod-push prevention** | **YES** — §2.4 `[APPROVED]` 2026-09-05 (`:412`), all sub-questions `[DECIDED]`, sign-off filled | **Foundation only, by design.** Key custody + token minting + probe + table `0026`. **No tool registered, no webhook, no PR-merge verb** — all deliberate | **YES for what exists** — 29 probe tests, 22 RLS tests, tripwire, all pass. **Live token minting UNVERIFIABLE** without a registered App | **NO — blocked on manual App registration** |

### 6.1 Could the CFO Test demo run today? **No.**

Two independent blockers:

1. **GitHub pillar cannot run at all.** No App is registered (§4.1), and
   registration *requires* a human in a browser — GitHub offers no API for it. Until
   then there is no installation id, no installation token, and nothing to probe.
   Note the Tier 1/Tier 2 *prevention* property does not depend on Skylize code and
   was empirically proven at `99299dd` — but demonstrating it *through Skylize*
   needs the App.
2. **Stripe pillar has no connector.** The spend-ceiling mechanism is real but
   dormant (§5.1), and there is no Stripe code to move money (§0.4). What could be
   demonstrated today is a ceiling breach on a *different* spend-capable tool — and
   no tool currently declares one.

The **GCP kill-switch** and **Slack HITL** pillars are genuinely demo-ready
(subject to deployment env vars and a real GCP federation).

### 6.2 A process observation worth recording

The GCP kill-switch shipped while §2.2 — its own provider section — still reads
`[OWNER-DECISION-REQUIRED] - HARD BLOCK` with Q2.2a ("Target account") unanswered
and GCP marked `UNRESOLVED` in the classification table. The work was instead
governed by `docs/audits/audit_gcp_killswitch_readiness.md` and
`gcp_wif_killswitch_design.md` (owner-approved at `6265d5d`). That may well have
been the right call, but the result is that **the document that claims to be "the
sole source of connector rules" (`:8`) does not cover the connector that shipped.**
Either §2.2 should be updated to reflect the WIF decision, or its HARD BLOCK
banner should be qualified — otherwise the next session reads a hard block over
shipped code.

---

## §7 — WHAT IS GENUINELY NEXT

### 7.1 The single next actionable item

**Push the 26 commits to `origin/main`.**

Rationale, from verified facts only: the entire Tier 0 body of work — every
connector, the whole GCP kill-switch, the GitHub foundation, and six approved doc
sections — exists **only in this local clone** (§1). The working tree is clean and
all six re-runnable CI gates are green (§3.2), so there is nothing to fix first.
Every other candidate task is smaller than the risk of holding 26 unpushed commits
on one machine.

**Blocked by:** nothing technical. It needs the owner's go-ahead, since pushing is
outward-facing and was not authorised in this pass.

### 7.2 Then, in order of value

1. **Register the GitHub App** (owner, browser — `scripts/register_github_app.py
   --serve` automates everything except the click). This is the sole blocker on the
   fourth demo pillar, and it also unblocks the only untestable part of the GitHub
   foundation: live installation-token minting.
2. **Decide the Stripe pillar.** It is the largest gap between the demo narrative
   and the code. Either build the connector (needs §2.1 + §1.1 + Q2.1d approved
   first, per §4.0), or re-scope the demo's "spend ceiling" pillar onto a
   spend-capable tool that actually exists — which today means declaring a
   `ToolSpendProfile` on something (§5.1).
3. **Fix the Redis test gating** — add `requires_redis` to
   `test_gcp_killswitch_e2e_pg.py`. One line; converts 5 misleading failures into
   honest skips (§5.3).
4. **Isolate the hanging integration test** (§5.5). It currently makes a full local
   suite run impossible, which is how this audit lost its whole-suite count.
5. **Doc hygiene, batched:** relabel §2.3/§2.5 stale sub-questions to `[DECIDED]`
   (§2.2); resolve §1.1's status to match shipped reality (§0.3); qualify §2.2's
   HARD BLOCK banner (§6.2); add `SKYLIZE_GITHUB_APP_*` to `.env.example` (§4.1).
6. **Branch cleanup:** 58 merged branches are deletable; 12 unmerged ones need a
   keep-or-abandon decision (§1.2).
7. **`sweep_expired`** (§5.2) — real defect, but latent and uncalled, so it ranks
   below everything above.

---

## Appendix — what this audit could NOT verify

Stated explicitly so absence of evidence is not read as evidence of absence:

1. Whether a Skylize GitHub App exists on GitHub (§4.1).
2. Whether `skylizeai-dev/ruleset-bypass-probe` has been deleted (§4.2).
3. Any deployment-environment variable's real value, on Railway or elsewhere
   (§4.2, §4.3).
4. A whole-suite pass/fail count at HEAD — the run hung and was killed (§3.2).
   Per-suite results are reported instead.
5. The "28 Redis-dependent failures" figure. The defect class is confirmed; the
   count is not (§5.3).
6. Live GitHub installation-token minting — needs a registered App (§6, GitHub row).
7. CI gate 7 (`pytest`, no exclusions) as a single run; gates 1-6 were re-run and
   pass (§3.2).
8. The contents or health of the 12 unmerged branches (§1.2).
