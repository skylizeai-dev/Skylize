# Evidence audit — the 137 agents without a registered contract

Read-only audit, 2026-09-24, at commit `0241277`. Written to support one
pending owner decision: for the agents on the roster that have no registered
`AgentContract`, do we write real contracts, trim the roster, or mark them
explicitly as planned-but-not-designed?

No contract was written, drafted or sketched during this audit. Nothing in
`src/` was changed. Every figure below was produced by executing code against
the repo, not by reading prose; the commands are named so any of it can be
re-derived.

## The headline: the gap is 127, not 137

`MVP_REGISTRY` has 23 registered contracts, 15 of which are on the 152-agent
manifest roster. That leaves 137 roster agents with no registered contract.

But **10 of those 137 already have a finished `AgentContract` written in
Python.** They are not registered because nothing imports them:

- `contracts/mvp/safety.py:100` defines `ALL_SAFETY_CONTRACTS` — imported
  nowhere. Four agents: `chief_security_officer`, `director_ai_safety`,
  `llm_safety_agent`, `prompt_injection_agent`.
- `contracts/mvp/finance.py:189` defines `ALL_FINANCE_CONTRACTS` — also
  imported nowhere. `contracts/mvp/__init__.py:21` imports only `cfo_agent`
  from that module. Six agents: `cfo`, `vp_finance`,
  `director_capital_allocation`, `director_fpanda`, `director_risk`,
  `director_treasury`.

These ten are not a design question. Verified: all ten have `input_schema` /
`output_schema` paths that resolve, and constructing
`AgentRegistry(ALL_MVP_CONTRACTS + those ten)` succeeds with 33 contracts and
passes `validate_schemas()`. Registering them is a wiring change, not authorship.

Today they fail closed at runtime — `MVP_REGISTRY.resolve("chief_security_officer")`
raises `AgentNotRegistered` (`contracts/registry.py:91-93`). Whether that is
deliberate staging or an oversight is itself an owner question, and a more
urgent one than the 127: it means the CSO, the CFO and the whole LLM-safety
tier are undefined to the running system.

## Classification of the remaining 127

Every one of the 137 `.md` specs is **generated** by `scripts/gen_agent_specs.js`
from a fixed 14-section template. Authority scope, escalation rules,
`allowed_tools`, HITL triggers, failure mode and budgets are all derived from
authority level and directory position. The only per-agent substance comes from
a hand-authored table in `scripts/agent_content.js`, keyed by `agent_id`.

So file length means nothing — all 17 CSO_Security files are 2,732-3,232 bytes
regardless of content. Median `agent_content.js` entry size is also comparable
across departments (334-718 chars), and does not separate them.

What does separate them is the presence of fields that **cannot** be derived
from an agent's id and department: `inputs`, `outputs`, `consumes`, `produces`,
`deps`, `hitl`, `failureNote`, `memoryNote`, `tools`. An entry carrying two or
more of those reflects a decision someone made about that specific agent.

| department | total | contract already written | designed (≥2 signals) | thin |
|---|---:|---:|---:|---:|
| CMO | 37 | 0 | 8 | 29 |
| CSO | 20 | 0 | 1 | 19 |
| COO | 19 | 0 | 4 | 15 |
| CSO_Security | 17 | 4 | 6 | 7 |
| CTO | 12 | 0 | 2 | 10 |
| CRO | 10 | 0 | 1 | 9 |
| CFO | 8 | 6 | 0 | 2 |
| CPO | 6 | 0 | 2 | 4 |
| CHRO | 4 | 0 | 1 | 3 |
| CLO | 3 | 0 | 2 | 1 |
| MISC | 1 | 0 | 1 | 0 |
| **total** | **137** | **10** | **28** | **99** |

"Thin" is not an accusation of sloppiness. A thin entry is a real role
statement — `director_retention`, "Own retention and churn reduction" — that
simply contains nothing a contract could consume beyond what the org chart
already says.

## Design attention and contract coverage are inversely related

The expectation going in was that well-documented agents would cluster in the
departments that already have contracts. The opposite is true.

CMO holds 13 of the 15 registered roster contracts, yet only 8 of its 37
uncontracted agents clear the designed bar (21%). The highest densities are
CLO (2/3), CSO_Security (10/17 counting its four written contracts), and CFO
(6/8, almost entirely as written-but-unregistered Python).

The readable explanation: where the team wrote real contracts in code, the
markdown stayed thin, because the code was the artifact that mattered. Where
they did not, the markdown carried the thinking. **Documentation quality is a
proxy for where contracts are missing, not for where they are easy to add.**

## The real blocker is not documentation — it is tooling and schemas

This applies to all 127, designed and thin alike, and it is the single most
decision-relevant finding.

**Every schema path cited in the docs is fictional.** `agent_content.js` names
32 distinct `skylize.schemas.*` dotted paths. All 32 fail to resolve — verified
by importing each one. The modules referenced (`skylize.schemas.marketing`,
`.finance`, `.legal`, `.engineering`, `.data`, `.exec`, `.operations`) do not
exist; the real schema modules live under `skylize.schemas.agents.*`.
`AgentContract` requires both `input_schema` and `output_schema` to be
importable dotted paths, and CI enforces it through
`MVP_REGISTRY.validate_schemas()`. So writing a contract for any of the 127
means first writing a real Pydantic model pair for it. That is roughly 254 new
models, none of which can be lifted from these docs.

**No agent's tool list is usable.** The registry holds exactly 15 tools
(`memory.search`, `search.web`, `utility.current_datetime`, ten
`integration.*`, `stripe.refund`). Every spec's `allowed_tools` line comes from
`LEVEL_TOOLS` (`gen_agent_specs.js:162-168`) and names `llm.generate`,
`memory.search`, `bi.query`, `orchestrator.delegate`. Of those, only
`memory.search` is a registered tool; `llm.generate` and `orchestrator.delegate`
are capability names (`contracts/base.py` `CAPABILITY_NAMES`).

**`bi.query` is a fourth orphan**, alongside the known `crm.read` / `crm.write`.
It is neither a registered tool nor a capability name — it resolves to nothing
anywhere in the codebase, yet it is printed into the `allowed_tools` line of
**26 specs**: every executive, every VP, and `director_business_intelligence`
(whose stated job is to "serve `bi.query`"). Any contract written from these
specs must not carry it, and it should be cleaned up regardless of what is
decided here.

Beyond that, the work these agents describe has no tool behind it at all: image
generation, video editing, text-to-speech, social publishing, paid-ads writes
(CMO); Shopify catalog writes (`director_store_operations`); settlement reads
(`director_treasury` — only `stripe.refund` is registered, not a read);
authorized scanning (`penetration_testing_agent`).

So "designed" means the prose reflects genuine thinking. It does not mean a
contract is cheap to derive. For all 127, the machine-readable parts — schemas
and tools — would have to be built from scratch.

## Options

Presented, not recommended. The decision is the owner's.

**A. Register the ten now.** Import `ALL_SAFETY_CONTRACTS` and
`ALL_FINANCE_CONTRACTS` in `contracts/mvp/__init__.py`. Verified to work: 33
contracts, schemas resolve, `validate_schemas()` passes. This is independent of
everything else here and is the only option with a finished artifact already in
the repo. It should be settled on its own merits — the CSO and CFO being
unresolvable at runtime is a live governance gap, not a backlog item.

**B. Contract the 28 designed agents.** Each still needs a Pydantic model pair
written first, and most need tools that do not exist. Cost is dominated by
schema and tool work, not by writing contracts. Worth scoping per-agent rather
than as a batch.

**C. Mark the 99 thin agents as planned-not-designed.** The generator already
labels their budgets `budget_source = "level_default_unimplemented"`, so the
honest-labelling half is done. An explicit `lifecycle_status`-style marker in
the docs would stop them reading as governed capability.

**D. Trim.** Two agents are trim candidates on evidence rather than judgement:
`chief_product_officer` is a duplicate of `cpo` that the generator itself flags
as a known issue, and `director_mna` / `director_m_and_a` are a naming variant
pair. Beyond those, trimming is a product question this audit cannot answer.

These are not exclusive. A, C and D could all proceed without touching B.

## Known inconsistency found during the audit

`agent_content.js:294` still describes `vc_procurement` as "(Path
`vc_procurement`; canonical `vp_procurement`.)", and that sentence renders into
`vc_procurement.md`. Commit `2316aa6` established that `vc_procurement` is a
real id that is not renamed, and emptied `CANON_ID` in all three generators
accordingly. This is authored prose, not generated, so it survived that change.
Left as found — this audit is read-only.

## How to re-derive

```
# the ten unregistered contracts, and whether they would register
python -c "import sys;sys.path.insert(0,'src');\
from skylize.contracts.registry import AgentRegistry;\
from skylize.contracts.mvp import ALL_MVP_CONTRACTS;\
import skylize.contracts.mvp.safety as s, skylize.contracts.mvp.finance as f;\
r=AgentRegistry(ALL_MVP_CONTRACTS+s.ALL_SAFETY_CONTRACTS+f.ALL_FINANCE_CONTRACTS);\
r.validate_schemas();print(len(r.all()))"

# which cited schema paths resolve (expect: none)
grep -o 'skylize\.schemas\.[A-Za-z_.]*' scripts/agent_content.js | sort -u

# which specs carry the bi.query orphan
grep -rl 'bi\.query' docs/03_agents/01_executive_board --include='*.md' | wc -l
```
