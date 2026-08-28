# Skylize — Thiel Fellowship Technical Brief

> **STATUS: STALE — NOT RECONCILED WITH THE 2026-08-28 REPO AUDIT.**
> Contains technical claims (test counts, coverage, merged branches,
> OPA's role as production arbiter) that the audit has since
> contradicted. Do not distribute until reconciled.

**Status:** Application material · technical specification
**Describes commit:** `37f3d2d` on `feat/durable-governance`
**Method:** every claim carries a `file:line` citation, or is marked **UNVERIFIED**.
Code is ground truth; docs and ADRs are claims tested against it.
**Related:** [technical_due_diligence.md](./technical_due_diligence.md) · [yc_overview.md](./yc_overview.md) · [../REPO_STATE.md](../REPO_STATE.md)

---

## 0. The one-paragraph version

Skylize is an AI-native Business Operating System: a governed organization of
autonomous agents that runs a company's operational work — creative, growth,
sales, finance ops — under human authority. The thing that makes it a system
rather than a demo is that **agent intent cannot become a real-world action
without traversing an explicit, cryptographically-signed, auditable governance
path**. Twenty-one agents, each defined by a frozen contract. Every side effect
gated by an ECDSA P-384 token. Every decision produced by a deterministic,
replayable evaluator. Every action written to an append-only, RLS-isolated
audit log. A kill switch that overrides all authority, including executive
agents. 27,942 lines of source, 31,083 lines of tests, 1,408 passing.

---

## 1. The contrarian thesis

> **The bottleneck on autonomous AI is not model capability. It is that no
> business will hand a real budget and a real brand to a system it cannot
> audit, override, or stop.**

Almost everyone building agents in 2026 is optimizing the wrong variable. The
field competes on reasoning quality, tool-use breadth, and benchmark scores —
on making the agent *smarter*. Meanwhile the actual adoption blocker sits
somewhere else entirely: a CFO will not let a language model near an ad
account, not because the model writes bad copy, but because there is no
mechanism by which the model's action becomes *accountable*.

The consensus view is that governance is compliance overhead — a wrapper you
bolt on after product-market fit. The contrarian view, and Skylize's bet, is
that **governance is the product**, and that it is the one component that
cannot be retrofitted. You cannot add "every action was signed, scoped,
budgeted, and replayable" to a system that was not built that way. It is a
property of the spine or it is a lie.

If that bet is right, the winning platform is not the one with the cleverest
single agent. It is the one an enterprise security team can review, an auditor
can reconstruct, and an owner can stop mid-flight — and that platform accrues
distribution precisely because trust is the scarce good.

Stated in the repo at [vision.md:31-39](../01_vision/vision.md#L31-L39) and
[yc_overview.md:23-37](./yc_overview.md#L23-L37).

---

## 2. What the product does

A business owner connects their store and ad accounts, sets budget ceilings and
approval rules. A governed Creative + Growth team then produces creative,
proposes campaigns, and — with human approval on first-launch and
over-ceiling actions — launches and optimizes. Every action is explainable and
reversible.

The end state is delegation of *outcomes*, not tasks: "grow profitable revenue
within these limits," and a governed agent organization plans, produces,
decides within its authority, escalates what it must, and reports
([vision.md:26-29](../01_vision/vision.md#L26-L29)).

---

## 3. Architecture — the full specification

### 3.1 Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12, `strict` mypy | [pyproject.toml:9,229](../../pyproject.toml#L9) |
| Edge | FastAPI + uvicorn | [pyproject.toml:24-25](../../pyproject.toml#L24) |
| Orchestration | **LangGraph OSS, sole framework** | [pyproject.toml:44](../../pyproject.toml#L44); ADR-0002 |
| System of record | Postgres via raw asyncpg (SQLAlchemy for Alembic plumbing only) | [pyproject.toml:30-32](../../pyproject.toml#L30) |
| Event bus / cache | Redis + hiredis | [pyproject.toml:35](../../pyproject.toml#L35) |
| Vector store | Qdrant | [pyproject.toml:60](../../pyproject.toml#L60) |
| LLM provider | Anthropic Claude, behind a port | [pyproject.toml:64](../../pyproject.toml#L64) |
| Crypto | `cryptography` — ECDSA P-384 | [pyproject.toml:16](../../pyproject.toml#L16) |
| Policy engine | OPA / Rego (designated, not yet enabled) | ADR-0004 |
| Durable execution | Temporal (built, unwired) | [pyproject.toml:68](../../pyproject.toml#L68) |
| Console | Next.js 15 / React, TypeScript | [website/](../../website/) |

Deliberately boring and self-hostable. The rigor is layered on top, not bought
from a vendor. Only commodity APIs are consumed — Postgres wire, S3, OIDC,
OTel — so every core component can be run by the customer
([technical_due_diligence.md:61-66](./technical_due_diligence.md#L61-L66)).

### 3.2 The governance token — the root of trust

This is the primitive the whole system rests on. An agent's authority to act is
a **signed, scoped, budgeted, short-lived bearer credential** minted only by
the Governance Authority.

**Scheme.** ECDSA on curve P-384 (SECP384R1), fixed at
[`GOVERNANCE_CURVE = Curve.P384`](../../src/skylize/contracts/token.py#L38);
key loading rejects any other curve
([keys.py `_assert_p384`](../../src/skylize/app/governance/keys.py#L70-L75)).
Chosen over Ed25519 deliberately: FIPS 186-4 approved, broader HSM and
FIPS-validated-module support, and one curve family across the platform
(the same `ECCService` curve underpins ECDH/ECIES) — ADR-0001 §Decision.

**Token fields** ([contracts/base.py:123-155](../../src/skylize/contracts/base.py#L123-L155)):

```
token_id            UUID          unique; the revocation handle
agent_id            str
authority_level     executive | vp | director | manager | worker
department          str
delegation_chain    list[str]     root authority → this agent, ordered
scope               list[str]     concrete tool_ids; ⊆ contract.allowed_tools
max_token_budget    int           LLM-token ceiling
max_execution_time_seconds  int
issued_at           datetime
expires_at          datetime      short-lived (default 5 min)
nonce               str           anti-replay
signature           str           ECDSA P-384, base64url
```

The model is `frozen=True, extra="forbid"`. Nothing about an agent is implicit
— if it is not in the contract, the agent cannot do it
([contracts/base.py:12-13](../../src/skylize/contracts/base.py#L12-L13)).

**Canonical serialization.** The signed message is deterministic JSON: stable
key order, no whitespace, UTC ISO-8601 datetimes normalized across timezone
inputs — so the bytes a signer produces are exactly the bytes a verifier
reconstructs
([token.py:45-86](../../src/skylize/contracts/token.py#L45-L86)). The scheme is
isolated behind `TokenSigner` / `verify_token_signature`, so a future curve
migration is a swap at one seam.

**Validation pipeline — canonical order, first failure short-circuits**
([token.py:230-299](../../src/skylize/contracts/token.py#L230-L299)):

```
1. SIGNATURE   verify ECDSA P-384 against the authority public key
2. EXPIRY      now >= expires_at → deny
3. REVOCATION  injected LiveStateChecker: revoked token / suspended agent /
               kill switch (tenant, department, platform)
4. SCOPE       requested_tool ∈ token.scope AND token.scope ⊆ contract.allowed_tools
5. BUDGET      tokens_used_so_far + requested_cost > max_token_budget → deny
6. DELEGATION  chain non-empty AND terminates at token.agent_id
```

Stage 3 is the seam where a *stateless* cryptographic check meets *live* state.
That separation is what lets the crypto module stay driver-free (enforced by
import-linter) while the kill switch still bites in real time.

**Where it is enforced — three independent call sites:**

| Site | File | Purpose |
|---|---|---|
| ToolProxy (IF-TOOL) | [tools/proxy.py:1-16](../../src/skylize/tools/proxy.py#L1-L16) | every tool call an agent makes |
| LangGraph `governance_checkpoint` node | [creative_workflow.py:55-85](../../src/skylize/app/orchestrator/workflows/creative_workflow.py#L55-L85) | re-validates live state mid-workflow |
| Pre-egress on the single-shot path | [execution.py:313-341](../../src/skylize/app/agents/execution.py#L313-L341) | refuses a revoked token *before* the model is called |

That third one matters more than it looks. A revoked token or killed agent is
refused before any LLM spend occurs — not stamped onto the deliverable
afterward.

### 3.3 The Governance Authority

The single component that mints and revokes tokens, runs the circuit breakers,
and engages the kill switch
([authority.py](../../src/skylize/app/governance/authority.py)). It holds the
P-384 private key. It does **not** validate tool calls itself — validation is
decentralized, which is what keeps it off the hot path.

**Minting** ([authority.py:229-282](../../src/skylize/app/governance/authority.py#L229-L282)):
asserts the agent is active, signs, persists a `TokenRow`, emits
`GovernanceTokenIssued`, and records a `governance.token_issued` audit entry.
Three durable artifacts per mint — no silent authority.

**Restart safety** ([authority.py:182-194](../../src/skylize/app/governance/authority.py#L182-L194)):
`rehydrate()` warms the in-memory snapshot from Postgres — active kill scopes,
revoked token IDs, non-active agents — *before* the process serves a single
request. This closes the "restart forgets the kill switch" hole, which is
exactly the class of bug that turns a safety control into theater.

**Cross-instance propagation**
([authority.py:196-211](../../src/skylize/app/governance/authority.py#L196-L211)):
a revocation mutates the local snapshot immediately (hot path), then publishes
a `GovernanceInvalidation` over Redis so every other replica converges. Four
invalidation kinds: `REVOKE`, `AGENT_STATE`, `KILL_TENANT`, `KILL_PLATFORM`.

**Circuit breaker — scope violations.** Threshold 3
([authority.py:51](../../src/skylize/app/governance/authority.py#L51)). Three
scope violations by an agent within a tenant → automatic suspension, breaker
event, suspension event, audit record.

**Circuit breaker — convergence (runaway-loop detection).** An action is
hashed as SHA-256 over canonical JSON of `{agent_id, action_type,
action_args}`
([authority.py:62-76](../../src/skylize/app/governance/authority.py#L62-L76)).
A per-`(correlation_id, agent_id)` ring buffer trips the moment the same hash
recurs consecutively
([authority.py:93-106](../../src/skylize/app/governance/authority.py#L93-L106)).
Tripping is idempotent — the buffer resets and a suspended agent is not
re-tripped, so escalation is emitted exactly once
([authority.py:340-350](../../src/skylize/app/governance/authority.py#L340-L350)).

**Kill switch — four scopes**
([authority.py:462-533](../../src/skylize/app/governance/authority.py#L462-L533)):

| Scope | Effect |
|---|---|
| `agent` | that agent, that tenant |
| `department` | expands to every registered agent in the department |
| `tenant` | the whole org |
| `platform` | everything, everywhere |

Persisted to `kill_switch_state` (migration 0001:129-141), broadcast to all
replicas, mirrored as a governance event and an audit record. Exposed at
`POST /api/v1/kill-switch/engage`
([edge/routes/kill_switch.py](../../src/skylize/edge/routes/kill_switch.py)).
It overrides all authority, including executive agents.

### 3.4 The agent contract registry — 21 governed agents

An `AgentContract` is a frozen, `extra="forbid"` Pydantic model
([contracts/base.py:60-120](../../src/skylize/contracts/base.py#L60-L120))
declaring: authority level, department, fully-qualified input/output schema
paths, the tool manifest (`allowed_tools`), which of those tools the LLM may
invoke (`invocable_tools`, validated as a subset), `max_tool_iterations`, token
and wall-clock budgets, an ordered `escalation_path` ending at a human role,
a `failure_mode`, memory read/write namespaces, and `human_in_loop_triggers`.

Verified live at `37f3d2d` by loading `ALL_MVP_CONTRACTS`:

| agent_id | authority | department | tok budget | time | HITL triggers |
|---|---|---|---|---|---|
| ceo | executive | executive_office | 120,000 | 600s | spend_over_ceiling, brand_legal_sensitive, low_confidence_irreversible |
| cmo | executive | marketing | 100,000 | 540s | spend_over_ceiling, brand_legal_sensitive |
| cfo_agent | executive | finance | 40,000 | 300s | spend_over_ceiling, low_confidence_irreversible |
| vp_creative | vp | creative | 80,000 | 420s | first_external_launch, brand_legal_sensitive |
| copy_director | director | creative | 40,000 | 300s | brand_legal_sensitive |
| art_director | director | creative | 30,000 | 300s | brand_legal_sensitive |
| director_growth | director | growth | 30,000 | 240s | spend_over_ceiling, first_external_launch |
| creative_operations_manager | manager | creative | 10,000 | 120s | — |
| hook_generator_agent | worker | creative | 8,000 | 60s | first_external_launch |
| ad_copy_agent | worker | creative | 10,000 | 90s | — |
| caption_writer_agent | worker | creative | 6,000 | 60s | — |
| script_writer_agent | worker | creative | 12,000 | 120s | — |
| cta_optimizer_agent | worker | creative | 4,000 | 45s | — |
| brand_guardian_agent | worker | creative | 8,000 | 60s | brand_legal_sensitive |
| tone_of_voice_agent | worker | creative | 6,000 | 60s | — |
| seo_keyword_agent | worker | growth | 20,000 | 120s | — |
| sdr_outreach_agent | worker | sales | 15,000 | 120s | first_external_launch |
| lead_qualifier_agent | worker | sales | 8,000 | 60s | — |
| fraud_detection_agent | worker | security | 12,000 | 90s | security_severity_high, low_confidence_irreversible |
| agency_requirements_analyst | worker | agency_ops | 12,000 | 90s | — |
| agency_deliverable_drafter | worker | agency_ops | 20,000 | 180s | brand_legal_sensitive |

Five authority levels, ranked
([evaluator.py:42-48](../../src/skylize/app/decision_engine/evaluator.py#L42-L48)):
`worker` 1 → `manager` 2 → `director` 3 → `vp` 4 → `executive` 5.

Six human-in-the-loop trigger classes
([contracts/base.py:38-46](../../src/skylize/contracts/base.py#L38-L46)):
`spend_over_ceiling`, `first_external_launch`, `brand_legal_sensitive`,
`authority_exceeded`, `security_severity_high`, `low_confidence_irreversible`.

**Fail-closed resolution.** An unknown `agent_id` raises `AgentNotRegistered`
([registry.py:26-28,94-97](../../src/skylize/contracts/registry.py#L26-L28)).
**Tenant overrides may only tighten budgets, never loosen them** —
most-restrictive-wins, implemented as `min()`
([registry.py:114-128](../../src/skylize/contracts/registry.py#L114-L128)).
A CI gate asserts every contract's I/O schema dotted path resolves to an
importable Pydantic model
([registry.py:108-112](../../src/skylize/contracts/registry.py#L108-L112)).

### 3.5 The Decision Engine — deterministic, replayable

**No LLM calls. No I/O beyond one injected ledger read. Same inputs → identical
verdict, always** — which is what makes a decision replayable and auditable
([evaluator.py:14-16](../../src/skylize/app/decision_engine/evaluator.py#L14-L16)).

Pipeline, first terminal outcome short-circuits (most-restrictive-wins):

| # | Stage | Behavior |
|---|---|---|
| 0 | `safety_veto` | A security verdict with `reject=True` blocks unconditionally, ahead of everything. Absence is **not** a veto. Routes to a human — a reject may be a false positive ([evaluator.py:263-283](../../src/skylize/app/decision_engine/evaluator.py#L263-L283)) |
| 1 | `authority_check` | External launch requires director+; a worker proposing one is `authority_exceeded` → defer up the escalation path, not a flat reject ([:286-302](../../src/skylize/app/decision_engine/evaluator.py#L286-L302)) |
| 2 | `opa_policy` | Inline guardrails: unknown action class → reject (never guessed); spend must be positive and director+; `brand_safety=="blocked"` → reject ([:305-327](../../src/skylize/app/decision_engine/evaluator.py#L305-L327)) |
| 3 | `scoring` | Deterministic 0–100: authority weight (8×rank, 8–40) + policy pass (30) + budget headroom (0–30). Never terminal ([:330-360](../../src/skylize/app/decision_engine/evaluator.py#L330-L360)) |
| 4 | `capital_allocation` | Projected spend > ceiling → defer. **No ceiling configured → also defer** (fail closed) ([:363-388](../../src/skylize/app/decision_engine/evaluator.py#L363-L388)) |
| 5 | `conflict_resolution` | Rival proposals on the same `partition_key`: authority → recency → escalate to human if unresolvable ([:391-455](../../src/skylize/app/decision_engine/evaluator.py#L391-L455)) |
| 6 | `hitl_gate` | Contract triggers matched against proposal metadata ([:458-485](../../src/skylize/app/decision_engine/evaluator.py#L458-L485)) |

**The synchronous execution vertical.** `agent.execute` proposals are decided
terminally at stage 2.5 and never ride the generic default-approve
([evaluator.py:132-140,186-260](../../src/skylize/app/decision_engine/evaluator.py#L132-L140)).
The rule reads the contract's `human_in_loop_triggers` field — not a
name-string inference over the agent id:

- `FIRST_EXTERNAL_LAUNCH` present → **defer**
- no triggers → **approve**
- any other trigger → **defer** (fail-closed, routed to the HITL queue with the
  triggering reasons recorded, rather than dead-ending as a reject)

**Measured outcome distribution across the 21 agents** (governed org, valid
input): **9 approve / 12 defer / 0 reject**
([REPO_STATE.md:143-146](../REPO_STATE.md#L143-L146)). `reject` is not a static
per-agent outcome — it is reachable only for a genuinely invalid proposal.

Conflict resolution deserves note: it is fully deterministic and it has a
defense-in-depth duplicate of the safety veto inside `_resolve`
([evaluator.py:435-443](../../src/skylize/app/decision_engine/evaluator.py#L435-L443)),
so a safety-rejected proposal can never win a conflict even if stage ordering
is later changed. That is the design temperament throughout.

### 3.6 The two engines — a deliberate, enforced separation

| | `app/decision_engine` (inline) | `decision_engine` (OPA) |
|---|---|---|
| Role | dev stand-in + production fallback | **designated production arbiter** |
| Wired? | yes — [bootstrap.py:294](../../src/skylize/bootstrap.py#L294) | no — own worker only |
| Live path may import? | yes | **no** (owner decision K3) |
| Status | serving | gated off |

`SKYLIZE_DECISION_ENGINE` selects per environment;
`bootstrap.py:276-280` **raises `RuntimeError` on any value but `"inline"`** —
misconfiguration fails closed at startup, and exactly one engine emits terminal
`decision.*` events per environment (ADR-0004).

The OPA package is real, not vapor: consumer, six-stage pipeline
([decision_engine/pipeline.py:1-21](../../src/skylize/decision_engine/pipeline.py#L1-L21)),
OPA client, publisher, transactional outbox poller, and a HITL resume handler.
Its `decision_id` is derived `uuid5` from the originating `event_id`, so a
redelivered proposal reconstructs the same ticket — determinism as an
idempotency mechanism.

**Honest status:** the 7 Rego files are fail-closed placeholders — 128 lines
total, `default allow := false`, and **no rule anywhere sets `allow := true`**
(verified at `37f3d2d`; the six grep hits are comments *stating* that fact).
The aggregate entrypoint `data.skylize.decision` returns a real, non-empty
`{allow: false, deny_reasons: [...]}`. Flipping the flag requires real Rego, a
live OPA server, and wire-parity certification.

### 3.7 The three ledgers — never conflated (ADR-0006)

This distinction is the kind of thing that separates a financial system from a
prototype, so it is architecturally enforced:

| Ledger | Unit | Store | Lifetime |
|---|---|---|---|
| `run_ledger` | LLM **tokens** | RAM or Redis | discarded when the run ends |
| `budget_ledger` | currency **minor units** (cents) | Postgres (migration 0001:146-160) | business spend vs ceiling |
| `ai_cost_ledger` | currency **micro units** (`cost_micros`) | Postgres (migration 0012) | money value of consumed tokens |

**Money discipline** ([dal/cost_ledger.py:9-22](../../src/skylize/dal/cost_ledger.py#L9-L22)):

- All arithmetic in `Decimal` with `ROUND_HALF_UP`. **Never float.**
- Unit prices are per 1e6 tokens (`*_per_mtok`) so every real quoted price is an
  exact integer and `tokens × price` needs no fractional price
  ([cost_ledger.py:56-77](../../src/skylize/dal/cost_ledger.py#L56-L77)).
- Cost stored in micros — 100× finer than a cent — so per-row rounding cannot
  perturb a cent-level total.
- **Cents are derived once, at aggregation, never stored per row**, so small-call
  residue never drifts ([cost_ledger.py:85-93](../../src/skylize/dal/cost_ledger.py#L85-L93)).
- Append-only, DB-enforced. Corrections are reversing rows, never `UPDATE`.
- Idempotent writes: `ON CONFLICT (org_id, idempotency_key) DO NOTHING` keyed on
  the provider's response id, so a retried write collapses to one row
  ([cost_ledger.py:245](../../src/skylize/dal/cost_ledger.py#L245)).

Pricing lives in a seeded `model_pricing` table (migrations 0013, 0018), not in
config. Config floats survive only as a WARNING-logged fallback when no cost
ledger is wired.

### 3.8 Multi-tenancy — isolation at the data layer

Isolation does not depend on application correctness. It holds in Postgres.

**Row-Level Security with `FORCE`** across 11 tables — `governance_tokens`,
`agent_live_state`, `kill_switch_state`, `budget_ledger`, `decisions`,
`hitl_queue`, `memory_records`, `kg_nodes`, `kg_edges`, `audit_log`,
`tenant_integrations`
([migration 0001:347-367](../../migrations/versions/0001_initial_schema.py#L347-L367)):

```sql
CREATE POLICY tenant_isolation ON %I FOR ALL
  USING      (org_id = current_setting('skylize.org_id', true))
  WITH CHECK (org_id = current_setting('skylize.org_id', true));
```

`FORCE ROW LEVEL SECURITY` means the policy applies **even to the table owner**.

**The correction that makes it real.** `FORCE` does not stop a SUPERUSER or
`BYPASSRLS` role. Migration 0003 therefore creates a non-superuser,
non-table-owner `skylize_app` role, and the runtime connects as that role
([0001 header:12-16](../../migrations/versions/0001_initial_schema.py#L12-L16)).
The test harness enforces the same
([tests/integration/conftest.py:26](../../tests/integration/conftest.py#L26)) —
an RLS test run as a superuser proves nothing, and the repo says so explicitly.

**One connection module.** `dal/connection.py` is the only file that opens an
asyncpg connection. Three session types:

- `tenant_session(org_id)` — `SET LOCAL skylize.org_id` inside a transaction, so
  the binding is discarded on commit and never leaks across pooled reuse
  ([connection.py:70-80](../../src/skylize/dal/connection.py#L70-L80))
- `admin_session()` — platform tables only; RLS tables return nothing by design
- `rehydration_session()` — a read-only, startup-only carve-out (migration 0002)
  so the Governance Authority can warm its kill/revocation snapshot across all
  tenants. Writes remain impossible — the policy's `WITH CHECK` still demands a
  matching `org_id` ([connection.py:89-102](../../src/skylize/dal/connection.py#L89-L102))

`org_id` always comes from the signed `RequestContext`, never from a query or
body field ([REPO_STATE.md:65](../REPO_STATE.md#L65)).

### 3.9 Audit — immutable by database trigger

```sql
CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION skylize_prevent_mutation()
```

([migration 0001:372-385](../../migrations/versions/0001_initial_schema.py#L372-L385)).
Not a convention. Not an ORM guard. A trigger that raises.

The `audit_log` row carries `event_id` (UNIQUE), `org_id`, `correlation_id`,
`causation_id`, `source_agent_id`, `authority_level`, `governance_token_id`,
`action_type`, `inputs_hash`, `outputs_hash`, `result`, `result_reason`,
`occurred_at`, `recorded_at` — so any action is attributable to the specific
signed token that authorized it.

### 3.10 Events — a closed taxonomy

`BaseEvent` is frozen, `extra="forbid"`, and the category enum is **closed** at
six: `creative`, `sales`, `memory`, `decision`, `governance`, `audit`
([schemas/base.py:27-36](../../src/skylize/schemas/base.py#L27-L36)).

Envelope invariants ([schemas/base.py:38-70](../../src/skylize/schemas/base.py#L38-L70)):
`event_id`, `schema_version` (regex-pinned `MAJOR.MINOR`), `category`, `type`,
`tenant_id`, `partition_key` (ordering key), `department`, `source_agent_id`,
`authority_level`, `governance_token_id`, `causation_id`, `correlation_id`,
`occurred_at`, `redelivery_count` (stamped by the bus, never the publisher).

One workflow is one `correlation_id`. `causation_id` chains cause to effect. A
defer → approve → execute sequence shares one chain
([execution.py:410-422](../../src/skylize/app/agents/execution.py#L410-L422)).
That is what makes replay a reconstruction rather than a guess.

`RequestContext` is the short-lived signed internal identity derived at the edge
from a verified OIDC JWT; internal services trust it, never the raw IdP token;
TTL ≤ 5 minutes ([schemas/base.py:73-87](../../src/skylize/schemas/base.py#L73-L87)).

### 3.11 The live request path

FastAPI mounts 13 routers plus `/health`, ~42 endpoints total
([edge/gateway.py:79-91](../../src/skylize/edge/gateway.py#L79-L91)).

The two paths that matter:

**`POST /api/v1/agents/execute`** — the synchronous governed path
([app/agents/execution.py](../../src/skylize/app/agents/execution.py)):

```
1. Resolve contract (fail closed on unknown agent_id)
2. Validate input against contract.input_schema
2.5 SYNCHRONOUS DECISION GATE (governed orgs only) — runs BEFORE prompt
    building, before the mint, before any LLM spend. A reject/defer means
    no LLM call, no deliverable, no ledger row.
3. Build system + user prompt from contract metadata
3a. Mint a signed GovernanceToken; re-validate pre-egress
4. LLM call — single-shot, or the multi-turn tool loop bounded by
   contract.max_tool_iterations (exceeding it is a governance escalation,
   audited, not a silent truncation)
5. Parse + validate output against contract.output_schema
6. Format markdown  7. Persist deliverable  8. Audit
```

Two details worth flagging as design signal:

- **Deterministic values are never trusted to the model.** `cfo_agent`'s budget
  totals and flags are recomputed in Python after the LLM returns
  ([execution.py:383-387](../../src/skylize/app/agents/execution.py#L383-L387)).
  Input-provided correlation fields like `brief_id` are echoed from validated
  input rather than invented ([execution.py:369-376](../../src/skylize/app/agents/execution.py#L369-L376)).
- **Durable-row-before-event ordering.** On a defer, the `hitl_queue` row is
  written *before* the terminal event and audit record — because the previous
  ordering let a subscriber race an empty table, and let an enqueue failure
  500 the request after a terminal "deferred, hitl_id=X" event had already been
  published for a row that would never exist
  ([execution.py:453-462](../../src/skylize/app/agents/execution.py#L453-L462)).
  An emission failure after a durable write is logged at ERROR naming the row
  and **re-raised, never swallowed**.

**`POST /api/v1/workflows/creative`** — the LangGraph path. Explicit,
inspectable nodes: `governance_checkpoint` → `agent_step` → `emit`, with a
failure branch, checkpointed via `MemorySaver` so the graph can pause and
resume ([creative_workflow.py:48-115](../../src/skylize/app/orchestrator/workflows/creative_workflow.py#L48-L115)).
Control flow is deterministic; **only the agent step reasons**. The governance
checkpoint re-validates live state mid-flight and feeds the *real* projected
token cost into the BUDGET stage rather than a hardcoded zero.

### 3.12 Human-in-the-loop

`hitl_queue` (migration 0001:202-226): status ∈ `pending | approved | rejected |
modified | expired`, with `decision_id` FK, `correlation_id`, `trigger_reason`,
`proposal_json`, `score_json`, `verdict_by/json/at`, `expires_at` (48h).

Approval **replays through `AgentExecutionService`** carrying a
`HitlApprovalContext` — which deliberately skips the evaluator, because the gate
already ran and deferred; the human approval *is* the gate's resolution, and
re-evaluating would defer forever
([execution.py:255-262](../../src/skylize/app/agents/execution.py#L255-L262)).
The stored payload is re-validated against the agent's *current* input schema
before the gate, the mint, and any spend, so schema drift surfaces as an error
and nothing executes ([execution.py:241-249](../../src/skylize/app/agents/execution.py#L241-L249)).

The claim is exactly-once: the queue repository uses a claiming `UPDATE`
([REPO_STATE.md:75](../REPO_STATE.md#L75)).

### 3.13 Context compression (the Model Context Engine)

A two-tier compression proxy between agent context assembly and LLM egress
([memory/compression/pipeline.py](../../src/skylize/memory/compression/pipeline.py)):
**L1 deterministic prune** → policy decision → **L2 semantic route** (only if
worth it), measured with `tiktoken` `cl100k_base` as ground-truth tokenizer.

**Totality is the contract**: `compress` always returns a `CompressionResult`
and never raises for a recoverable failure. A failed L2 degrades to L1-only text
and records a `compression.l2_degraded` audit action with the correlation_id
threaded through
([pipeline.py:10-19](../../src/skylize/memory/compression/pipeline.py#L10-L19)).

### 3.14 Enforced architectural boundaries

Not documented conventions — **CI gates that fail the build**
([pyproject.toml:123-221](../../pyproject.toml#L123-L221)). Five import-linter
contracts, currently **5 kept / 0 broken**:

1. **Agents may only import schemas** — agents cannot reach adapters, dal, app,
   runtime, events, or memory. Agents are untrusted: no egress, no credentials,
   no DB driver.
2. **Schemas are leaf packages** — they import nothing from skylize.
3. **Pure inner layers hold no database driver** — `agents`, `schemas`,
   `contracts`, `runtime`, `memory` may not import `asyncpg`.
4. **Application logic contains no SQL** — `skylize.app` depends on dal *ports*,
   never `dal.repositories`, `dal.connection`, or `asyncpg`. One documented
   exemption, with a stated removal trigger, for the paused Temporal worker
   (a process composition root that happens to live inside `app`).
5. **No direct LangChain/CrewAI imports** — LangGraph's transitive
   `langchain_core` is accepted (LangGraph structurally requires it); *our* code
   importing the ecosystem directly is banned. Backed by an AST scan
   (`scripts/check_forbidden_imports.py`) because import-linter cannot
   prefix-match.

Plus: `check_all_modules_importable.py` (206 modules import cleanly),
`find_orphan_modules.py` (a ratchet — no *new* unreachable modules; 13
allowlisted, down from 14 after a dead adapter was deleted rather than
resurrected).

### 3.15 Fail-closed configuration

Five environment variables kill the process at startup rather than degrade
silently ([REPO_STATE.md:154-159](../REPO_STATE.md#L154-L159)):

| Var | Failure |
|---|---|
| `SKYLIZE_ANTHROPIC_API_KEY` | absent + not demo mode → `LLMConfigurationError` |
| `SKYLIZE_JWT_SECRET` | absent + not dev auth → `ValueError` |
| `SKYLIZE_CORS_ORIGINS` | contains `"*"` → `ValueError` (the gateway sets credentials) |
| `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` | empty on a non-memory backend → fails closed |
| `SKYLIZE_DECISION_ENGINE` | any value but `"inline"` → `RuntimeError` |

---

## 4. Evidence — what is measurably true

Measured at `37f3d2d` / `4c6f4511` (the intervening commits are documentation
only):

| Metric | Value | Source |
|---|---|---|
| Source | **27,942 LOC**, 206 Python modules | measured at `37f3d2d` |
| Tests | **31,083 LOC**, 158 test files | measured at `37f3d2d` |
| Suite | **1,408 passed / 2 skipped / 0 failed** (services up) | [REPO_STATE.md:32](../REPO_STATE.md#L32) |
| Order-independence | identical tally in reversed collection order | [REPO_STATE.md:32](../REPO_STATE.md#L32) |
| mypy | `strict`, 0 issues across 206 files | [REPO_STATE.md:39](../REPO_STATE.md#L39) |
| import-linter | 262 files, 1,195 dependencies, **5 kept / 0 broken** | [REPO_STATE.md:40](../REPO_STATE.md#L40) |
| Static gates | **7 of 7 pass, exit 0** | [REPO_STATE.md:38](../REPO_STATE.md#L38) |
| Migrations | 18, unbroken chain `base → 0001 → … → 0018`, one file per revision, head confirmed against a live DB | [REPO_STATE.md:46](../REPO_STATE.md#L46) |
| Governed agents | 21 | verified live at `37f3d2d` |
| API surface | 13 routers, ~42 endpoints | [gateway.py:79-91](../../src/skylize/edge/gateway.py#L79-L91) |

**Tests run against real services, not mocks.** The CI `integration` job stands
up Postgres 16 and Redis 7, applies migrations as the admin role, and runs the
suite as the non-superuser `skylize_app` role
([.github/workflows/ci.yml:66-106](../../.github/workflows/ci.yml#L66-L106)).
The repo's own rule: *a money-path or tenancy claim is only believed if the
Postgres-backed tests **ran**, not skipped* — because they skip silently
without their env vars.

Test ratio is **1.11:1 tests to source**. The property test on the memory
identity map is a Hypothesis test, and the repo notes that a green tick in one
worktree can mean the counterexample was never replayed — so the injectivity is
argued from the code, not the tick
([REPO_STATE.md:228](../REPO_STATE.md#L228)).

---

## 5. What is NOT built — stated plainly

A governance product that oversells its own status would be self-refuting. The
repo maintains a read-only audit mirror ([REPO_STATE.md](../REPO_STATE.md))
whose entire purpose is to keep this list honest.

**Seven subsystems exist in code but are unreachable from a live request:**

1. **OPA decision engine** — worker-only; the API process raises on any
   non-`"inline"` value. Needs real Rego + live OPA + certification.
2. **Temporal worker + LLMJudge** — no entrypoint schedules it.
3. **MemoryGateway** — constructed only in tests. **There is no agent-memory
   persistence on any path.** The one table built for it, `memory_records`, has
   no reader and no writer in `src/`.
4. **runtime alt-stack** (`LLMAgentRunner`, `runtime/tool_proxy.py`) — dead.
5. **mem0 adapter** — not wired.
6. **obsidian_writer** — dead.
7. **n8n admin BFF** — gated default-OFF, returns HTTP 501 unless explicitly
   enabled; a governed rewrite is a hard precondition (ADR-0003).

**Three open defects**, each with a stated consequence
([REPO_STATE.md:100-108](../REPO_STATE.md#L100-L108)):

- `APIConnectionError` collapses retry-safe failures (connection refused, never
  sent) with ambiguous ones (mid-flight reset, may be billed). Provably-safe
  retries are refused as terminal.
- No sweep moves time-expired `hitl_queue` rows to `expired`. They linger in the
  pending list until someone attempts a verdict and gets a 410.
- The adapter-level `_check_budget` is dormant on every live path — no
  production call site populates `max_token_budget` on the request. The live
  budget control is the separate `validate_tool_call` BUDGET stage, which does
  bite; this defense-in-depth layer does not.

**Also true:** cross-tenant learning is off at MVP by design; a graph DB is
deliberately deferred to Postgres relations; SOC2 attestation is a Phase-4
milestone, not a current claim; and traction metrics are unpopulated pending
design partners ([yc_overview.md:70-76](./yc_overview.md#L70-L76)).

One deletion is worth describing, because it is the thesis applied to our own
code. A dead `PgMemoryAdapter` implied durable agent-memory persistence the
system did not have. The choice was to write the missing DDL — making an
unreachable capability look real in the schema — or delete the module. **It was
deleted.** The exposure was the *claim*, not the dead code
([REPO_STATE.md:102](../REPO_STATE.md#L102)).

---

## 6. Why this is defensible

| Moat | Argument |
|---|---|
| **Governance cannot be retrofitted** | Signed tokens, RLS-enforced tenancy, DB-immutable audit, deterministic decisions, closed event taxonomy, CI-enforced import boundaries — each is a property of the spine. A competitor who ships an agent demo first still has to rewrite from the data layer up. |
| **Trust is distribution** | Enterprises and owners adopt what they can audit and stop. The system is built for that review, not defended against it. |
| **No lock-in** | Every dependency sits behind a port; only commodity APIs are bought; every core component self-hostable; the LLM provider is never named in business logic. This pre-answers the largest enterprise objection. |
| **Organizational memory compounds per tenant** | The system gets measurably better at *your* business while staying tenant-isolated. (Phase 3 — see §5; not built today.) |
| **Margin control is architectural** | Per-agent token ceilings, per-org spend ceilings, micro-precision cost attribution per `governance_token_id`, and provider abstraction for cost routing. |

---

## 7. Roadmap

1. **Today (MVP)** — a governed creative + growth team that produces, proposes,
   and with human approval launches, fully audited.
2. **Near** — more departments (sales, support, finance ops, procurement) as
   governed crews on the same spine.
3. **Mid** — organizational memory and the learning pipeline compound.
4. **Long** — the business operating system layer: owners run their company
   through Skylize the way they once ran it through a suite of SaaS tools plus a
   team, but as one governed, accountable organization.

([vision.md:54-64](../01_vision/vision.md#L54-L64))

**Nearest technical milestones**, in dependency order: owner approval of
`policy_inputs.md` → author real Rego → stand up live OPA → wire-parity
certification → flip `SKYLIZE_DECISION_ENGINE` to `"opa"`. Three of the five
blockers ADR-0005 listed have already landed (department vocabulary,
caller-minted single `hitl_id`, OPA HITL resume path); only "live OPA + real
Rego" remains.

---

## 8. What the fellowship funds

*(Populate per the application. Funds accelerate department breadth (Phase 2),
organizational-memory depth (Phase 3), and enterprise/SOC2 readiness (Phase 4),
each on the existing spine — [yc_overview.md:78-82](./yc_overview.md#L78-L82).)*

**Sections requiring the applicant's own input — not inferable from the
codebase, deliberately left blank rather than fabricated:** founder background
and age eligibility, current education status, team composition, funding
history, design partners and traction metrics, and the specific ask.

---

## 9. The closing argument

The strongest technical evidence in this repository is not any single
subsystem. It is the discipline visible in how the project treats its own
claims: a `REPO_STATE.md` that catalogs 14 stale documentation claims against
contradicting code; a testing rule that refuses to believe a money-path result
unless the Postgres-backed tests are confirmed to have *run* rather than
skipped; a dead module deleted rather than propped up with DDL because the
exposure was the claim; and CI gates that make architectural boundaries fail the
build instead of appearing in a style guide.

A company selling governed autonomy has to govern itself first. That is the
argument.
