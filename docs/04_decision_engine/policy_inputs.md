# Skylize — Policy Inputs (Business Rules for Rego Authoring)

> **Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)**
> **Date compiled:** 2026-07-17
>
> This file is the **sole source of business rules** for OPA/Rego authoring (Faz 2).
> Every Rego rule MUST cite a line in this file. No Rego rule may exist without a
> corresponding approved entry here. This institutionalizes the M5 lesson: This institutionalizes the lesson from the unsourced-label incident (introduced in commits 01a91644 / 9861cf3c, remediated on fix/unsourced-m5-references): no value, threshold, or rule enters code without a traceable, owner-approved source.
>
> **How to read this file:** Every concrete number, category, and matrix row below
> is marked with one of:
> - `[RESEARCH-SUGGESTED]` — a defensible default synthesized from the 0.x research
>   artifacts. **NOT yet approved.** Owner must confirm, adjust, or reject.
> - `[CODE-VERIFIED]` — extracted directly from the current codebase (schema, contracts,
>   migrations). This is ground truth, not a proposal.
> - `[OWNER-DECISION-REQUIRED]` — a design choice only the owner can make; no default
>   is safe to assume.
>
> Nothing here is `[APPROVED]` until the owner changes the banner at the top of each
> section. Faz 2 (Rego) is BLOCKED until each section reaches `[APPROVED]`.

---

## Global combining principle (applies to all classes)

`[RESEARCH-SUGGESTED]` Default-deny, fail-closed. The final decision is:

```
allow if {
    rbac_allow          # 0.1 authority
    budget_ok           # 0.2 spend
    external_ok         # 0.3 external_action
    brand_legal_ok      # 0.4 brand_legal
    data_access_ok      # 0.6 data_access
    not security_veto   # 0.5 security_veto — deny-overrides, cannot be overridden
}
default allow := false
```

Evaluation order matters for the *reason* returned, but `security_veto` is
**absolute**: if it fires, no other class can produce an allow. Missing input,
timeout, or evaluator error on ANY class → deny.

---

## 0.1 — Authority (Delegation-of-Authority / HITL matrix)

> **Section status: `[OWNER-DECISION-REQUIRED]` — the matrix rows are research-suggested; the dollar thresholds and level assignments need your sign-off.**

### Authority levels (ordered)
`[RESEARCH-SUGGESTED]` Five levels, mapping to Skylize's agent hierarchy:

```
L1 = Worker agent      (fully autonomous, logged)
L2 = Manager agent     approval
L3 = Director agent    approval
L4 = Executive agent   approval
L5 = Human Principal   (mandatory HITL — human approval)
```

### Risk bands (the core axis — dollar amount is only ONE dimension)
`[RESEARCH-SUGGESTED]` The primary axis is **reversible vs. irreversible** and
**read vs. write**, not dollar amount:
- **Low** = read-only or easily reversible/internal
- **Medium** = reversible write or limited external effect
- **High** = irreversible external action, or budget/brand impact
- **Critical** = irreversible money movement, legal commitment, or high blast radius

### Cross-cutting rule — Separation of Duties (SoD)
`[RESEARCH-SUGGESTED]` Applies to ALL domains (SOX 404 / COSO):
> The agent that *initiates* a transaction may not be the agent that *approves* it.
> Rego: `deny if { input.action=="approve"; input.resource.initiated_by == input.agent.name }`

### SDR / Sales matrix
`[RESEARCH-SUGGESTED]` — every row needs owner confirmation:

| Action | Risk | Min Level | Rego trigger signal |
|---|---|---|---|
| Templated outbound email (opted-in list) | Low | L1 | `tool=="sendEmail" && template in approved && list=="opted_in"` |
| Meeting booking / calendar | Low | L1 | `tool=="bookMeeting"` |
| CRM update (standard fields, audited) | Low-Med | L2 | `tool=="updateCRM" && field in standard_fields` |
| Personalized/free-text outbound (new content) | Medium | L2 | `tool=="sendEmail" && template==null` |
| Discount offer 0–15% | Medium | L2 | `discount <= 0.15` |
| Discount 16–30% or Net 60 | High | L3 | `discount > 0.15 \|\| terms=="net60"` |
| Send proposal (standard template) | Med-High | L3 | `tool=="sendProposal"` |
| Send contract / e-signature | Critical | L5 | `tool=="sendContract" \|\| esignature==true` |
| Discount >30% or non-standard legal term | Critical | L5 | `discount > 0.30 \|\| nonStandardLegal==true` |

### Marketing matrix
`[RESEARCH-SUGGESTED]`:

| Action | Risk | Min Level | Rego trigger signal |
|---|---|---|---|
| Internal content/report draft, analysis | Low | L1 | `tool in {"draftContent","analyze"}` |
| Schedule social post (approved content) | Low-Med | L2 | `tool=="schedulePost" && content_approved==true` |
| Paid ad spend, under daily cap | Medium | L2 | `tool=="adSpend" && dailySpend+amount <= cap` |
| Email campaign send (existing list) | Medium | L3 | `tool=="sendCampaign"` |
| Paid ad spend, over cap / new campaign | High | L3 | `dailySpend+amount > cap` |
| Brand/messaging change | High | L4 | `tool=="changeBrandMessaging"` |
| New market expansion, large budget realloc | Critical | L5 | `tool=="budgetReallocation" && amount > exec_limit` |

### Finance matrix
`[RESEARCH-SUGGESTED]`:

| Action | Risk | Min Level | Rego trigger signal |
|---|---|---|---|
| Financial report generation (read-only) | Low | L1 | `tool=="generateReport" && mode=="read"` |
| PO-matched, clean three-way-match invoice → approval route | Low-Med | L2 | `threeWayMatch==true && poBacked==true` |
| Invoice approval (within tolerance, under threshold) | Medium | L2 | `tool=="approveInvoice" && amount < mgr_limit` |
| Refund (small, under threshold, no fraud flag) | Medium | L2 | `tool=="issueRefund" && amount < refund_threshold && !fraudFlag` |
| Vendor record create/modify | High | L3 | `tool=="modifyVendor"` |
| Payment processing (over threshold) | High | L4 | `tool=="processPayment" && amount >= exec_threshold` |
| Budget reallocation (large) | Critical | L5 | `tool=="budgetReallocation" && amount > critical_limit` |
| Vendor bank-detail change | Critical | L5 | `tool=="changeVendorBank"` |

> **`[OWNER-DECISION-REQUIRED]`** — Fill in the actual dollar values for:
> `mgr_limit`, `refund_threshold`, `exec_threshold`, `exec_limit`, `critical_limit`,
> `cap` (daily ad cap). These live in `data.json` (tunable without touching Rego).
> See 0.2 for suggested defaults to align against.

---

## 0.2 — Spend (budget ceilings)

> **Section status: `[OWNER-DECISION-REQUIRED]` — the tier table is a defensible default, but every dollar figure is yours to set. No authoritative industry survey fixes these; they are synthesized from Ramp/Brex/Spendesk + delegation norms + documented AI-agent incidents.**

### Architecture (two-layer, not a single gate)
`[RESEARCH-SUGGESTED]` Hard-deny rules evaluated FIRST (regardless of approval),
then soft escalation, then auto-approve. Governs by the **stricter of** absolute
dollar amount OR percentage variance from approved budget line.

### Suggested starter tier table (USD, per single spend action)
`[RESEARCH-SUGGESTED]`:

| Tier | Dollar range | % variance | Enforcement | Approver |
|---|---|---|---|---|
| T0 — Agent-autonomous | < $500 | ≤ 10% | Auto-approve, log only | None (agent) |
| T1 — Manager | $500–$5,000 | 10–25% | Soft escalation, 24–48h timer | Manager |
| T2 — Director/Finance | $5,000–$25,000 | 25–50% | Soft escalation + justification | Director / finance BP |
| T3 — Owner/CFO | $25,000–ceiling | > 50% | Soft escalation, dual sign-off | Owner / CFO (+1) |
| T4 — Hard block | > per-action ceiling (default **$50,000**), OR monthly cap exceeded, OR velocity/loop breaker tripped, OR MCC not allowlisted | any | **Auto-reject before execution — no human wait** | None (requires recorded policy override) |

### Agent-operational caps (always-on, independent of the above)
`[RESEARCH-SUGGESTED]`:
- Per-run/session cost cap: default **$50 soft alert / $100 hard cutoff** per day per agent
- Monthly per-agent ceiling: default **$1,000** (owner approval to extend)
- Velocity cap: N transactions/hour + loop-similarity circuit breaker
- Scoped single-use payment credentials; MCC/merchant allowlist (ad platforms only)

### Over-ceiling behavior — the single most important spend decision
> **`[OWNER-DECISION-REQUIRED]`** Per class, choose: `defer_to_human` vs `hard_deny`.
> Research recommendation: **hard_deny** above T4 ceiling (no human wait — the $6,531
> AWS and $47,000 loop incidents were runaway loops, not single bad decisions);
> `defer_to_human` for the T1–T3 ambiguous middle band only.

### Currency
> **`[OWNER-DECISION-REQUIRED]`** USD-only for MVP, or multi-currency with
> normalization? Research assumes USD-only for MVP simplicity.

---

## 0.3 — External Action ("first-time" detection + HITL triggers)

> **Section status: `[OWNER-DECISION-REQUIRED]` — the 3-tier model is well-grounded in primary sources (OpenAI, Anthropic, Microsoft, SAIF); confirm the tier assignments fit your product.**

### "First-time" detection — deterministic, NOT model judgment
`[RESEARCH-SUGGESTED]` A first-seen record keyed on `(tenant_id, channel, resource_id, action_type)`
in a durable state store. No record → first-time → HITL. On approval + successful
execution, write the record. Must be enforced in the orchestrator, **never decided
by the model** (adversarial prompts can bypass a model-decided gate — Microsoft).

### 3-tier classification
`[RESEARCH-SUGGESTED]`:

**TIER A — ALWAYS HITL (regardless of history):**
- Opening a new ad account or new channel/platform integration
- Budget/bid change above absolute or % threshold (e.g. > +30% for a campaign in learning), or any action pushing projected spend above tenant ceiling
- First connection of a payment method / billing change
- Publishing creative in restricted verticals (health, finance, crypto, housing, employment) — links to 0.4
- Actions affecting legal exposure (contracts, sensitive lead-form fields)
- Bulk/irreversible operations (delete audience, mass-archive)
- **Immutable-config choices** (e.g. TikTok budget type — cannot change post-launch) — Tier-A even on repeat
- **Mid-month budget change on Google Ads** (resets the monthly cap calculation → runaway spend risk)

**TIER B — FIRST-TIME HITL (first per `(tenant, channel, resource, action_type)`, autonomous after):**
- First campaign launch on a channel / in a new ad account (within Tier-A bounds)
- First use of a new objective, bid strategy, or budget type
- First targeting/audience config for a channel
- First content publish of a given type on a channel
- First automated budget adjustment within safe bounds

**TIER C — NEVER HITL (autonomous, logged):**
- Read-only analytics, reporting, insights
- **Pausing** a campaign (reversible, risk-reducing — encourage autonomously as kill-switch)
- Minor budget *decreases* within a small band
- Resuming a previously-approved campaign at/below approved budget
- Internal draft generation (staged, not published)

### Hard platform-independent spend caps (critical)
`[RESEARCH-SUGGESTED]` Because Google (2× daily / 30.4× monthly) and Meta (1.75×
daily / 7× weekly) **by design overspend** daily budgets, set Skylize-side account
caps independent of platform budgets. New accounts below an age/spend-history
threshold → force Tier-A on all launches + gradual ramp (+20–30%/week) to avoid
triggering platform fraud systems.

### Graduated autonomy
> **`[OWNER-DECISION-REQUIRED]`** Number of clean reviewed executions (N) before a
> Tier-B combination graduates to autonomous. Research suggests N = 1–3. Auto-demote
> back to Tier-A on any policy strike or overspend event.

---

## 0.4 — Brand / Legal (sensitive content classification)

> **Section status: `[OWNER-DECISION-REQUIRED]` — the 36-category taxonomy is comprehensive and primary-source-backed. Confirm the REJECT/ASK-HUMAN split matches your risk appetite. NOTE: this is a risk-management default, not legal advice.**

### Routing model
`[RESEARCH-SUGGESTED]` Claim-type-based routing layered over vertical severity tiers.
Two actions: **REJECT** (auto-block; illegal/unfixable/high civil-criminal risk) and
**ASK-HUMAN** (route to HITL; lawful if substantiated/disclosed/qualified). REJECT
can be overridden only via a documented verified-client-exception workflow.

### 36-category taxonomy (default action)
`[RESEARCH-SUGGESTED]` — abbreviated; full reasoning in the 0.4 research artifact:

**Default REJECT (14):** health cure/disease claims; guaranteed financial returns/income; weight-loss "gut-check" claims; AI-deepfake of a real identifiable person without consent; AI-fabricated testimonials/reviews (FTC Fake Reviews Rule, $53,088/violation); protected-class targeting in housing/employment/credit; personal-attributes copy (Meta's most-cited rejection); political/electoral (default; ask-human only for authorized clients); tobacco/vaping; gambling (default; ask-human only for verified-licensed); adult content; children-directed/minor-targeting; prescription drugs/pharma (default); crypto/NFT/DeFi (default; ask-human only for certified); weapons; counterfeit goods; crisis exploitation; hate speech; sensitive personal-data references (GDPR special-category).

**Default ASK-HUMAN (route to HITL, lawful if qualified):** general health/wellness claims; balanced investment/financial-product marketing; before/after imagery; absolute/superlative claims; comparative claims naming competitors; AI-generated fictional spokesperson (with disclosure); genuine testimonials; pricing/discount/"was-now"/free-trial; urgency/scarcity + dark-pattern devices; negative-option/auto-renewal copy; green/environmental claims; "Made in USA"/origin claims; alcohol; dietary supplements; unverifiable statistics (AI hallucination risk); IP/trademark use; undisclosed realistic AI-generated media (fixable by labeling).

### Three stricter verticals (mandatory human compliance sign-off)
`[RESEARCH-SUGGESTED]`:
- **Healthcare/Medical/Pharma — STRICTEST:** default REJECT for disease/cure claims, prescription promotion, before/after medical imagery; ASK-HUMAN + compliance sign-off + auto-injected disclaimers for supplements/wellness; LegitScript verification gate; block AI patient testimonials outright.
- **Financial Services/Fintech — STRICT:** default REJECT for guaranteed-return claims, unlicensed investment solicitation, crypto ROI; ASK-HUMAN + sign-off + risk disclosures for balanced marketing; verify FINRA/SEC/state registration.
- **Gambling/Betting — STRICT:** default REJECT unless verified-licensed operator for the specific jurisdiction; then ASK-HUMAN + sign-off + responsible-gambling messaging + 25+ age-targeting; hard-block youth appeal or financial-solution framing.

### Cross-cutting: AI-provenance
`[RESEARCH-SUGGESTED]` Hard-wire AI-disclosure labels + provenance metadata (C2PA/IPTC)
into every generated asset by default — single control mitigates FTC Fake Reviews,
EU AI Act Art. 50, and all three platforms' disclosure rules at once.

### Regulatory volatility
`[RESEARCH-SUGGESTED]` The policy library MUST be versioned/updatable (see 0.7).
Monitor: EU AI Act Art. 50 (Aug 2 2026 / possibly Dec 2 2026); EU ECGT (Sept 27 2026);
FTC click-to-cancel re-rulemaking; Meta automated AI detection (June 1 2026). If a
rule is vacated (click-to-cancel, Green Claims), retain the underlying Section 5 /
UCPD control — the conduct stays actionable.

---

## 0.5 — Security Veto

> **Section status: `[OWNER-DECISION-REQUIRED]` for the policy stance (one line); `[CODE-VERIFIED]` for the schema gap. THIS CLASS IS NOT YET IMPLEMENTED IN CODE — see the implementation requirement below.**

### Owner decision (single line)
> **`[OWNER-DECISION-REQUIRED]`** Recommended: **absolute, deny-overrides, fail-closed.**
> Security agent reject overrides any RBAC/budget allow, no exception. Verdict
> missing/timeout/error → deny. (Consistent with Safety Suite's stateless design.)

### Combining algorithm
`[RESEARCH-SUGGESTED]`:
```
allow if { rbac_allow; budget_ok; not security_veto }
default allow := false   # fail-closed
```

### `[CODE-VERIFIED]` — current state (from the 0.5 code-verification audit)
The security veto **works nowhere in code today.** Confirmed:
- Neither engine has a verdict-carrier field. The inline engine's `DecisionProposal`
  has only `metadata: dict[str, Any]` (`extra="forbid"`). The OPA engine's
  `OPAResult` is `{allow, require_human, deny_reasons, policy_version}` — no security field.
- The inline conflict-resolution stage (`evaluator.py:264-319`) has exactly two rules
  (`authority`, `recency`) — **no `safety_veto` rule.** The wire schema comment claims
  `authority|safety_veto|policy|escalated` but code never emits `safety_veto`.
- Safety Suite verdicts (`SafetyVerdictOut`) are produced per contract but **structurally
  cannot reach the evaluator** — no event wraps them, no consumer reads them.
- `security_severity` (`evaluator.py:345`) is a **dead key** — read but never written.

### `[CODE-VERIFIED]` — required implementation (Faz 1 terminal T-veto, BEFORE OPA flag flip)
Minimal typed carrier (do not overload `metadata`):
```python
class SecurityVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_agent_id: str      # e.g. "chief_security_officer"
    reject: bool
    severity: str             # 'none'|'low'|'medium'|'high'|'critical'
    reason: str | None = None
```
Plus: a mapping step bridging `SafetyVerdictOut` → `DecisionProposal.security_verdict`;
a check in `_resolve` for `security_verdict.reject` **ahead of** authority; promote
`rule_applied` to `Literal["authority","recency","safety_veto","escalated"]` on both
`Conflict` and the wire `DecisionConflictResolved.Payload`.

---

## 0.6 — Data Access

> **Section status: `[CODE-VERIFIED]` taxonomy below is ground truth from the 24 live contracts. Owner confirms the framework; the namespace grants are NOT invented — they are what the contracts actually grant today.**

### Two-tier control model
`[RESEARCH-SUGGESTED] + [CODE-VERIFIED]`:
- **RLS = enforcement floor (do NOT re-implement in policy).** Postgres FORCE ROW
  LEVEL SECURITY on all 11 tenant tables, keyed on `current_setting('skylize.org_id')`;
  app connects as `skylize_app` (NOSUPERUSER NOBYPASSRLS) so RLS actually binds
  (migration 0003). Policy layer does NOT re-check `org_id` — RLS is that defense.
- **OPA = per-namespace scope PDP (THE gap policy must close).** RLS filters by `org_id`
  only — it has no concept of `security:fraud:*` vs `finance:*`.

### `[CODE-VERIFIED]` — THE real gap
`MemoryGateway.read/write` (`memory/gateway.py:49-56, 80-87`) checks only that the
grant list is **non-empty** — it does NOT verify the requested namespace is actually
inside the granted list. No `fnmatch`/prefix-match enforcement exists anywhere in
`src/skylize/memory/`. **This is what the data_access class + a Faz 1 terminal
(T-namespace) must close:** given `memory_read_access=["security:fraud:*"]`, verify the
requested namespace matches a granted pattern (prefix match on trailing `*`) before
allowing — not just "list non-empty."

### `[CODE-VERIFIED]` — namespace vocabulary
The real namespace format is convention `department:subtopic[:leaf]`, trailing `*` =
prefix wildcard, empty list = stateless. Grants come from the **24 live coded
contracts** (NOT the 157 markdown specs — those are not-yet-onboarded). Full
per-namespace read/write grant table is in the 0.6 code-verification audit. Key rules:
- **Scopes source:** per-agent `memory_read_access`/`memory_write_access` from agent
  contracts, **NOT authority-level ties.**
- **Do NOT normalize near-duplicate namespaces** (`finance:allocation:*` vs
  `finance:allocations:*`, etc.) — they exist as-is from parallel authoring; normalizing
  would misrepresent actual grants (M5 lesson).

### `[CODE-VERIFIED]` — zero-scope agents
Safety Suite (`chief_security_officer`, `director_ai_safety`, `llm_safety_agent`,
`prompt_injection_agent`) + `cfo` → `memory_read=[]`, `memory_write=[]` → tool-denied
if memory requested (`MemoryPermissionDenied`).

### `[CODE-VERIFIED]` — legitimate cross-org paths (policy must NOT block)
1. **Rehydration** — `rehydration_session()` (migration 0002): read-only cross-tenant
   carve-out for Governance Authority startup snapshot (kills/revocations/suspensions).
   WITH CHECK unchanged → writes never cross-tenant. Startup only, never a request path.
2. **admin_session()** — no tenant binding; platform tables (`tenants`, `tenant_users`,
   `agent_contracts`), RLS-excluded. Out of the memory-namespace model entirely.
3. **`director_risk` reading `security:fraud:summary`** — the one deliberate
   cross-department (not cross-org) grant. Intentional per risk-role design.

### Cross-org rule
`[RESEARCH-SUGGESTED]` Cross-tenant: **deny_always** except the three coded carve-outs
above. Audit every data-access decision with `org_id + scope + agent_id + purpose`.

---

## 0.7 — Policy Version Schema (cross-cutting)

> **Section status: `[OWNER-DECISION-REQUIRED]` — recommend the hybrid model; confirm.**

### Recommended schema (hybrid — bundle hash alone is insufficient for replay)
`[RESEARCH-SUGGESTED]`:
```
policy_version = {
    semver,                  # human intent, e.g. "1.0.0"
    git_sha,                 # source provenance
    bundle_sha256,           # byte-exact identity of compiled bundle
    opa_manifest_revision    # OPA-native .manifest revision
}
```

### Per-decision record (for deterministic replay + right-to-explanation)
`[RESEARCH-SUGGESTED]`:
```
decision_record = {
    decision_id,
    input_snapshot,          # full JSON input — required for deterministic replay
    result,
    policy_version,          # the full object above
    nd_builtin_cache,        # if any non-deterministic builtins used
    timestamp
}
```
Recorded on **every** decision (approve/reject/defer — not just approvals).

### In-flight consistency
`[RESEARCH-SUGGESTED]` A decision pins the `bundle_sha256` it started with — a bundle
update mid-flight does not change the version an in-progress decision is evaluated against.

---

## Faz 1 implementation items surfaced by Faz 0 audits (must land BEFORE OPA flag flip)

These are NOT policy content — they are code gaps the Faz 0 code-verifications exposed:

1. **T-veto** (from 0.5): `SecurityVerdict` field + `SafetyVerdictOut`→proposal bridge +
   `rule_applied` enum + veto check in `_resolve` ahead of authority.
2. **T-namespace** (from 0.6): prefix-match enforcement in `MemoryGateway` (currently
   only checks list non-emptiness).
3. **hitl_id reconciliation** (from T24): `publisher.py` and `hitl_writer.py` mint
   independent `uuid4()`s — single `hitl_id` minted once, flowed through both.
4. **HITL resume path** (from T24): OPA `DecisionOrchestrator.process` stops after
   escalate — no `governance.human_approval_received` handler. Deferred decisions would
   write tickets that can never resume.
5. **Consumer transport rebuild** (from T18/T24): OPA consumer onto EventBus port,
   gated on ADR-0005 department-vocabulary resolution.

---

## Sign-off

When each section is approved, change its `Section status` line to `[APPROVED]` and
record the date. Faz 2 (Rego authoring) for a given policy class may begin only when
that class's section reads `[APPROVED]`.

- 0.1 Authority: ______________________  (owner, date)
- 0.2 Spend: __________________________  (owner, date)
- 0.3 External Action: ________________  (owner, date)
- 0.4 Brand/Legal: ____________________  (owner, date)
- 0.5 Security Veto: __________________  (owner, date)
- 0.6 Data Access: ____________________  (owner, date)
- 0.7 Policy Version: _________________  (owner, date)
