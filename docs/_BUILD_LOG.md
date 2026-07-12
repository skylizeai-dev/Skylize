# Skylize Build Log

The consistency anchor for the entire build. One line per file written, in order.

## Foundation documents (the spine)

- `docs/02_architecture/system_boundaries.md` — Defines Skylize vs. external ownership and the six named interfaces (IF-EDGE/AGENT/DATA/EVENT/TOOL/INTEGRATION), boundary enforcement (API gateway, auth tokens, signed governance tokens), and rejection behavior at each boundary.
- `docs/02_architecture/event_driven_architecture.md` — Redis Streams event bus: versioned Pydantic v2 schema, six-category taxonomy (Creative/Sales/Memory/Decision/Governance/Audit), publisher/subscriber contracts, Decision Engine flow, DLQ, replay, ordering, and per-type retention.
- `docs/03_agents/agent_governance.md` — Authority hierarchy (executive/vp/director/manager/worker), autonomous-vs-escalate matrix, the canonical GovernanceToken definition, capability model, tool manifest, circuit breaker, kill switch, human-in-the-loop triggers, audit requirements, and conflict resolution.
- `docs/03_agents/agent_contract_registry.md` — The Pydantic v2 AgentContract schema, the byte-identical GovernanceToken, five example contracts (ceo, vp_creative, copy_director, hook_generator_agent, fraud_detection_agent), and the Orchestrator registry lookup/resolution pattern.

## Conformance tooling

- `scripts/gen_manifest.js` — Reproducible Node generator that walks docs/03_agents, infers authority_level/parent/escalation_path per agent, flags duplicates/path-anomalies/stray-text files, and writes the generation manifest CSV.
- `docs/03_agents/_generation_manifest.csv` — Review-only manifest of all 154 agent files with inferred authority_level, parent_agent_id, escalation_path, and per-file issue notes (pre-generation).

## Final architecture documents

- `docs/architecture/01_final_stack.md` — Final technology selection per category with rationale/alternatives/migration, rejected overlapping tech (one tool per job), MVP-vs-Scale stack, and anti-lock-in invariants.
- `docs/architecture/02_system_architecture.md` — Layered system view, consolidated boundaries, agent communication architecture (delegation/event/decision modes), and LangGraph orchestration architecture.
- `docs/architecture/03_agent_runtime.md` — Agent sandbox, tool proxy validation order, LangGraph execution stack, lifecycle, failure modes, escalation/HITL/conflict at runtime, and framework migration path.
- `docs/architecture/04_memory_architecture.md` — Memory tiers and store assignment (Qdrant/Postgres/Redis/S3), event-sourced read/write paths, consistency model, tenant isolation, and scale migration behind VectorStore/MemoryRepository ports.
- `docs/architecture/05_security_architecture.md` — Threat model, identity/authZ layers, the Ed25519 governance-token chain of trust, zero-trust agent runtime, secrets, defense-in-depth tenant isolation, incident controls, and audit/compliance.
- `docs/architecture/06_deployment_architecture.md` — Deployable units, MVP (Compose) → Scale (Kubernetes) topology, per-component migration triggers, CI/CD contract gate, backup/replay, observability, and disaster recovery.

## Wave 1 — Architecture (supporting docs)

- `docs/README.md` — Master documentation index: what Skylize is, the four-document spine, the canonical vocabulary (authority levels, interfaces, event categories, failure modes, token validation order, core objects), the section map, the new-engineer reading order, and build provenance.
- `docs/02_architecture/repository_structure.md` — Physical repo layout projected onto the logical boundaries; package-to-boundary mapping; the enforced rule that `agents/` may import only `schemas/`; the org-chart-shaped docs mirror; manifest-flagged known issues (vc_procurement, departmant typo, duplicate director_vendor_management/CPO, depth contradictions).
- `docs/02_architecture/system_architecture.md` — Index to the consolidated `architecture/02`; one-screen system view, the three agent-communication modes, orchestration summary (LangGraph + Orchestrator facade), multi-tenancy summary.
- `docs/02_architecture/service_map.md` — Catalogue of runtime services (gateway, api, orchestrator, governance, decision-engine, agent-worker, memory, integration-adapters, worker-archiver) with boundary/consume/produce/scale; the contract registry as an orchestrator concern; service dependency direction; CI contract gate.
- `docs/02_architecture/tech_stack.md` — Index to the consolidated `architecture/01`; five decision principles, the stack at a glance, reconciliation of Temporal/LangGraph/OPA into the canonical stack (LangGraph = durable control plane, Temporal not separately run in v1, OPA = policy engine behind Decision Engine/Governance), one-tool-per-job, anti-lock-in invariants.

## Wave 2 — Decision Engine

- `docs/04_decision_engine/decision_engine.md` — The intent→outcome subsystem; six evaluation stages (authority, OPA policy, scoring, capital, conflict, HITL); determinism/explainability; failure handling; the only emitter of terminal DecisionEvents.
- `docs/04_decision_engine/decision_flow.md` — Step-by-step control flow with all branches (approve/reject/escalate/conflict/HITL), escalation routing, conflict resolution order, HITL pause/resume, and delivery semantics (idempotency, ordering, retry/DLQ).
- `docs/04_decision_engine/guardrails.md` — OPA/Rego policy engine behind the Decision Engine; token∩policy model; policy classes; default-deny rule shape; evaluation contract; versioning/testing/audit; distributed policy ownership.
- `docs/04_decision_engine/scoring_models.md` — Deterministic, explainable, versioned scoring (creative/allocation/lead/vendor/security); Score structure with feature contributions; MVP linear → governed learned models; tenant isolation.
- `docs/04_decision_engine/capital_allocation.md` — Spend control as STAGE 4; budget hierarchy + tightest-ceiling-wins; the budget ledger with reservations; authority×spend matrix; reallocation flow; fail-closed safety.
- `docs/04_decision_engine/kill_switch_protocol.md` — Operational protocol for the human override; scopes (agent→dept→tenant→platform); who may engage; engage/disengage protocols; relationship to circuit breaker; testing/drills.

## Wave 3 — Memory

- `docs/05_memory/memory_taxonomy.md` — The six tiers (working/episodic/semantic/procedural/organizational/audit), namespaces and grant-gating, tier-by-tier detail, consistency model.
- `docs/05_memory/organizational_memory.md` — Institutional knowledge projected from the event log; decision records/outcomes/playbooks; governed promotion; use in decisions/onboarding/explainability; per-tenant isolation.
- `docs/05_memory/knowledge_graph.md` — Entities/relationships materialized in Postgres relations (+Qdrant fuzzy), not a separate graph DB; storage model; build/query; tenant isolation; deferral of a graph DB.
- `docs/05_memory/retrieval_strategy.md` — Hybrid recall (Qdrant vector + Postgres FTS + graph) with RRF fusion + deterministic re-rank; non-negotiable tenant/namespace scoping; caching; feeding the Decision Engine.
- `docs/05_memory/learning_pipeline.md` — The single governed cross-tenant learning path; opt-in + de-identification + review gates; five hard constraints; always-allowed per-tenant learning; off at MVP.

## Wave 4 — Security & Operations

- `docs/07_security/permissions.md` — Three-layer authorization (human RBAC × agent contract × governance token), most-restrictive-wins; RBAC roles; the authority×action permission matrix; enforcement points; data/memory/secret permissions.
- `docs/08_operations/incident_response.md` — Detect/triage/contain/eradicate/recover/learn; severity levels; containment decision (breaker vs kill switch at tightest scope); replay-based blast-radius; recovery patterns; blameless postmortems.
- `docs/08_operations/monitoring.md` — Golden signals + platform-specific SLIs (DLQ rate, breaker trips, decision latency, token mint failures, cross-tenant denials, spend velocity, HITL age, LLM cost); SLOs; severity-routed alerting; dashboards.
- `docs/08_operations/observability.md` — Four lenses (OTel traces by correlation_id, Langfuse LLM cost by governance_token_id, structured PII-safe logs, authoritative replay); tracing/logging detail; tenant scoping; vendor neutrality.

## Wave 5 — Vision / Product / Dev / Investor / Integrations

- `docs/01_vision/vision.md` — The AI-native Business OS vision; governed autonomy as the differentiator; long-horizon picture; non-goals.
- `docs/01_vision/mission.md` — Mission + six operating principles; how each principle is enforced in the architecture; success definition.
- `docs/01_vision/roadmap.md` — Strategic phases 0–5 (spine first, never regresses); sequencing principles; what does not move.
- `docs/11_product/mvp_definition.md` — Phase-1 scope in/out; MVP thesis; non-negotiable spine present at MVP; definition of done; success metrics.
- `docs/11_product/requierements.md` — Functional (FR-1..10) and non-functional (NFR-1..9) requirements traced to enforcing architecture + CI/SLI; inherited constraints; acceptance/traceability. (Filename misspelling preserved on disk.)
- `docs/11_product/user_personas.md` — Personas (Owner/Operator/Security-Reviewer/Analyst) mapped to RBAC roles and requirements; anti-personas (ungoverned autonomy).
- `docs/11_product/feature_roadmap.md` — Tactical feature table by phase, each row gated on its governing spine capability; prioritization rule.
- `docs/09_development/coding_standards.md` — Python/Pydantic/FastAPI conventions; enforced boundary rules (agents import only schemas; no vendor SDK in domain; no raw DB outside DAL; no secrets); canonical vocabulary; testing/contract gate; observability-in-code.
- `docs/09_development/deployment_strategy.md` — Index to consolidated `architecture/06`; environments; CI/CD with contract gate; MVP→Scale substitution; replay-based DR.
- `docs/10_investor_materials/yc_overview.md` — YC narrative: problem (governance gap), insight (safe autonomy wins), product, why-now, moats, model, status, ask — backed by the technical record.
- `docs/10_investor_materials/technical_due_diligence.md` — Diligence index: architecture, governance/safety, security posture, scalability/ops, anti-lock-in, SOC2 readiness, known issues — every claim linked to source-of-truth docs.
- `docs/06_integrations/anthropic.md` — Claude provider behind the LLM gateway; inward IF-TOOL / outward IF-INTEGRATION contracts; secrets; Langfuse cost; failover; provider-name-never-in-domain.
- `docs/06_integrations/openai.md` — OpenAI provider behind the gateway; structurally identical to anthropic by design (the point of the abstraction); failover/cost routing.
- `docs/06_integrations/shopify.md` — Store system-of-record; signed-webhook inbound → events; scoped adapter writes after decision.approved; Skylize mirrors, never owns.
- `docs/06_integrations/stripe.md` — Payment system-of-record; reference IDs only, never card data (PCI scope stays with Stripe); settlement → capital allocation; signed webhooks.
- `docs/06_integrations/meta_ads.md` — Spend-bearing ad adapter; performance inbound → events/scoring; campaign ops only after decision.approved within ceiling; first-launch/over-ceiling HITL.
- `docs/06_integrations/tiktok_ads.md` — Spend-bearing ad adapter on the Meta pattern; same governed-spend gate; channel reallocation under one capital policy.
- `docs/06_integrations/n8n.md` — External low-code execution surface; signed trigger/callback; n8n holds no Skylize creds and cannot bypass governance.
- `docs/06_integrations/mcp_servers.md` — MCP tools behind the tool proxy + token; allow-listed per tenant; untrusted output screened for prompt injection; cannot bypass governance.

## Wave 6 — Agent role specifications (154 agents)

- `docs/03_agents/00_organization_chart.md` — The org map: canonical authority hierarchy, the executive board, the department tree, the 14-section agent spec template, the manifest-flagged structural anomalies, and ownership/evolution.
- `scripts/agent_content.js` — Authored, role-specific content for every agent (mission, responsibilities, KPIs, I/O schemas, dependencies, events, memory namespaces, governance, failure notes), keyed by canonical agent_id. 152 entries covering all 154 files (the two `director_vendor_management` files share one role spec; `vc_procurement`→`vp_procurement` canonicalized).
- `scripts/gen_agent_specs.js` — Reproducible Node generator that renders the canonical 14-section spec into every `docs/03_agents/01_executive_board/**.md` from agent_content.js + manifest-derived authority_level/department/escalation_path. Idempotent; applies known-issue notes for flagged paths; defaults governance (tools/budgets/HITL/failure_mode) by authority level.
- `docs/03_agents/01_executive_board/**` — **154 agent role specifications generated.** Each contains all 14 sections (Mission, Responsibilities, Authority Scope, Escalation Rules, KPIs, Inputs, Outputs, Dependencies, Events Consumed, Events Produced, OPA Governance Requirements, Memory Requirements, Success Metrics, Failure Conditions). Verified: 0 empty/stray/generic-fallback files; all 154 escalation paths match `_generation_manifest.csv` exactly; vocabulary (authority levels, failure modes, event types, token validation order, memory namespaces) consistent with the spine. Stray-text files (task_router_agent, voiceover_agent, director_experimentation, compliance_monitor_agent, director_devops, the legal/security `director_compliance` pair) overwritten with full specs per manifest. Canonical agent_id used in-doc for typo paths (vc_procurement→vp_procurement) with a Known-Issue note; disk paths preserved.

## Completion

- **206 total `docs/**.md` files; 0 empty.** 154 agent specs + 35 supporting docs (this wave-set) + 4 spine + 6 architecture + 6 pre-existing + indexes. Every empty `.md` in the repository has been populated with production-grade, internally consistent, spine-conformant documentation.
