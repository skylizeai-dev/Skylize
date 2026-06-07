# Scoring Models

**Status:** Subsystem specification (source of truth for decision scoring)
**Owner:** `chief_data_officer` · `director_ml` · Principal Architect
**Related:** [decision_engine.md](./decision_engine.md) · [decision_flow.md](./decision_flow.md) · [capital_allocation.md](./capital_allocation.md) · [../05_memory/retrieval_strategy.md](../05_memory/retrieval_strategy.md)

---

## 1. Purpose

Scoring models give the Decision Engine a **deterministic, explainable number**
for decisions that require ranking or sizing rather than a binary allow/deny:
which creative variant to promote, how much budget a campaign should receive,
which lead to prioritize, which vendor to select. Scoring informs the decision;
it never *is* the decision — policy and authority still gate the outcome.

## 2. Architectural role

Scoring is **STAGE 3** of the evaluation flow
([decision_flow.md §3](./decision_flow.md#3-the-flow)). It runs after authority
and policy checks pass and before capital and HITL gates. A score annotates the
`decision.evaluated` record; it is never terminal on its own. Scoring is
deterministic given inputs so that decisions stay replayable
([decision_engine.md §5](./decision_engine.md#5-determinism-and-explainability)).

## 3. Scoring philosophy

1. **Deterministic & versioned.** A score is a pure function of (features,
   weights, model_version). The same inputs always produce the same score; the
   `model_version` is recorded on the decision.
2. **Explainable.** Every score decomposes into named, weighted contributions —
   no opaque black box drives a spend or launch decision.
3. **Auditable & re-runnable.** Because features are sourced from the event log
   and memory, a past score can be recomputed under the model version that was in
   force.
4. **Human-overridable.** A score is advisory to the human at a HITL gate; it
   never removes the human's authority.

## 4. Standard scoring models

| Model | Used for | Key features | Output |
|---|---|---|---|
| **Creative Score** | rank creative variants | predicted CTR/CVR, brand-fit, novelty vs. fatigue, past-win similarity | 0–100 |
| **Campaign Allocation Score** | size/rank budget proposals | ROAS trend, CAC, headroom, confidence, risk | 0–100 + suggested amount |
| **Lead/Account Score** | prioritize sales effort | fit, intent signals, engagement recency, expected value | 0–100 |
| **Vendor Score** | procurement selection | price, reliability, risk, contract terms | 0–100 |
| **Security Score** | risk-rank flags | anomaly magnitude, blast radius, confidence | 0–100 severity |

These map to the corresponding scoring worker agents (`creative_score_agent`,
`supplier_score_agent`, `security_score_agent`, etc.), whose specs reference this
document.

## 5. Score structure (illustrative)

```python
from pydantic import BaseModel, ConfigDict

class FeatureContribution(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float          # value * weight, for explainability

class Score(BaseModel):
    model_config = ConfigDict(frozen=True)
    model_id: str                # e.g. "creative_score"
    model_version: str           # recorded on the decision
    value: float                 # 0–100
    contributions: list[FeatureContribution]
    confidence: float            # 0–1; low confidence can trigger HITL
    computed_at: str
```

- **Features** are pulled tenant-scoped via the DAL and from semantic/episodic
  memory ([../05_memory/retrieval_strategy.md](../05_memory/retrieval_strategy.md)) —
  never cross-tenant.
- **Low confidence** on an irreversible action maps to the
  `LOW_CONFIDENCE_IRREVERSIBLE` HITL trigger.

## 6. Model lifecycle (MVP → learned)

- **MVP:** transparent **weighted linear / rule-based** models with
  human-set weights — fully explainable, no training pipeline needed, easy to
  audit. This is the default and the compliance-safe baseline.
- **Scale:** weights/curves may be learned by the ML function
  (`director_ml`) via the governed learning pipeline
  ([../05_memory/learning_pipeline.md](../05_memory/learning_pipeline.md)) on
  **de-identified, consented** data only. A learned model must still emit the same
  explainable `contributions` structure; black-box models that cannot explain a
  spend/launch decision are not permitted at the decision boundary.
- Every model version is registered, tested against a holdout, and gated by the
  CI contract gate before it can score a real decision.

## 7. Tenant isolation

Scoring features are computed per `org_id`; a tenant's model weights and history
never leak across tenants. Cross-tenant pattern learning is opt-in, anonymized,
and runs in the separate governed pipeline
([../05_memory/learning_pipeline.md](../05_memory/learning_pipeline.md)), never by
reading another tenant's data.

## 8. Ownership & evolution

- **Owner:** `chief_data_officer` (model governance), `director_ml` (model
  implementation), `director_analytics` (feature definitions), Principal Architect
  (the scoring contract).
- **Evolution:** new decision classes add a registered model; the
  explainability and determinism invariants hold for every model regardless of
  whether weights are hand-set or learned.
