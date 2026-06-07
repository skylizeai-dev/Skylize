# Organization Chart

**Status:** Foundation document (source of truth for the org structure)
**Owner:** `ceo` · Chief Systems Architect · human owner
**Related:** [agent_governance.md](./agent_governance.md) · [agent_contract_registry.md](./agent_contract_registry.md) · [_generation_manifest.csv](./_generation_manifest.csv) · [../02_architecture/repository_structure.md §5](../02_architecture/repository_structure.md#5-the-docs03_agents-mirror)

---

## 1. Purpose

This document is the **map of the agent organization**: every agent, its
authority level, its parent, and its escalation path. It is the human-readable
companion to the machine-generated [_generation_manifest.csv](./_generation_manifest.csv),
which is the authoritative source for `authority_level`, `parent_agent_id`, and
`escalation_path` (derived by `scripts/gen_manifest.js` from the directory tree).

Every agent file under `01_executive_board/` is a **role specification** (not a
prompt) following the standard template in §5.

## 2. Authority hierarchy (canonical)

Five levels, identical to [agent_governance.md §2](./agent_governance.md#2-authority-hierarchy):

```
human_owner  (ultimate authority; kill switch; final HITL)
   └─ executive   (ceo, cfo, cmo, coo, cto, cso, chief_*_officer, chief_data_officer)
        └─ vp      (vp_creative, vp_marketing, vp_engineering, vp_sales, …)
             └─ director  (copy_director, director_growth, director_cybersecurity, …)
                  └─ manager  (creative_operations_manager, manager_incident_response, …)
                       └─ worker  (hook_generator_agent, fraud_detection_agent, …)
```

Authority flows **down** as delegation, **up** as escalation. The
`escalation_path` of every agent walks this tree to `human_owner`.

## 3. The executive board

| Executive | Domain | Reports to |
|---|---|---|
| `ceo` | company-wide strategy & arbitration | human_owner |
| `chief_ai_advisor` | AI strategy & safety counsel | human_owner |
| `cfo` | finance | human_owner |
| `cmo` | marketing & creative | human_owner |
| `coo` | operations & procurement | human_owner |
| `cpo` / `chief_product_officer` | product *(duplicate role files; canonical `cpo`)* | human_owner |
| `cro` | revenue (sales + customer success) | human_owner |
| `cso` | strategy & special projects | human_owner |
| `chief_security_officer` (CSO_Security) | security & safety | human_owner |
| `cto` | technology & engineering | human_owner |
| `chief_data_officer` (under CTO/Data_and_AI) | data & AI | human_owner |

## 4. Department tree (summary)

```
ceo
├─ cfo ── vp_finance ── {director_capital_allocation, director_fpanda, director_risk, director_treasury}
│                          └─ {manager_budgeting, manager_profitability}
├─ chro ── {director_performance, director_talent, director_training}
├─ chief_legal_officer ── {director_contracts, director_privacy, director_compliance*}
├─ cmo ── vp_marketing ── {director_brand, director_email_marketing, director_growth,
│   │                        director_performance_marketing, director_seo}
│   └─ Social_Media ── director_social_media
│        └─ vp_creative ── {copy_team, art_team, video_team, brand_team,
│                            creative_team, creative_operations} (directors + workers)
├─ coo ── vp_operations ── {director_logistics, director_store_operations,
│   │                         director_supply_chain, director_vendor_management}
│   └─ vc_procurement(→vp_procurement) ── {director_sourcing, director_contract_procurement,
│                                            director_vendor_management}
│        └─ {manager_procurement_operations, manager_vendor_relations} ── workers
├─ cpo ── vp_product ── {director_product_strategy, director_user_research, director_experimentation*}
├─ cro ── {vp_sales ── (Sales directors), vp_customer_success ── (CS directors)}
├─ cso ── {vp_strategy ── (Strategy directors), vp_special_projects ── (Special Projects directors+workers)}
├─ chief_security_officer ── {director_ai_safety, director_cybersecurity,
│        director_identity_access, director_compliance*} ── {managers} ── security workers
└─ cto ── {vp_engineering ── (Engineering directors), chief_data_officer ── (Data_and_AI directors)}
```

`*` = manifest-flagged anomaly (see §6).

## 5. Agent spec template (every agent file)

Every `01_executive_board/...` file contains these 14 sections — organizational
operating specifications, **not** roleplay/personality:

1. **Mission** — the single outcome this agent exists to produce.
2. **Responsibilities** — concrete duties.
3. **Authority Scope** — `authority_level` + what it may/may not decide
   ([agent_governance.md §3](./agent_governance.md#3-authority--escalation)).
4. **Escalation Rules** — its `escalation_path` (from the manifest).
5. **KPIs** — how its performance is measured.
6. **Inputs** — `input_schema` (what it consumes as work).
7. **Outputs** — `output_schema` (what it produces).
8. **Dependencies** — agents/services it relies on.
9. **Events Consumed** — bus event types ([event taxonomy](../02_architecture/event_driven_architecture.md#5-event-taxonomy)).
10. **Events Produced** — bus event types.
11. **OPA Governance Requirements** — `allowed_tools`, token scope, `human_in_loop_triggers`.
12. **Memory Requirements** — `memory_read_access` / `memory_write_access` namespaces.
13. **Success Metrics** — what "working well" means.
14. **Failure Conditions** — `failure_mode` + what counts as failure.

The five worked example contracts (`ceo`, `vp_creative`, `copy_director`,
`hook_generator_agent`, `fraud_detection_agent`) live in
[agent_contract_registry.md §4](./agent_contract_registry.md#4-example-contracts-5-representative-agents).

## 6. Known structural anomalies (from the manifest)

Paths are **preserved on disk**; specs use the canonical `agent_id` and note the
issue (per [../02_architecture/repository_structure.md §5](../02_architecture/repository_structure.md#5-the-docs03_agents-mirror)):

- **`vc_procurement`** → canonical `agent_id: vp_procurement` (`vc` typo).
- **`creative_operations_departmant/`** → "department" misspelling in path.
- **`director_vendor_management`** appears under both Operations and Procurement —
  two distinct agents, disambiguated in their specs.
- **Duplicate CPO** files (`CPO/chief_product_officer.md`, `CPO/Product/cpo.md`) —
  canonical `cpo`; both cross-reference.
- **Duplicate `director_compliance`** (CLO/Legal and CSO_Security) — two distinct
  agents (legal compliance vs. security compliance).
- **Depth contradictions**: several `director_*` sit under a `managers/` directory;
  the **manifest's** `authority_level` is authoritative over the path.
- **Workers under `managers/workers/`** (Procurement, CSO_Security): path quirk;
  `authority_level: worker` per manifest.

## 7. Ownership & evolution

- **Owner:** `ceo` (org design) under the human owner; Chief Systems Architect
  (the spec template & governance binding).
- **Evolution:** a new agent = a new directory node + a spec from the template + a
  code-level `AgentContract` in the registry. `gen_manifest.js` re-derives the
  hierarchy; the CI contract gate enforces consistency. New departments are added
  as governed crews on the same spine, never as ungoverned shortcuts.
