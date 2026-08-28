# ADR 0004 — OPA/Rego Is the Production Governance Arbiter; Inline Evaluator Is the Dev/Fallback

**Status:** Accepted (2026-07-17) — **CORRECTED 2026-08-28**, see [Correction](#correction-2026-08-28) below
**Date:** 2026-07-17
**Deciders:** Principal Architect, human owner
**Related:** [decision_engine.md §header](../../04_decision_engine/decision_engine.md) · [guardrails.md](../../04_decision_engine/guardrails.md) · [ADR-0001](./0001-governance-signature-scheme.md) · [ADR-0003](./0003-n8n-admin-governance-gap.md) · `src/skylize/decision_engine/` · `src/skylize/app/decision_engine/` · `src/skylize/bootstrap.py` · branch `feat/opa-composition-glue` (prototype, unmerged) · external ADR register (Word doc), referenced there as "ADR-003" — untracked in this repository, cited here only as a pointer, not quoted

---

## Correction (2026-08-28)

*Added after a forensic review of the code this ADR describes. The decision
above stands and is not rewritten. This section records where the ADR's title
and framing have been read as claiming more than the code does, and states the
current position precisely. Where the Context section's implementation notes
have gone stale since 2026-07-17, they are corrected here rather than edited in
place.*

### What the title implies, and what is actually running

The title says OPA "**Is** the Production Governance Arbiter." Read alone —
which is how a title gets read — that states a fact about the running system.
It is not one, and was never meant to be: §4 of the Decision already says
`"opa"` is the *destination*, not the current state. The title outran its own
body. **Read the title as "is the designated production arbiter," a commitment,
not a description.**

**The arbiter running in production today, in every environment, is the inline
evaluator** (`src/skylize/app/decision_engine/evaluator.py`). Not as a fallback
that OPA might yield to under load — as the only engine that has ever gated a
decision on this platform.

- `src/skylize/config.py:122` — `decision_engine: Literal["inline", "opa"] = "inline"`.
- `src/skylize/bootstrap.py:472-478` — raises `RuntimeError` for **any** value
  other than `"inline"`. There is no environment in which `"opa"` boots.
- **This guard is correct and must not be relaxed.** See the next subsection for
  what would happen if it were.

### The OPA package is fail-closed by design — and that is why it must stay unwired

The OPA package's fail-closed posture is real, implemented, and verified:

- `decision_engine/opa_client.py` raises `OPAPolicyDenied` on timeout, connect
  error, non-200, malformed body, and a non-dict `result`; a missing `allow` key
  defaults to `False` (`opa_client.py:163`). OPA being unreachable cannot become
  a silent allow.
- `decision_engine/pipeline.py:253-267` converts that exception into a terminal
  `DecisionOutcome.REJECTED`.

But fail-closed is only a virtue when the policy set can also say yes. **It
cannot.** All seven `.rego` files under `policy/skylize/decision/` carry
`default allow := false` and contain **zero rules that set `allow := true`** —
`decision.rego` describes itself as a "fail-closed SKELETON... it contains NO
business logic and can never approve anything."

So flipping `SKYLIZE_DECISION_ENGINE=opa` today would not produce stricter
governance. It would reject **100% of business actions**, unconditionally,
including every legitimate one. The bootstrap guard is not a temporary
inconvenience blocking a finished feature; it is what stands between the
platform and a total, silent outage of every governed action. Relaxing it before
real Rego content exists is a production incident, not a cutover.

### The inline evaluator is permissive by default — the inverse posture

The engine that *is* running has the opposite default from the one this ADR
designates. `app/decision_engine/evaluator.py`'s `policy_check` (`:314-341`)
tests three specific deny conditions — unknown action class, non-positive or
sub-director spend, `brand_safety == "blocked"` — and if none matches, **falls
through to `_PASS`** (`:341`). An action nobody wrote a rule against is
approved.

This is implicit-approve. It is the correct posture for a development stand-in
and the wrong one for a governance arbiter, and it is worth stating plainly
because the ADR's own framing invites the opposite assumption: a reader who
knows OPA is fail-closed, and reads this ADR's title, may conclude the platform
is fail-closed. **It is not.** Inverting the inline evaluator to default-deny is
scheduled work and is not done.

### No decision has ever passed real Rego

Not in production, and not in any test. Every `allow=true` under
`tests/decision_engine/` comes from `_MockOPA` or a patched `httpx` response —
`test_consumer_integration.py`, `test_orchestrator_integration.py`,
`test_pipeline.py`, `test_resume.py`, `test_department_vocabulary.py`,
`test_opa_client.py`. The only tests that would exercise a live OPA server
(`test_opa_client_integration.py:60,76`) **skip** without `SKYLIZE_TEST_OPA_URL`,
and they skip in the default run.

What the OPA test suite proves is that the client and pipeline handle OPA's
responses correctly. It proves nothing whatever about whether the policies
decide correctly, because no policy has ever decided anything.

### Stale implementation notes in the Context section

The Context section's "Current implementation state" described branch HEAD
`96f00381` on 2026-07-17. Three of its items have since changed — recorded here,
not edited above:

| Context claim (2026-07-17) | Status 2026-08-28 |
|---|---|
| "No `SKYLIZE_DECISION_ENGINE` flag exists on this branch" | **Landed.** `config.py:122`, guard at `bootstrap.py:472-478`. |
| "`consumer.py` and `constants.py` still target placeholder Redis stream names" | **Rebuilt** onto the `EventBus` port per ADR-0005; `SUBSCRIBED_STREAMS` deleted. |
| "No `policy_inputs.md` exists in the repository" | **Authored.** `docs/04_decision_engine/policy_inputs.md`, status DRAFT — awaiting owner approval. Rego may not be written against it until approved. |

The three preconditions in Decision §4 are therefore **partially** met: the
transport is rebuilt and the input contract is drafted. **Real Rego content and
wire-parity confirmation remain outstanding, and the input contract is
unapproved.** `"opa"` stays unenablable.

### Corrected position

1. The OPA package **exists**, is **genuinely fail-closed**, and is
   **deliberately unwired** — blocked on real policy content, not on plumbing.
2. The live arbiter is the **inline evaluator**, which is
   **permissive-by-default** and scheduled for inversion to default-deny.
3. `bootstrap.py:472` **stays as it is.** It is the correct guard.
4. Any document, brief, or diligence material stating that OPA gates production
   decisions today is **wrong** and must be corrected before it is distributed.

---

## Context

The repository currently contains **two independent decision-engine implementations**, and they disagree — in their own docstrings — about which one is allowed to run in production:

- **`src/skylize/app/decision_engine/`** (the inline evaluator) states, verbatim, in three places: *"The only component permitted to emit terminal `decision.*` events"* (`app/decision_engine/__init__.py:4`, `app/decision_engine/engine.py:4`, and in prose at `docs/04_decision_engine/decision_engine.md:13`, echoed in `docs/_BUILD_LOG.md:36` and a `bootstrap.py:215` comment).
- **`src/skylize/decision_engine/`** (the OPA/Rego engine) carries no equivalent "sole emitter" self-description today — its `consumer.py` docstring only describes Redis Streams delivery mechanics, not emission authority — so the inline engine's unqualified claim currently stands unopposed in the docstrings even though the two engines are meant to be alternatives, not a hierarchy.
- `docs/04_decision_engine/guardrails.md` separately documents OPA as *"the policy engine behind the Decision Engine and the Governance"* layer (line 23), a framing that assumes OPA sits in the authorization path rather than being excluded from it.

This is the same class of documentation-vs-implementation drift ADR-0001 and ADR-0002 resolved: two components make mutually exclusive "sole emitter" claims, and the repo has no code that decides between them. `bootstrap.py:224` (this branch, HEAD `96f00381`) unconditionally wires the inline `DecisionEngine` with no selector — so today the inline engine is the de facto sole emitter, but only because nothing else runs, not because of a decision anyone made and recorded.

The human owner has separately referenced this drift as tracked findings ("T15/T16/T18") and an external ADR register (a Word document, referenced there as "ADR-003") that states OPA is the sole arbiter. Neither is present in this git repository, its history, or any local branch/worktree checked during this ADR's research — they are cited here as the owner's stated source for the decision below, not as verifiable in-repo artifacts. This ADR is the durable, in-repo record; if the external register or T15/T16/T18 documents are later added to the repository, they should be cross-linked back to this ADR rather than treated as a separate source of truth.

**Current implementation state** (verified against this branch during review, relevant because the decision below has follow-up work attached to it):

- No `SKYLIZE_DECISION_ENGINE` flag exists on this branch. `bootstrap.py` does not branch on engine choice at all.
- A prototype of the flag exists on the unmerged sibling branch `feat/opa-composition-glue` (`src/skylize/config.py:90-98`, `bootstrap.py:215-224`): a `decision_engine: Literal["inline", "opa"]` setting defaulting to `"inline"`, with `bootstrap.py` **raising `RuntimeError` (fail-closed) for any value other than `"inline"`** — because, per that branch's own comment, "the OPA decision engine's consumer is not wired to the EventBus yet."
- `src/skylize/decision_engine/consumer.py` and `constants.py` still target placeholder Redis stream names (`SUBSCRIBED_STREAMS` in `constants.py:5-9`), two of which are not real event types in the current schema. The OPA engine's evaluation pipeline (`pipeline.py`), OPA HTTP client (`opa_client.py`), and publisher (`publisher.py`) are implemented, but the consumer that would connect them to the live `EventBus` is not.
- No `policy_inputs.md` exists in the repository. If Rego policy authoring conventions are to be gated on such a document, it does not yet exist and is not created by this ADR.

## Decision

**The OPA/Rego engine (`src/skylize/decision_engine/`) is Skylize's designated production governance arbiter for MVP launch.** The inline evaluator (`src/skylize/app/decision_engine/`) is the development stand-in and production fallback.

1. **Selection is per-environment, via `SKYLIZE_DECISION_ENGINE`.** The setting takes exactly one of two values:
   - `"inline"` — the port-based inline evaluator (`app/decision_engine/`). Development default and the only value permitted while the OPA consumer transport is unfinished (see Consequences).
   - `"opa"` — the OPA/Rego engine (`decision_engine/`). The production target.

2. **Exactly one engine is the sole emitter of terminal `decision.*` events per environment — never both, and never neither.** The "only component permitted to emit terminal `decision.*` events" claim currently hard-coded into both engines' docstrings is not false, but it is incomplete: it is true *of whichever engine `SKYLIZE_DECISION_ENGINE` selects for that environment*, not universally true of either engine in isolation. Both docstrings must be corrected to say so (see Consequences).

3. **Fail-closed on misconfiguration.** An unrecognized or unset-but-required value for `SKYLIZE_DECISION_ENGINE`, or a request to select `"opa"` before the OPA consumer transport is production-ready (see item 4), must raise at startup rather than silently falling back to a different engine than the one configured. Silently running the wrong arbiter — however similar its output — is a governance-integrity failure, not a degraded mode. This mirrors the fail-closed posture ADR-0003 established for `SKYLIZE_ENABLE_N8N_ADMIN`.

4. **`"opa"` is not yet enablable in production, and this ADR does not make it so.** Selecting OPA as the production arbiter is the *destination* this ADR commits to, not a claim that the cutover is complete today. Before any environment may set `SKYLIZE_DECISION_ENGINE=opa`:
   - the OPA consumer transport (`decision_engine/consumer.py`, `constants.py`) must be rebuilt onto the live `EventBus` port — the same seam the inline engine already uses — replacing the current placeholder Redis-stream event types with real, schema-backed ones;
   - the Rego policy set the OPA engine evaluates against must be defined and reviewed against a `policy_inputs.md` (or equivalently named) input-contract document, which does not yet exist and must be authored before policies are written against assumed inputs;
   - wire-level parity between the two engines' `decision.*` event payloads must be confirmed, so that swapping the arbiter does not silently change what downstream consumers (audit, HITL, capital allocation) receive.

   Until all three land, `SKYLIZE_DECISION_ENGINE` must fail closed to `"inline"` being the only accepted value, exactly as prototyped on `feat/opa-composition-glue`.

## Scope / invariants preserved

- **The six-stage evaluation model is unchanged.** Both engines implement the same conceptual pipeline (authority → policy → scoring → capital → conflict → HITL gate); this ADR chooses which implementation is authoritative per environment, not a new model.
- **`GovernanceToken` chain-of-trust and validation order are unchanged** (ADR-0001) — neither engine bypasses token/authority/kill-switch checks.
- **The Orchestrator / event-bus seam is unchanged.** Both engines are, or (for OPA) will be, plain `EventBus` consumers/producers; neither is special-cased into the runtime.
- **Postgres-first, then-stream publication order in `decision_engine/publisher.py` is unchanged** by this ADR; it becomes relevant only once OPA is production-selected.

## Consequences

- **Both engines' "sole emitter" docstrings/docs are corrected** to state per-environment, flag-selected exclusivity instead of an unqualified universal claim, each pointing back to this ADR:
  - `docs/04_decision_engine/decision_engine.md:13`
  - `src/skylize/app/decision_engine/__init__.py:4`
  - `src/skylize/app/decision_engine/engine.py:4`
  - `docs/_BUILD_LOG.md:36`
  - `src/skylize/bootstrap.py:215` (comment)
- **`docs/04_decision_engine/guardrails.md`** gets a short note cross-linking this ADR at its OPA framing (line ~23), since it now describes the designated production path rather than an auxiliary policy check.
- **Transport rebuild is required before OPA can run anywhere**, tracked as launch-blocking follow-up: rebuild `decision_engine/consumer.py` / `constants.py` onto the live `EventBus`, replacing placeholder Redis-stream event types with real ones.
- **Rego policy authoring is gated on a `policy_inputs.md` input contract** that does not yet exist and must be authored before production Rego policies are written or reviewed.
- **The inline evaluator is retained, not deprecated.** It remains the development default and the designated production fallback until wire-event parity between the two engines is assessed post-launch. No code deletion results from this ADR.
- **No code change results from this ADR itself** — it is a documentation-only record of the owner's decision and the follow-up it obligates. Introducing `SKYLIZE_DECISION_ENGINE` on this branch (porting the prototype from `feat/opa-composition-glue`), rebuilding the OPA consumer transport, and authoring `policy_inputs.md` are tracked as required follow-up work, not completed by this ADR.
- **T15/T16/T18 and the external Word-doc ADR register are cited as the owner's stated source for this decision but are not verifiable in-repo artifacts.** If and when they are added to this repository, they should cross-link back to this ADR rather than stand as an independent, unreconciled source of truth — the drift this ADR resolves was exactly two documents disagreeing without a cross-link.

## Alternatives considered

- **Make the inline evaluator the permanent sole arbiter, drop OPA.** Rejected: the owner has explicitly decided OPA is the production arbiter for MVP launch; the OPA engine's pipeline, client, and publisher are already substantially implemented and represent real investment, and Rego policy-as-code is the intended long-term governance authoring surface.
- **Cut over to OPA immediately, without a flag.** Rejected: the OPA consumer transport is not wired to the live `EventBus` today (placeholder Redis-stream event types, two of which don't exist in the schema). An immediate cutover with no fallback would either fail at startup with no governed engine running, or run against a broken transport — both worse than a flag-gated, fail-closed rollout.
- **Run both engines simultaneously and reconcile their output.** Rejected: this reintroduces exactly the two-emitter ambiguity this ADR resolves. Governance's audit and replay guarantees depend on a single, unambiguous emitter of terminal `decision.*` events per environment, the same reasoning ADR-0002 applied to orchestration frameworks.
- **Leave the docstrings' unqualified "sole emitter" claims as-is and resolve the drift informally.** Rejected: the same reasoning as ADR-0001 and ADR-0002 — a security/governance-critical contradiction between components must be an explicit, cross-linked decision, not left to whichever docstring a reader happens to trust.
