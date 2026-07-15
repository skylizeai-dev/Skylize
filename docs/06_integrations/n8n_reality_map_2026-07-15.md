# n8n Integration — Reality Map (implemented vs target)

**Audit date:** 2026-07-15
**Type:** Read-only, due-diligence-grade. No code changed.
**Auditor role:** Principal Integration/Platform Engineer.
**Method:** Static read of both trees (`src/skylize/` backend + `website/` BFF), the
integration/boundary docs, `config.py`, `.env.example`, and `website/docs/DEPLOY_RAILWAY.md`.
Every claim below carries `file:line` evidence.

---

## 0. Audit provenance & branch note (read this first)

- **Checked-out branch:** `feat/durable-governance` @ `b025c43e` — **not** `release/console-m1`
  as the task framing assumed. I did **not** switch branches (read-only constraint).
- **Does this matter?** No, for this audit. I diffed every n8n-relevant path between
  `HEAD` and `release/console-m1`: the routes, the BFF, `n8n.md`, `system_boundaries.md`,
  `DEPLOY_RAILWAY.md`, and the n8n config keys are **byte-identical** on both branches.
  The only deltas are unrelated (`temporal_*` settings and an `SKYLIZE_OPENAI_API_KEY`
  comment block). **All findings below hold identically on `release/console-m1`.**
- **NOT IN SCOPE (explicitly):** the live n8n Cloud state — whether workflow
  `T2Vam3MkdBIoR1TT` exists or is active on `skylize.app.n8n.cloud`. The live n8n API/MCP
  was never contacted; no workflow was touched; no authentication to n8n was attempted.
  That is a separate manual check by the human operator.

---

## 1. TOP DD-SENSITIVE FINDING (stated plainly)

**An authenticated console session can create, activate, or delete arbitrary n8n workflows —
an arbitrary-code-execution surface — with no governance gate whatsoever.**

`website/src/app/api/console/workflows/route.ts:66-106` is a server-side bridge to n8n's
**admin** public REST API. A caller holding a valid `skylize_console` session cookie can:

- **create** a workflow — `POST {N8N_API_URL}/api/v1/workflows` (`route.ts:78-84`)
- **activate** a workflow — `POST /api/v1/workflows/{id}/activate` (`route.ts:87-93`)
- **discard/delete** a workflow — `DELETE /api/v1/workflows/{id}` (`route.ts:96-101`)

n8n workflows can contain arbitrary **Code** and **HTTP Request** nodes; the route's own
header comment concedes this (`route.ts:14-16`: "drives privileged n8n admin actions …
which can run arbitrary Code/HTTP nodes"). The **only** controls on this path are:

1. a session-cookie check (middleware `proxy-gate.ts:27-55` + authoritative re-check in
   `handler.ts:77-79`), and
2. zod **shape** validation of the request body (`route.ts:26-37`, `handler.ts:82-100`).

There is **no** GovernanceToken, **no** Decision Engine call, **no** org scoping, and **no**
contract check anywhere on this path (`website/src/app/api/console/workflows/route.ts`
imports none of them; the only governance-related files under `website/src` are
`proxy-gate.ts` (session) and marketing copy in `hero-workflow-demo.tsx` — neither touches
this route). The credential used is a standing n8n admin key `N8N_API_KEY` presented as
`X-N8N-API-KEY` (`route.ts:23-24,40-46`).

This directly contradicts the architecture invariant that outbound egress happens **only**
through governance-scoped Integration Adapters in the infrastructure layer
(`docs/02_architecture/system_boundaries.md:208-216`).

**Severity: HIGH (latent).** Mitigating nuance, stated honestly: the endpoint is currently
**dormant** — no UI button in the tracked tree calls create/activate/discard (see §4, the
"button" analysis). But the endpoint is **live and reachable** by any authenticated session
via a direct `POST`, and is deployed whenever `N8N_API_URL`/`N8N_API_KEY` are set. Dormant ≠
absent.

---

## 2. Direction × Layer matrix

| Path | Layer | Exists? | Auth mechanism | Governed? (Decision Engine / GovernanceToken) | Primary evidence |
|---|---|---|---|---|---|
| **INBOUND** `GET /api/v1/agent-prompts/{id}` | backend `src/` (edge) | **Yes** | Static API key — `X-Skylize-API-Key` vs `n8n_api_key` | **N/A** — read-only, no side effect; the LLM call it feeds runs **inside n8n, outside** the governance chain | `agent_prompts.py:19-55`; registered `gateway.py:76` |
| **INBOUND** `POST /api/v1/knowledge/ingest` | backend `src/` (edge) | **Yes** | HMAC-SHA256 — `X-Hub-Signature-256` vs `knowledge_webhook_secret`, constant-time | **UNGOVERNED by Decision Engine** — direct DAL write to the vector store, gated by HMAC + an LLM content-screen, **not** the event/Decision-Engine path | `knowledge.py:28-65`; `knowledge_ingestion.py:31-47`; registered `gateway.py:81` |
| **OUTBOUND-BACKEND** Skylize `src/` → n8n | backend `src/` (adapter) | **No — does not exist** | — | **N/A (absent)** | grep of `src/skylize` for n8n egress: zero hits (`n8n.md:46-55` self-confirms) |
| **OUTBOUND-BFF** console → n8n admin API (create/activate/delete) | `website/` (Next.js BFF) | **Yes** | Session cookie (`skylize_console`) + zod shape only | **UNGOVERNED** — no token, no Decision Engine, no org scope | `route.ts:66-106`; auth `proxy-gate.ts:27-55` + `handler.ts:66-113` |
| _(contrast)_ `POST /api/console/workflows/creative` | `website/` (BFF) | Yes | Session cookie | **N/A to n8n** — proxies to backend `/api/v1/workflows/creative`; **no n8n involvement** | `creative/route.ts:29-44`; caller `creative-runner.tsx:58` |

**Verdict per path:**
- INBOUND agent-prompts → **N/A** (read-only; but enables an ungoverned in-n8n LLM call).
- INBOUND knowledge/ingest → **UNGOVERNED** (by the canonical Decision Engine; has HMAC + content-gate compensating controls).
- OUTBOUND-BACKEND → **N/A (absent).**
- OUTBOUND-BFF → **UNGOVERNED.** ← the DD-sensitive one.

---

## 3. INBOUND (n8n → Skylize) — detail

Both inbound endpoints are real and wired into the FastAPI app
(`src/skylize/edge/gateway.py:76,81` include `agent_prompts.router` and `knowledge.router`).

### 3.1 `GET /api/v1/agent-prompts/{agent_id}`
- **File:** `src/skylize/edge/routes/agent_prompts.py:38-55`; auth dep `:19-35`.
- **Auth:** static API key. `_verify_api_key` compares the `X-Skylize-API-Key` header to
  `settings.n8n_api_key` (`:23,30`). **Fail-closed:** returns `503` if the key is unconfigured
  (`:27-29`), `401` on a missing/wrong key (`:30-35`). **This is a static key, not HMAC** —
  matching `n8n.md §2.1` and correcting the mixed framing in `n8n.md §3`.
- **What it does:** resolves the agent's system prompt + metadata (authority level, model
  tier, token budget) from `MVP_REGISTRY` via `AgentPromptService.get_prompt`
  (`app/agent_prompts/service.py:15-54`). Pure read; no persistence, no side effect.
- **Governance:** does **not** touch the Decision Engine. Confirms the prior finding: the
  actual LLM call happens **inside n8n**, so the model invocation, its token budget, and its
  tool use run **outside** Skylize's governance chain. Skylize only hands n8n the prompt
  text; enforcement of the budget/escalation rules embedded in that prompt is advisory once
  it leaves the edge (`agent_prompts.py:1,40` docstrings; `n8n.md §2.1`).

### 3.2 `POST /api/v1/knowledge/ingest`
- **File:** `src/skylize/edge/routes/knowledge.py:35-65`; HMAC helper `:28-32`.
- **Auth:** HMAC-SHA256 body signature. `X-Hub-Signature-256` verified against
  `knowledge_webhook_secret` with `hmac.compare_digest` (constant-time) (`:31-32,50-59`).
  **Fail-closed:** `503` if the secret is unconfigured (`:44-49`), `401` on bad/missing
  signature (`:52-59`).
- **What it does:** persists a document into the `platform_knowledge` vector store —
  `KnowledgeIngestionService.ingest` → `LLMContentGate.check` (prompt-injection screen) →
  embed → Qdrant upsert (`memory/knowledge_ingestion.py:31-47`). **This is a genuine Skylize
  side effect** (a durable write later retrieved into agent context).
- **Governance:** the side effect does **not** route through the Decision Engine or the event
  bus — it is a direct DAL write, gated by HMAC + the content screen only. Note the explicit
  admission at `knowledge.py:53-54`: the `governance.integration_bad_signature` event that
  `n8n.md §7` / `system_boundaries.md §4.6:230-231` promise on a bad signature is **not**
  emitted — it's a deferred `TODO`. So even the audit event on rejection is missing today.

---

## 4. OUTBOUND-BFF (Next.js console → n8n) — detail

- **File:** `website/src/app/api/console/workflows/route.ts` (whole file). Committed in
  `a614b667` ("feat(console): operator console UI + BFF …"); **git-tracked and not
  gitignored** (verified via `git ls-files` / `git check-ignore`). _Tooling note: ripgrep
  silently skipped this file during grep sweeps despite it being tracked plain text — it was
  found by direct read + `git ls-files`. Do not rely on a grep-only sweep of `website/` for
  this path._
- **Target:** `${N8N_API_URL}/api/v1{path}` with header `X-N8N-API-KEY: ${N8N_API_KEY}`
  (`:39-50`), i.e. n8n's **admin** REST API on the configured instance (e.g.
  `https://skylize.app.n8n.cloud`, per the comment at `:19` and `DEPLOY_RAILWAY.md:31`).
- **Actions:** `create` (`:78-84`), `activate` (`:87-93`), `discard`→DELETE (`:96-101`),
  dispatched by a discriminated-union body (`:33-37`).
- **Auth guarding the route (two layers, both session-based):**
  1. Middleware `proxy-gate.ts:27-55` — optimistic, fail-closed session check on all
     `/api/console/*` (except login/session), and strips spoofable `x-skylize-user` /
     `x-skylize-org` headers (`:19-24`).
  2. Authoritative `consoleRoute` in `handler.ts:66-113` — method guard (`:73-75`), session
     verify of the signed `skylize_console` cookie (`:35-40,77-79`), zod validation
     (`:82-100`), 503 if n8n unconfigured (`route.ts:70-75`).
- **Governance:** **none.** No GovernanceToken, no Decision Engine, no org binding, no
  contract validation. The `X-N8N-API-KEY` is a standing admin credential; nothing scopes
  what workflow definition may be created (`nodes`/`connections` are `z.unknown()`,
  `route.ts:28-29`). → **Verdict: UNGOVERNED.**
- **Is there a live "button"?** **No, not in the tracked tree.** The only console component
  that calls the `/api/console/workflows` namespace is `creative-runner.tsx:58`, and it calls
  the **`/creative`** sub-route (backend proxy, §5), not the n8n admin route. The
  `activate`/`discard` language in `hero-workflow-demo.tsx:203,218` is **landing-page
  marketing copy**, not wired to this endpoint. `DEPLOY_RAILWAY.md:31` calls the endpoint
  "the console workflow builder," but the builder UI that would drive create/activate/discard
  is **not present** in `website/src`. **The endpoint is live and reachable but dormant.**

---

## 5. Not-an-n8n-path (flagged to prevent conflation)

`website/src/app/api/console/workflows/creative/route.ts:29-44` shares the `workflows` URL
prefix but has **zero** n8n involvement: it proxies to the backend
`POST /api/v1/workflows/creative` via `skylizeFetch` (`:34-37`). "Workflow" here means the
internal Temporal/orchestrator creative workflow, governed on the backend side — **not** n8n.
Do not count this as a Skylize→n8n path.

---

## 6. Environment variables

| Env var | Layer | Direction & meaning | Read at | Documented in |
|---|---|---|---|---|
| `SKYLIZE_N8N_API_KEY` | backend | **Inbound.** The key **n8n presents to Skylize** on agent-prompts. | `config.py:104` (`n8n_api_key`); consumed `agent_prompts.py:23,30` | `.env.example:30` |
| `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET` | backend | **Inbound.** HMAC secret for knowledge/ingest. | `config.py:112`; consumed `knowledge.py:44,52` | `.env.example:34` |
| `SKYLIZE_AGENT_PROMPTS_HMAC_SECRET` | backend | **Inbound (declared, unused).** Intended `X-Skylize-Signature` HMAC on agent-prompts. | `config.py:113` (`agent_prompts_hmac_secret`) — **only hit repo-wide; no consumer** | **Not** in `.env.example` |
| `N8N_API_URL` | **BFF** | **Outbound.** n8n admin base URL the console calls. | `route.ts:23` | `website/docs/DEPLOY_RAILWAY.md:31` — **not** in `.env.example` |
| `N8N_API_KEY` | **BFF** | **Outbound.** The key **Skylize presents to n8n's** admin API. | `route.ts:24` | `DEPLOY_RAILWAY.md:32` — **not** in `.env.example` |

**Backend and BFF use DIFFERENT n8n credentials, in opposite directions:**
- Backend `SKYLIZE_N8N_API_KEY` is a secret **n8n → Skylize** (Skylize validates the caller).
- BFF `N8N_API_KEY` is a secret **Skylize → n8n** (n8n validates Skylize as admin).
- They live under different prefixes (backend `SKYLIZE_`, `config.py:19`; BFF raw `N8N_*`)
  and are documented in **different files** (`.env.example` vs `DEPLOY_RAILWAY.md`). A DD
  reader who inspects only `.env.example` will **never see the outbound n8n admin
  credential** — a documentation blind spot that helps hide the §1 finding.

**Minor finding:** `agent_prompts_hmac_secret` (`config.py:113`) is declared but consumed
**nowhere** — the agent-prompts route uses the static key path only. Dead/latent config,
suggesting an intended HMAC upgrade on agent-prompts that was never wired.

---

## 7. Master Orchestrator / workflow `T2Vam3MkdBIoR1TT`

- `T2Vam3MkdBIoR1TT`: **zero hits** repo-wide (ripgrep, no matches).
- `"Master Orchestrator"` (case-insensitive): **zero hits** repo-wide. Every `Orchestrator`
  hit is the **internal Temporal orchestrator** (`src/skylize/app/orchestrator/…`), unrelated
  to n8n.
- **Confirms the prior finding and extends it:** the Master Orchestrator workflow and its ID
  are **n8n Cloud state, not source-controlled** anywhere in this repo (neither `src/` nor
  `website/`).

---

## 8. Governance-gap summary — doc claims vs implemented reality

**Claim A — `docs/06_integrations/n8n.md:81-82` (§4):** "An n8n-driven action that would cause
a Skylize side effect still routes through the normal event/Decision-Engine path — n8n cannot
bypass governance."
- **agent-prompts:** read-only, no side effect → claim vacuously holds.
- **knowledge/ingest:** **does** cause a side effect (vector-store write) and does **not**
  route through the Decision Engine/event bus (`knowledge.py:61-65` → direct
  `knowledge_ingestion.py` DAL write). Compensating controls are HMAC + `LLMContentGate`, not
  the canonical enforcement point. → **Claim not literally satisfied** for the one inbound
  path that has a side effect.

**Claim B — `docs/02_architecture/system_boundaries.md:208-216, 224-228` (§4.6):** outbound
egress happens "only by the infrastructure layer's Integration Adapters," and "Skylize
triggers n8n workflows with a signed payload … n8n holds no Skylize credentials."
- The documented signed-trigger **adapter does not exist** (`n8n.md:46-55` self-confirms;
  §3 above).
- The **implemented** Skylize→n8n path is the BFF admin REST call (`route.ts`), which is
  **not** an infrastructure Integration Adapter, carries **no** governance token, and is the
  **inverse** of the documented model: here **Skylize holds an n8n credential** and drives
  n8n's admin API directly. → **Claim contradicted by the implemented reality**, at a layer
  (`website/` BFF) the integration doc never mentions.

**Documented-vs-implemented honesty check:** `n8n.md` is admirably hedged about `src/` — §2.2
(`:46-55`) explicitly labels the outbound trigger "aspirational, not yet implemented," and the
§3 blockquote (`:64-69`) flags that §3–§7 mix implemented inbound with unimplemented outbound.
**But `n8n.md` is scoped to `src/` only** — it is entirely **silent** about the `website/` BFF
admin path. The doc is accurate about the backend and blind to the BFF; the reality map's job
was to surface exactly that gap.

---

## 9. Hard-exit-gate status

- [x] **Both trees covered** — `src/skylize/` (backend) **and** `website/` (BFF), plus docs,
      `config.py`, `.env.example`, `DEPLOY_RAILWAY.md`.
- [x] **Every claim carries `file:line` evidence.**
- [x] **Live n8n Cloud never contacted** — no API/MCP call, no auth, no workflow touched.
- [x] **One new file only** — this report. No source/doc/config file was modified.
      _(Pre-existing working-tree noise unrelated to this audit and present at session start —
      `src/skylize.egg-info/*` build artifacts, `.claude/`, a stray `setuptools egg-info`
      artifact — was **not** created or touched by this audit and is not part of the commit.)_
