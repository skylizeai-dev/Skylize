# Organizational Memory

**Status:** Subsystem specification (source of truth for org-level memory)
**Owner:** `chief_data_officer` · `director_memory_systems`
**Related:** [memory_taxonomy.md](./memory_taxonomy.md) · [knowledge_graph.md](./knowledge_graph.md) · [retrieval_strategy.md](./retrieval_strategy.md) · [../architecture/04_memory_architecture.md](../architecture/04_memory_architecture.md)

---

## 1. Purpose

Organizational memory is the company's **durable institutional knowledge**: the
decisions it made, why it made them, what worked, what failed, and the playbooks
distilled from that experience. It is what lets the agent organization *learn as
an organization* rather than each agent starting cold — the difference between a
company with memory and a company with amnesia.

## 2. Architectural role

Organizational memory spans the **episodic**, **semantic**, and **procedural**
tiers ([memory_taxonomy.md §3](./memory_taxonomy.md#3-the-six-tiers)) plus the
knowledge graph. It is built **entirely from the event log** — every decision,
outcome, and approval is already an event, so organizational memory is a
materialized, queryable projection of that immutable history, not a separate
source of truth.

```
event log (decision.*, *.performance_ingested, governance.*, approvals)
        │  Memory service projects
        ▼
organizational memory:
   • decision records (what + why + who + outcome)     → org:decisions
   • outcome history (campaign/initiative results)     → episodic
   • distilled playbooks (approved, versioned)         → procedural
   • entity/relationship graph                         → knowledge graph
```

## 3. What it stores

| Kind | Namespace | Example |
|---|---|---|
| **Decision records** | `org:decisions` | "approved spring campaign launch; policy v12; ROAS forecast 2.0; approved by vp_creative" |
| **Outcome history** | episodic | "campaign 42: spend $X, ROAS 2.1, 14-day window" |
| **Playbooks** | `org:playbooks` (procedural) | "high-CAC channel response: pause, diagnose creative, A/B new hooks" |
| **Strategic directives** | `strategy:directives` | executive directives and their cascade |
| **Lessons / postmortems** | `org:lessons` | incident learnings, failed-experiment writeups |

## 4. How it is written (governed)

Organizational memory is **not** writable by arbitrary workers. It is written by:

1. **System projection** — the Memory service materializes decision/outcome
   records from the event log automatically (read-only derivation).
2. **Governed promotion** — a `director`+ agent (or human) promotes a pattern to a
   **playbook** via an approval event; promotion is audited and versioned. This
   keeps procedural memory trustworthy: only vetted knowledge becomes "how we do
   things."

A worker emitting `memory.write_requested` for an `org:*` namespace it lacks write
access to is denied at the proxy → `audit.access_denied`.

## 5. How it is used

- **Decision context:** the Decision Engine and proposing agents recall prior
  decisions and outcomes for similar situations (via
  [retrieval_strategy.md](./retrieval_strategy.md)) to inform scoring and avoid
  repeating known failures.
- **Onboarding new agents/departments:** a new department inherits relevant
  playbooks and brand/strategy context rather than relearning from zero.
- **Explainability:** "why did we do this?" is answerable because the decision
  record links the proposal, the policy version, the score, the authorizing token,
  and the outcome via `correlation_id`.

## 6. Tenant isolation

Organizational memory is **per `org_id`** — one tenant's decisions, outcomes, and
playbooks never appear in another's recall. Cross-tenant institutional learning
(e.g. "this hook pattern works across many stores") is opt-in, anonymized, and
runs only through the governed learning pipeline
([learning_pipeline.md](./learning_pipeline.md)), never by reading another
tenant's namespace ([../architecture/04_memory_architecture.md §9](../architecture/04_memory_architecture.md#9-tenant-isolation--learning)).

## 7. Ownership & evolution

- **Owner:** `chief_data_officer` (governance of what becomes institutional
  memory), `director_memory_systems` (implementation), `director_business_intelligence`
  (reporting/projection).
- **Evolution:** MVP materializes decisions/outcomes and supports manual playbook
  promotion; at Scale, the learning pipeline can *propose* playbook updates that
  still require governed approval before becoming procedural memory.
