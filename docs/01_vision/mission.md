# Mission

**Status:** Mission document (source of truth for purpose)
**Owner:** `ceo` · human owner
**Related:** [vision.md](./vision.md) · [roadmap.md](./roadmap.md) · [../11_product/mvp_definition.md](../11_product/mvp_definition.md)

---

## 1. Purpose

The vision ([vision.md](./vision.md)) is the destination; the **mission** is what
we do every day to get there, and the principles we refuse to violate on the way.

## 2. The mission

**Give every business a governed AI organization it can trust with real budget,
real brand, and real customers — and always stay in command of.**

Concretely, we build and operate the platform that lets a business owner delegate
operational outcomes to a hierarchy of autonomous agents that are **bounded by
contract, authorized by signed token, gated by policy, and reversible by a human**.

## 3. Operating principles

1. **Safe before clever.** We ship governance before we ship autonomy. A capable
   agent without governance does not ship.
2. **Accountable by construction.** If we can't audit and replay it, we don't do
   it. Every action is a typed, immutable event.
3. **Human in command.** The owner can stop anything, anytime, at any scope. The
   kill switch overrides all authority.
4. **Tenant trust is sacred.** Cross-tenant isolation is a zero-tolerance
   invariant; a single breach is a SEV1.
5. **Boring infrastructure, rigorous controls.** Proven, self-hostable, no
   lock-in; complexity goes into governance, not into the stack.
6. **Explainable decisions.** Every spend, launch, and decision names the policy,
   score, authority, and token behind it.

## 4. How the mission shows up in the architecture

| Mission principle | Where it is enforced |
|---|---|
| Safe before clever | [agent_governance.md](../03_agents/agent_governance.md), [guardrails.md](../04_decision_engine/guardrails.md) |
| Accountable by construction | [event_driven_architecture.md](../02_architecture/event_driven_architecture.md) (immutable audit, replay) |
| Human in command | [kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md) |
| Tenant trust | [../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth) |
| Boring infra, no lock-in | [../architecture/01_final_stack.md](../architecture/01_final_stack.md) |
| Explainable decisions | [decision_engine.md](../04_decision_engine/decision_engine.md), [scoring_models.md](../04_decision_engine/scoring_models.md) |

## 5. What success looks like

- A business owner trusts Skylize with a real budget and a real brand because they
  can see, bound, and stop everything it does.
- A security/enterprise reviewer can trace any action to who did it, under what
  authority, with what token, and why.
- The organization measurably improves the owner's business outcomes over time
  while every tenant's data stays its own.

## 6. Ownership & evolution

- **Owner:** `ceo` (agent) under the human owner.
- **Evolution:** the mission's *principles* are durable; the *commitments* that
  realize them are tracked in [roadmap.md](./roadmap.md) and
  [../11_product/feature_roadmap.md](../11_product/feature_roadmap.md).
