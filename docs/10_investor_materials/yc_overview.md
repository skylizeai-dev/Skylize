# YC Overview

**Status:** Investor material (source of truth for the YC narrative)
**Owner:** `ceo` · human owner
**Related:** [technical_due_diligence.md](./technical_due_diligence.md) · [../01_vision/vision.md](../01_vision/vision.md) · [../11_product/mvp_definition.md](../11_product/mvp_definition.md)

---

## 1. Purpose

A concise, honest overview for YC-style diligence: what Skylize is, the problem,
the insight, the product, why now, and why this team/architecture can win. It is
backed end-to-end by the technical record in
[technical_due_diligence.md](./technical_due_diligence.md) — claims here are
verifiable there.

## 2. One-liner

**Skylize is an AI-native Business Operating System: a governed organization of
autonomous agents that runs a company's operations under human authority, with
the accountability of a real company.**

## 3. The problem

AI can produce the work (creative, copy, analysis, proposals), but businesses
**cannot hand real budgets and brand to ungoverned agents**. The blocker to
autonomy isn't model capability — it's **governance, accountability, and
control**. Today's "autonomous agent" tools are demos, not systems a CFO would
let near an ad account.

## 4. The insight

The winning platform is not the cleverest single agent — it's the one that makes
autonomy **safe, accountable, and overridable**. So Skylize is built as a real
**organization**: an authority hierarchy, signed governance tokens for every side
effect, policy guardrails, an immutable audit log, conflict resolution, and a
human kill switch that overrides everything. Governance is the product.

## 5. The product

A business owner connects their store + ad accounts, sets budget ceilings and
approval rules, and a governed Creative + Growth team produces creative, proposes
campaigns, and — with human approval on first/over-ceiling actions — launches and
optimizes. Every action is explainable and reversible
([../11_product/mvp_definition.md](../11_product/mvp_definition.md)).

## 6. Why now

- Models are finally capable enough for multi-step operational work.
- Durable, inspectable orchestration (LangGraph) makes governed agent control flow
  practical.
- The market is full of ungoverned demos and empty of **trustworthy** systems —
  the gap Skylize fills.

## 7. Why we win

| Moat | Why it's defensible |
|---|---|
| **Governance architecture** | safe-autonomy is hard to retrofit; it's our spine from day one |
| **Organizational memory** | per-tenant compounding outcomes; the system gets better at *your* business |
| **Trust = distribution** | enterprises/owners adopt what they can audit and stop; we're built for that review |
| **No lock-in, self-hostable** | removes the biggest enterprise objection |

## 8. Business model (sketch)

Multi-tenant SaaS: subscription + usage (governed LLM/automation consumption),
with enterprise tiers for dedicated isolation and compliance. Margins protected by
per-agent budget ceilings and provider abstraction (cost routing).

## 9. Traction & status

- **Backend spine built:** the governance/audit/boundary architecture — signed
  tokens, policy guardrails, immutable audit log, tenant isolation — is
  implemented in the backend
  (see [technical_due_diligence.md](./technical_due_diligence.md)).
- **Console UI built, not yet wired:** the operator-facing `/console/*`
  surface (chat, dashboard, agent network, etc.) is fully designed and
  implemented against the target visual system, but currently runs on mock
  data and is not yet connected to the live backend
  (status: [`docs/audits/console_state_audit.md`](../audits/console_state_audit.md)).
- **User-auth & credential vault: not started.** The login/session layer that
  will make the console's auth guard functional, and the vault for storing
  org integration credentials, are scoped but not yet built — the routers
  exist but depend on a persistence, composition-root, and config layer that
  isn't in place yet
  (status: [`docs/audits/epic_user_auth_buildout.md`](../audits/epic_user_auth_buildout.md)).
- **MVP target:** governed creative + growth team, multi-tenant, audited
  ([../11_product/mvp_definition.md](../11_product/mvp_definition.md)) —
  the backend capability for this exists; the remaining work to reach an
  end-to-end demoable product is the console-to-backend wiring and user-auth
  buildout above.
- *(Metrics to be populated as the MVP ships to design partners.)*

## 10. The ask & use of funds

*(Populated per round.)* Funds accelerate department breadth (Phase 2),
organizational-memory depth (Phase 3), and enterprise/SOC2 readiness (Phase 4),
each on the existing spine.

## 11. Ownership & evolution

- **Owner:** `ceo` under the human owner.
- **Evolution:** this overview is kept honest against the technical record and the
  product status; every claim must be defensible in diligence.
