# Coding Standards

**Status:** Development specification (source of truth for code conventions)
**Owner:** `vp_engineering` · `director_backend` · Principal Architect
**Related:** [deployment_strategy.md](./deployment_strategy.md) · [../02_architecture/repository_structure.md](../02_architecture/repository_structure.md) · [../03_agents/agent_contract_registry.md](../03_agents/agent_contract_registry.md) · [../architecture/01_final_stack.md](../architecture/01_final_stack.md)

---

## 1. Purpose

Coding standards make the architecture's invariants **enforceable in code**, not
just documented. The goal: a new engineer writes code that reads like the
surrounding code and *cannot* accidentally violate a boundary.

## 2. Architectural role

The standards encode the boundary/governance rules as lint/type/test gates so the
CI **contract gate** ([../architecture/06_deployment_architecture.md §7](../architecture/06_deployment_architecture.md#7-cicd-pipeline))
can fail a build that breaks them — the same way the registry fails closed on an
invalid contract.

## 3. Language & framework conventions

- **Python 3.12**, typed throughout; `from __future__ import annotations`.
- **Pydantic v2** for every boundary model (events, contracts, tokens, I/O
  schemas); `model_config = ConfigDict(extra="forbid")` and `frozen=True` for
  immutable envelopes — matching the spine
  ([../02_architecture/event_driven_architecture.md §3](../02_architecture/event_driven_architecture.md#3-event-schema-standard-pydantic-v2-versioned)).
- **FastAPI** routes are thin adapters over the service layer; no business logic
  in routes.
- **asyncio** end to end; no blocking I/O in async paths.
- **Frontend:** TypeScript strict, Next.js + Tailwind + shadcn/ui.

## 4. The boundary rules (enforced)

| Rule | Enforcement |
|---|---|
| `agents/` may import **only** `schemas/` | import-linter in CI ([../02_architecture/repository_structure.md §4](../02_architecture/repository_structure.md#4-package-to-boundary-mapping)) |
| No vendor SDK in agent/domain code; only in `adapters/` | import-linter + review |
| No raw DB access outside the DAL | import-linter; DAL holds creds |
| No secret in code/env-in-VCS/logs/events/memory | secret scanner in CI |
| No direct stream writes by agents | only Orchestrator publishes |
| Every event/contract validates or fails closed | Pydantic + contract tests |

## 5. Naming & consistency (the canonical vocabulary)

Use the canonical terms verbatim ([../README.md §3](../README.md#3-canonical-vocabulary-used-identically-everywhere)):
authority levels (`executive/vp/director/manager/worker`), interfaces
(`IF-*`), event categories, failure modes, token validation order. **Do not
introduce synonyms.** `agent_id`s are globally unique, lowercase_snake_case, and
match the org-chart docs and contract registry.

## 6. Errors, failure, and idempotency

- **Fail closed** on doubt for security-relevant paths; choose the contract's
  `failure_mode` deliberately ([../architecture/03_agent_runtime.md §7](../architecture/03_agent_runtime.md#7-failure-modes)).
- **Idempotent** event consumers (keyed on `event_id`) — at-least-once delivery
  means redelivery happens.
- No silent drops: invalid input → reject + audit, never swallow.

## 7. Testing

- **Unit** for logic; **contract tests** for every event schema, `AgentContract`,
  and OPA policy (these are the CI gate); **replay tests** for decision/memory
  regressions.
- A schema/contract/policy that doesn't load or breaks compatibility **fails the
  build**.
- Tenant-isolation tests assert 0 cross-`org_id` access.

## 8. Observability in code

Every new hop emits an OTel span with `correlation_id`/`event_id`, structured
logs (PII-safe), and — for LLM calls — Langfuse keyed by `governance_token_id`
([../08_operations/observability.md](../08_operations/observability.md)). Missing
instrumentation is a review-blocking defect.

## 9. Reviews & supply chain

- PRs require review; security-relevant changes (governance, adapters, DAL, OPA
  policy) require a security reviewer.
- Dependencies pinned + lockfiles; SBOM per build; images signed, non-root,
  minimal base ([../architecture/05_security_architecture.md §11](../architecture/05_security_architecture.md#11-supply-chain--platform-hardening)).
- Commit messages and PRs follow the repo convention; no `--no-verify`.

## 10. Ownership & evolution

- **Owner:** `vp_engineering`, `director_backend` (Python), `director_frontend`
  (TS), Principal Architect (boundary rules).
- **Evolution:** new enforcement (lint/test) is added when a class of mistake is
  seen twice; the boundary rules are permanent.
