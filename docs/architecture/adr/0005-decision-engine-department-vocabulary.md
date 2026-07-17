# ADR 0005 — Decision Engine Department Vocabulary: An Event's Category Is Not Its Department Channel

**Status:** Proposed — resolution pending. Decision (1) below is binding on acceptance *and in the interim*.
**Date:** 2026-07-17
**Deciders:** Principal AI Infrastructure Engineer, human owner
**Related:** [../../04_decision_engine/decision_engine.md](../../04_decision_engine/decision_engine.md) · [../../04_decision_engine/decision_flow.md](../../04_decision_engine/decision_flow.md) · [../../02_architecture/event_driven_architecture.md](../../02_architecture/event_driven_architecture.md) · `src/skylize/decision_engine/constants.py:31-49` · `src/skylize/decision_engine/pipeline.py:193-223` · `src/skylize/contracts/mvp/growth.py:17` · `src/skylize/contracts/mvp/sdr.py:17,39` · `src/skylize/events/bus.py:27` · `src/skylize/schemas/base.py:57` · commits `c04b5651` (the glue + the deferred-rebuild note), `3dd4dd71`

---

## Context

The OPA-backed decision engine (`src/skylize/decision_engine/`) is complete except for its transport. It is gated behind `SKYLIZE_DECISION_ENGINE` (`src/skylize/config.py:98`, default `"inline"`), and the composition root **fails closed** on `"opa"` rather than silently falling back, because `DecisionEngineConsumer` was never rebuilt onto the `EventBus` port. Commit `c04b5651` recorded that rebuild as the one blocker to flipping the flag, and `constants.py` documents it in place.

Scoping that rebuild surfaced a conflict that the rebuild itself cannot absorb. **The engine has two different vocabularies for "department" and treats them as one.**

### The two vocabularies

- **Event category / type namespace.** `SalesCampaignProposed` is `category=EventCategory.SALES`, `type="sales.campaign_proposed"` (`schemas/events/sales.py:44-46`). The `sales.` prefix is a *taxonomy* — which family of business event this is.
- **Department channel.** `BaseEvent.department` is the *"owning department channel"* (`schemas/base.py:57`), and it is the routing key: the bus writes every event to `evt:{tenant}:{department}` (`events/bus.py:27`), and consumers subscribe per-(org, department).

These answer different questions: *"what kind of event is this?"* versus *"which team's channel does it ride?"*

### Where they are conflated

`constants.py:31-49` builds the AUTHORITY allow-list by splitting the event type on `.` and calling the prefix a department:

```python
for event_type in SUBSCRIBED_EVENT_TYPES:      # "sales.campaign_proposed"
    department = event_type.split(".", 1)[0]   # -> "sales"
...
ALLOWED_DEPARTMENTS = frozenset(ALLOWED_EVENT_TYPES_BY_DEPARTMENT)  # {"creative", "sales"}
```

The docstring states the assumption outright: *"Group `SUBSCRIBED_EVENT_TYPES` by their `{department}.` prefix."* In this codebase that assumption is **false for the two spend-bearing events the engine exists to govern**:

- Campaign and budget-reallocation proposals are owned by **`director_growth`**, whose contract is `department="growth"` (`contracts/mvp/growth.py:17`, output schema `CampaignProposalOut`). `sales.py`'s own module docstring says the same: *"Departments: Sales Intelligence, Growth."*
- `department="sales"` is owned by the **SDR agents** — `sdr_outreach_agent` and `lead_qualifier_agent` (`contracts/mvp/sdr.py:17,39`). They do outbound sequences and lead qualification. They never propose campaigns.

So `sales.campaign_proposed` is a `sales.`-category event produced by the **growth** department, and the `sales` department is a different team entirely.

### Why this blocks the transport rebuild

`_stage_authority` (`pipeline.py:193-223`) gates on that derived set:

```python
dept_ok = context.department in ALLOWED_DEPARTMENTS   # {"creative", "sales"}
type_ok = context.event_type in allowed_types
passed  = dept_ok and type_ok
```

Both possible wirings fail, in opposite directions:

- **Subscribe per-(org, department), mirroring the inline engine** (`app/decision_engine/engine.py:47`, `DEFAULT_DEPARTMENTS = ("creative", "growth", "governance")`) → a real campaign proposal arrives stamped `department="growth"` → `"growth" in {"creative","sales"}` is `False` → **REJECTED at stage 1** with *"department 'growth' not served by the engine"*. Every spend proposal auto-denied, with a governance-shaped audit trail saying policy did it.
- **Subscribe to `evt:{tenant}:sales`** to satisfy the allow-list → that is the SDR channel; campaign proposals never land there → the consumer runs and **receives nothing**.

A governance engine that silently rejects every spend proposal is strictly worse than one that is switched off. This is why the rebuild stops here rather than picking a side.

### Why it has stayed invisible

- **`creative.review_requested` aligns by coincidence.** Its category *is* `creative` and its department *is* `creative`, so 1 of the 3 subscribed types works. Every existing test fixture uses exactly that type (`tests/integration/test_decision_engine_wiring.py:24`, `department="creative"`).
- **The conflict is currently latent.** No production code publishes any of the three subscribed events. `SalesCampaignProposed` and `SalesBudgetReallocationProposed` have **zero construction sites anywhere in `src/` or `tests/`** — the agent-output → event mapping was never written. Nothing can misroute yet because nothing routes at all.
- **The one test that covers the sales path encodes the bug as expected behavior.** `tests/decision_engine/test_orchestrator_integration.py:59-66` hand-builds:

  ```python
  DecisionContext(department="sales", event_type="sales.campaign_proposed", ...)
  ```

  That pairing is unreachable in production — no producer emits it, and no `evt:{tenant}:sales` publisher exists that would. The test is green because it constructs the context directly and **bypasses the transport**, which is precisely the layer the rebuild would connect. Its green status is false assurance, and any transport rebuild that made this test pass end-to-end would have had to fabricate the same impossible event.

### What is *not* affected

The inline engine (`app/decision_engine/`) is **correct and unaffected**. It has no department allow-list at all: `DecisionProposal.from_event` dispatches on `isinstance` and takes `department` from the event itself (`app/decision_engine/events.py:85-151`). It subscribes to `creative` / `growth` / `governance` and therefore already receives growth-stamped campaign proposals correctly. The defect is confined to the OPA package's AUTHORITY stage.

## Decision

**1. Hard gate (binding, effective immediately — not contingent on this ADR's acceptance).**
The `DecisionEngineConsumer` transport rebuild **MUST NOT land**, and `SKYLIZE_DECISION_ENGINE=opa` **MUST NOT be set in any environment**, until this ADR is accepted with a resolution to the vocabulary conflict. The current fail-closed posture is the safe one and must be preserved. Wiring the transport under today's vocabulary would convert a never-started engine into a *running* engine that denies every spend proposal and writes an audit trail attributing the denial to policy. **Fail-closed and unwired is the correct state until the vocabulary is settled.**

**2. The resolution is deferred to this ADR's acceptance.**
Choosing the department vocabulary is a governance-modeling decision spanning the AUTHORITY stage, the agent contracts, and the bus routing key. It is out of scope for a transport rewrite and is not being made unilaterally by the engineer who found it. The candidates are laid out under *Alternatives considered*; **Alternative A is the recommendation**.

**3. Resolve before a producer exists — this is the cheap moment.**
No producer for `sales.campaign_proposed` has been written yet. Whoever writes it will hardcode a `department`, and from then on the vocabulary is load-bearing in runtime code and the fix gets more expensive and riskier. Settling it now costs a constants table; settling it later costs a migration of live event routing.

## Scope / invariants preserved

- **Docs-only.** This ADR changes no code, no config, no test, and no policy. It records a finding and a gate.
- **The inline engine remains the wired default** and is untouched — it does not share the defective allow-list.
- **The flag stays `"inline"`; fail-closed on `"opa"` is preserved.** This ADR does not weaken that gate; Decision (1) reinforces it.
- **No Rego / policy content is touched.** The conflict is in the Python AUTHORITY stage's *vocabulary*, not in any policy rule.
- **The OPA package is not removed or reverted.** Everything landed in `c04b5651` / `3dd4dd71` stands; only the transport rebuild is gated.

## Consequences

- **The published "what remains before staging flag-flip" is wrong and should be corrected.** It is *not* "live OPA server + real Rego only." The actual remaining list is, in order: **(1)** this ADR accepted; **(2)** the consumer transport rebuild onto the `EventBus` port; **(3)** the `hitl_id` reconciliation (below); **(4)** a HITL resume path (below); **(5)** a live OPA server + real Rego policies. Items (3) and (4) are independent of this ADR and can proceed on their own tracks.
- **`tests/decision_engine/test_orchestrator_integration.py` must be re-fixtured** once (2) is resolved. Its `department="sales"` context must become whatever the accepted vocabulary makes real, and the assertion that the sales path reaches OPA at all is currently unproven.
- **Related open item — do not fold into this ADR's scope.** The `hitl_id` is minted **twice, independently**: `publisher.py:458` mints `uuid4()` into the `decision.deferred_to_human` payload, while `hitl_writer.py:112` mints a *different* `uuid4()` for the `hitl_queue` row. The governance event and the queue row therefore disagree on the ticket id for the same deferral, so a consumer resolving the event cannot find the row. The inline engine already shows the correct pattern — a deterministic `hitl_id_for(proposal_id)` via `uuid5` (`app/decision_engine/events.py:48`). This is a real defect on its own track; it is *not* caused by, and does not depend on, the vocabulary conflict.
- **Related open item — no HITL resume path on the OPA side.** `DecisionOrchestrator.process` runs evaluate → publish → escalate and stops. Nothing in `src/skylize/decision_engine/` handles `governance.human_approval_received`, and it is absent from `SUBSCRIBED_EVENT_TYPES`. The inline engine resumes deferred decisions via `_resume_from_human` (`app/decision_engine/engine.py:269`). As it stands, flipping the flag would produce HITL tickets that can never be resumed into a terminal outcome — a second, independent flag-flip blocker.
- The `SUBSCRIBED_STREAMS = list(SUBSCRIBED_EVENT_TYPES)` aliasing in `constants.py:28` becomes meaningless under any resolution — stream keys are `evt:{tenant}:{department}`, never event-type names. Whichever alternative is accepted should delete the alias rather than re-point it.

## Alternatives considered

- **A. Replace the prefix derivation with an explicit `department → event-type` table. (Recommended.)**
  ```python
  ALLOWED_EVENT_TYPES_BY_DEPARTMENT = {
      "creative": frozenset({"creative.review_requested"}),
      "growth":   frozenset({"sales.campaign_proposed",
                             "sales.budget_reallocation_proposed"}),
  }
  ALLOWED_DEPARTMENTS = frozenset(ALLOWED_EVENT_TYPES_BY_DEPARTMENT)
  # subscriptions = ALLOWED_EVENT_TYPES_BY_DEPARTMENT.keys() -> creative, growth
  ```
  Keeps both AUTHORITY checks intact (*is this department served?* and *may it raise this type?*) but sources the mapping from the agent contracts, which are the actual authority on department ownership, instead of inferring it from a string prefix. Confined to `constants.py`; makes the subscription set fall out of the same table, so transport and authority can never drift again. It matches the inline engine's department set (minus `governance`, pending the resume-path item above). **Cost:** it changes governance semantics — which departments the engine serves becomes an explicit, reviewable declaration rather than a derived accident — which is exactly why it needs sign-off rather than a quiet patch.

- **B. Restamp campaign proposals to `department="sales"` so the prefix derivation holds.** Rejected. `director_growth` genuinely *is* the Growth department; `department="sales"` is already owned by the SDR agents. This collides two unrelated teams onto one channel, breaks the inline engine (which correctly watches `growth`), and contorts the org model to satisfy an implementation detail of a string split. It fixes the symptom by making the data wrong.

- **C. Insert a category → department mapping layer between the bus and the pipeline.** Rejected. This is a third routing concept alongside the bus's department keying and the pipeline's allow-list, and it would need maintaining in lockstep with both. The mapping it would encode (`sales.* → growth`) is exactly the table Alternative A declares directly, minus the indirection.

- **D. Drop the department check from AUTHORITY and gate on `event_type` alone.** Rejected. The check is a real governance control — it asserts an event's department is one the engine serves and that the department is entitled to raise that event type. Deleting a control to resolve a naming conflict trades a correctness bug for a governance hole, and would let any department raise any subscribed event type.

- **E. Proceed with the rebuild, subscribing to `growth`, and let AUTHORITY reject.** Rejected, and recorded only because it is what following the original task scope literally would have produced. It ships an engine whose observable behavior is "denies all spend, cites policy." A test written against it would have to fabricate `department="sales"` to go green — the same impossible fixture `test_orchestrator_integration.py` already contains — which is how the defect would have reached staging with a green suite behind it.
