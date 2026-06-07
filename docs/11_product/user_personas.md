# User Personas

**Status:** Product specification (source of truth for who we serve)
**Owner:** `director_user_research` · `cpo`
**Related:** [mvp_definition.md](./mvp_definition.md) · [requierements.md](./requierements.md) · [../07_security/permissions.md §3](../07_security/permissions.md#3-layer-1--human-rbac)

---

## 1. Purpose

This document defines **who uses Skylize and what they need**, so product
decisions and the RBAC roles ([../07_security/permissions.md §3](../07_security/permissions.md#3-layer-1--human-rbac))
are grounded in real users. Personas map directly to human roles in the
permission model — they are not marketing fiction.

## 2. Primary personas

### P1 — The Owner / Founder ("Maya")
- **Context:** runs a growing DTC brand; wears every hat; can't afford a full ops
  team.
- **Wants:** outcomes (profitable growth) without managing tasks; to set limits
  and trust the system within them.
- **Fears:** an agent burning budget, going off-brand, or leaking data.
- **Needs from Skylize:** budget ceilings, approval rules, a visible kill switch,
  and a clear audit of everything done on her behalf.
- **RBAC role:** `owner`.

### P2 — The Operator / Marketing Lead ("Devon")
- **Context:** runs day-to-day growth; reviews and approves the system's work.
- **Wants:** high-quality creative and sound campaign proposals; fast approvals;
  to intervene when needed.
- **Fears:** rubber-stamping bad decisions; losing track of what's running.
- **Needs:** a clear HITL queue, explainable proposals (score + policy + why),
  easy modify/reject.
- **RBAC role:** `operator` (or `admin`).

### P3 — The Security / Compliance Reviewer ("Priya")
- **Context:** enterprise buyer's security team, or the tenant's compliance owner.
- **Wants:** proof of isolation, auditability, access control, and incident
  controls before trusting the platform.
- **Fears:** cross-tenant leakage, ungoverned agent actions, untraceable
  decisions.
- **Needs:** the security model ([../architecture/05_security_architecture.md](../architecture/05_security_architecture.md)),
  audit/replay, and the permissions model — answerable end to end.
- **RBAC role:** `admin`/`analyst` (read audit), often external reviewer.

### P4 — The Analyst ("Sam")
- **Context:** wants to understand performance and decisions.
- **Wants:** dashboards, decision history, spend vs. ceilings, ROAS trends.
- **Needs:** read access to per-tenant dashboards and organizational memory.
- **RBAC role:** `analyst` / `viewer`.

## 3. Persona → requirement mapping

| Persona | Key requirements |
|---|---|
| Owner (P1) | FR-4 ceilings, FR-6 kill switch, FR-8 audit, NFR-1 isolation |
| Operator (P2) | FR-5 HITL, FR-3 governed actions, explainable decisions |
| Security reviewer (P3) | NFR-1/2/5/8 isolation, audit, security, compliance-ready |
| Analyst (P4) | FR-9 dashboards, FR-10 scoped memory recall |

(Requirement IDs from [requierements.md](./requierements.md).)

## 4. Anti-personas (not the target, by design)

- A user who wants **ungoverned** autonomy ("just let it spend freely") — Skylize
  deliberately refuses this; governance is the product.
- A user who wants Skylize to be the **system of record** for data an external
  system owns — Skylize holds scoped mirrors, not the source of truth.

## 5. Ownership & evolution

- **Owner:** `director_user_research` (persona research), `cpo` (prioritization).
- **Evolution:** as departments expand (Phase 2+), new operator sub-personas
  appear (e.g. a procurement approver); each maps to an RBAC role and a set of
  HITL approvals. The owner-in-command persona is central in every phase.
