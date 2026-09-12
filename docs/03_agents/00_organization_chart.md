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
├─ cmo ── vp_marketing ── {director_brand, director_email_marketing, director_growth†,
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
`†` = REPORTS to `vp_marketing`, but its **department is `growth`**, not
`marketing` — see §4.1. Reporting line and department are different axes.

## 4.1 Departments (canonical)

**Eighteen** departments, not fifteen. A department is a real org department if a
governed `AgentContract` declares it, whether or not `01_executive_board/` has a
directory for it yet. `agency_ops`, `cowork` and `growth` are the three that have
contracts but no directory: they are departments, not channel-only slugs.

Two independent things are called "department" in this repo and they must not be
merged:

- The **contract** `department` field (`src/skylize/contracts/base.py:116`) — what the
  decision engine routes on. Authoritative.
- The **disk path** under `01_executive_board/` — what `deptOf()` in
  `scripts/gen_agent_specs.js` and `scripts/gen_agent_network_data.js` infers a
  department from, for the 154 manifest rows that have spec files.

Where they disagree, the contract wins and the generators carry an explicit
`DEPT_OVERRIDE` entry. There is one today: `director_growth`.

| Department | Roster agents | Directory under `01_executive_board/` | Contract-declared |
|---|---:|---|---|
| `executive_office` | 2 | (root) | yes |
| `finance` | 8 | `CFO/` | yes |
| `marketing` | 7 | `CMO/` | yes |
| `growth` | 1 | — (`director_growth` sits in `CMO/Marketing/`) | yes |
| `creative` | 42 | `CMO/.../Creative*` | yes |
| `operations` | 6 | `COO/` | no |
| `procurement` | 13 | `COO/Procurement/` | no |
| `product` | 5 | `CPO/` | no |
| `sales` | 5 | `CRO/Sales/` | yes |
| `customer_success` | 5 | `CRO/` | no |
| `engineering` | 7 | `CTO/` | no |
| `data` | 5 | `CTO/Data_and_AI/` | no |
| `security` | 17 | `CSO_Security/` | yes |
| `strategy` | 20 | `CSO/` | no |
| `people` | 4 | `CHRO/` | no |
| `legal` | 4 | `CLO/` | no |
| `agency_ops` | 0 | — | yes |
| `cowork` | 0 | — | yes |

"Roster agents" is the count the generated console data carries (151 total; see
§6 for why 154 manifest rows collapse to 151). A `0` means the department's
agents are **code-only** — they have `AgentContract`s in
`src/skylize/contracts/mvp/` but no spec file under `01_executive_board/` yet.

The engine's own department vocabulary is narrower again and is a separate,
deliberately-gated list: `ALLOWED_EVENT_TYPES_BY_DEPARTMENT`
(`src/skylize/decision_engine/constants.py:28-45`) admits only `creative`,
`growth` and `governance`. Adding a department there is a governance decision
under ADR-0005, not a consequence of adding one to this table.

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
- **`director_vendor_management`** has a spec file under both Operations and
  Procurement. It is **one agent**, not two: both files describe the same role
  from the two organizations it serves, and the generator collapses them to a
  single `agent_id` (`gen_agent_network_data.js` "duplicate id collapsed"
  warning). `scripts/agent_content.js` carries **152** entries for 154 files
  for the same reason (`../_BUILD_LOG.md:83`).
- **Duplicate CPO** files (`CPO/chief_product_officer.md`, `CPO/Product/cpo.md`) —
  canonical `cpo`; both cross-reference.
- **`director_compliance`** has a spec file under both CLO/Legal and
  CSO_Security. Same shape as `director_vendor_management` above: **one agent**
  under one `agent_id`, with a shared spec covering legal and security
  compliance. It is collapsed to a single node, not counted twice.
- **154 manifest rows → 151 roster agents.** Three rows do not become their
  own node: `chief_product_officer` (dropped as the duplicate CPO file),
  `director_vendor_management` and `director_compliance` (each collapsed to
  one `agent_id`). 154 − 3 = **151**, the count the generated console data
  reports. It is not 153: the two shared-spec roles are one agent each.
- **Depth contradictions**: several `director_*` sit under a `managers/` directory;
  the **manifest's** `authority_level` is authoritative over the path.
- **Workers under `managers/workers/`** (Procurement, CSO_Security): path quirk;
  `authority_level: worker` per manifest.

## 6.1 Code-only agents (open, not yet specified)

Eight registered `agent_id`s have an `AgentContract` in
`src/skylize/contracts/mvp/` but **no** spec file under `01_executive_board/`
and no manifest row. They are governed at runtime and invisible to this chart:

`agency_deliverable_drafter`, `agency_requirements_analyst`, `cfo_agent`,
`cowork_agent`, `infrastructure_executor`, `lead_qualifier_agent`,
`sdr_outreach_agent`, `seo_keyword_agent`.

Writing their specs is open work, deliberately deferred. Until then the CI
roster gate (`scripts/check_agent_network_data.py`) carries them in a named
allow-list, so a NEW un-specified agent still fails the build while these eight
do not.

**`cfo` and `cfo_agent` are the same role under two `agent_id`s.**
`src/skylize/contracts/mvp/finance.py:13` defines `cfo` ("Chief Financial
Officer — financial governance & capital allocation"); line 161 defines
`cfo_agent` ("Chief Financial Officer — financial oversight, spend
authorisation & departmental budget summaries"). Both are
`authority_level="executive"`, `department="finance"`. Only `cfo_agent` is in
`ALL_MVP_CONTRACTS` (`contracts/mvp/__init__.py:35`); the manifest and this
chart know only `cfo` (`_generation_manifest.csv:9`). Merging them is open work
and is NOT done here — recorded so the split is not mistaken for two roles.

## 7. Ownership & evolution

- **Owner:** `ceo` (org design) under the human owner; Chief Systems Architect
  (the spec template & governance binding).
- **Evolution:** a new agent = a new directory node + a spec from the template + a
  code-level `AgentContract` in the registry. `gen_manifest.js` re-derives the
  hierarchy; the CI contract gate enforces consistency. New departments are added
  as governed crews on the same spine, never as ungoverned shortcuts.
