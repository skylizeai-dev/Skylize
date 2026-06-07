# 05 — Security Architecture

**Status:** Production architecture (source of truth)
**Owner:** Principal Architect
**Related:** [02_system_architecture.md](./02_system_architecture.md) · [03_agent_runtime.md](./03_agent_runtime.md) · [system_boundaries.md](../02_architecture/system_boundaries.md) · [agent_governance.md](../03_agents/agent_governance.md) · existing [docs/07_security/](../07_security/)

---

## 1. Purpose

The end-to-end security model: identity, authorization, the agent trust model,
secrets, tenant isolation, the governance-token chain of trust, and incident
controls (circuit breaker, kill switch). This consolidates
[docs/07_security/](../07_security/) and the governance spine into a final
posture.

---

## 2. Threat model (what we defend against)

| Threat | Primary control |
|---|---|
| Stolen/forged user session | OIDC JWT verification at edge; short-lived `RequestContext` |
| Cross-tenant data access | RLS + namespace isolation at `IF-DATA` (independent of upstream) |
| Compromised / runaway agent | Sandbox + tool proxy + circuit breaker + kill switch |
| Agent exceeding authority | Governance token scope + Decision Engine authority check |
| Prompt injection / jailbreak | Dedicated security agents + `fail_closed` + HITL on high severity |
| Credential leakage | Secrets only in adapters; agents have none; no egress |
| Forged inter-component messages | ECDSA P-384-signed governance tokens; signed webhooks |
| Tampered audit trail | Append-only, object-locked audit/governance archive |
| Supply-chain / dependency risk | Pinned deps, SBOM, signed images, least-privilege CI |

---

## 3. Identity & authentication

- **Humans:** authenticate via an OIDC IdP (Clerk/Auth0/Keycloak — swappable,
  [01_final_stack.md §4.9](./01_final_stack.md#49-auth--oidc-idp-clerk-or-auth0)).
  The edge verifies the JWT against the IdP JWKS and derives a **short-lived,
  signed `RequestContext`** (`org_id`, `user_id`, roles; ≤5 min TTL). Internal
  services trust the `RequestContext`, never the raw IdP token.
- **Agents:** have no human identity. Their right to act is the **Governance
  Token** (§5), not a session.
- **External systems:** authenticate to us via **signed webhooks** (HMAC /
  provider signatures) verified at the edge; we authenticate to them via
  **adapter-held credentials** at `IF-INTEGRATION`.

---

## 4. Authorization model

Three independent layers; the **most restrictive wins** (per
[agent_governance.md §5](../03_agents/agent_governance.md#5-agent-capability-model)):

1. **Human RBAC** — what the user/tenant may request (checked at edge/service).
2. **Agent contract** — static capability (`allowed_tools`, memory scopes,
   budgets, `authority_level`).
3. **Governance token** — per-run narrowing of the contract + live governance
   state (suspension, kill switch).

Authority levels are the canonical set `executive / vp / director / manager /
worker`. The Decision Engine checks an agent's `authority_level` against the
action's required level; insufficient → escalate via `escalation_path`.

---

## 5. The governance-token chain of trust

The Governance Token is the cryptographic root of every side effect. Its
definition is identical across
[agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token) and
[agent_contract_registry.md §3](../03_agents/agent_contract_registry.md#3-governance-token).
The signature scheme is **ECDSA P-384** ([ADR 0001](./adr/0001-governance-signature-scheme.md)).

```
Governance Authority (holds ECDSA P-384 root key, Application Boundary)
   │  mints short-lived, run-scoped tokens (governance.token_issued)
   ▼
GovernanceToken { token_id, agent_id, authority_level, department,
                  delegation_chain, scope, max_token_budget,
                  max_execution_time_seconds, issued_at, expires_at,
                  nonce, signature(ECDSA P-384) }
   │
   ▼ injected across IF-AGENT
Tool Proxy / Adapters validate BEFORE any side effect:
   signature → expiry → revocation/live-state → scope → budget → delegation
```

- **Minting** is centralized in the Governance Authority; no other component can
  issue a token.
- **Validation** is decentralized (every tool proxy and adapter), so a forged or
  expired token fails everywhere.
- **Delegation** is provable: `delegation_chain` records the authority path; a
  parent can only delegate a subset of what it holds.
- **Revocation** is immediate: circuit breaker / kill switch add `token_id` to
  the revocation set and the agent's live state flips to suspended/killed.

No valid token ⇒ no side effect, ever.

---

## 6. Agent trust model (zero-trust runtime)

Agents are treated as **untrusted code**:
- sandboxed with **no egress, no credentials, no DB driver**
  ([03_agent_runtime.md §4](./03_agent_runtime.md#4-the-agent-sandbox-if-agent));
- every tool call validated by the proxy ([03 §5](./03_agent_runtime.md#5-tool-proxy-if-tool));
- every memory access scoped by contract + `org_id`;
- every action audited.

The dedicated security department (CSO_Security org) provides active defense:
`prompt_injection_agent`, `llm_safety_agent`, `fraud_detection_agent`,
`security_audit_agent`, etc. These run `fail_closed` and can raise
`SECURITY_SEVERITY_HIGH` HITL triggers.

---

## 7. Secrets management

- Secrets (DB creds, provider API keys, signing keys, webhook secrets) live in a
  **KMS-backed secrets manager / Vault**
  ([01_final_stack.md §4.12](./01_final_stack.md#412-secrets-iac-cicd--vaultkms--terraform--github-actions)).
- **Only adapters** at `IF-INTEGRATION` and the DAL read secrets. Agents and the
  agent runtime never see them.
- The **ECDSA P-384 signing key** is the highest-value secret; custody is
  restricted to the Governance Authority, rotated on schedule, with audited access.
- No secret is ever in code, env files committed to VCS, logs, events, or memory.

---

## 8. Tenant isolation (defense in depth)

| Layer | Control |
|---|---|
| Edge | `RequestContext.org_id` derived from verified JWT |
| Event bus | per-tenant streams `evt:{org_id}:{department}` |
| Postgres | row-level security keyed on `org_id`, enforced via a **non-superuser app role** |
| Qdrant | payload filter `org_id` (+ shard at Scale) |
| S3 | prefix `s3://skylize/{org_id}/...` |
| Memory service | `org_id` required on every call |

Isolation at `IF-DATA` holds **even if an upstream check is bypassed** — the data
layer never returns another tenant's rows. Cross-tenant attempts are denied and
audited (`audit.access_denied`) and fed to fraud detection.

> **Role model (required for RLS to mean anything).** Postgres `FORCE ROW LEVEL
> SECURITY` subjects the table *owner* to RLS, but a **`SUPERUSER` or `BYPASSRLS`
> role bypasses RLS unconditionally**. Therefore the application runtime MUST
> connect as a dedicated **`skylize_app`** role created `NOSUPERUSER NOBYPASSRLS`
> (migration `0003`), connected via `SKYLIZE_DB_APP_URL`. The superuser DSN
> (`SKYLIZE_DB_URL`) is used **only** for migrations and extension setup. Wiring
> the runtime as the superuser — as the Sprint-1 deployment did — silently
> disables tenant isolation; the integration suite asserts the runtime role is
> neither superuser nor `BYPASSRLS`.

---

## 9. Incident controls

- **Circuit breaker** (automatic): trips on repeated scope violations, runaway
  time/budget, output-validation failures, or a security-agent flag → revokes
  tokens, suspends the agent, emits `governance.circuit_breaker_tripped`
  ([agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules)).
- **Kill switch** (human/CSO override): scopes agent → department → tenant →
  platform; revokes all tokens in scope, blocks new mints, quarantines in-flight
  events, emits `governance.kill_switch_engaged`. Overrides **all** authority,
  including executive
  ([agent_governance.md §8](../03_agents/agent_governance.md#8-kill-switch-protocol),
  [kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md)).
- **Human-in-the-loop**: high-severity security verdicts, first external launches,
  and over-ceiling spend pause for human approval.

---

## 10. Audit & compliance

- Every agent action records an immutable `AuditEvent` with `correlation_id`,
  `governance_token_id`, authority, inputs/outputs hashes, and result
  ([agent_governance.md §10](../03_agents/agent_governance.md#10-audit-log-requirements)).
- Audit and governance events are object-locked for the 7-year compliance floor
  and are **replayable** to reconstruct exactly who did what, under which token,
  and why
  ([event_driven_architecture.md §10-11](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance)).

---

## 11. Supply chain & platform hardening

- Pinned dependencies + lockfiles; automated CVE scanning; SBOM per build.
- Container images signed; minimal base images; non-root; read-only FS where
  possible.
- Least-privilege CI (GitHub Actions) with OIDC to cloud, no long-lived cloud
  keys.
- Terraform-managed, reviewed infra changes; network policies deny-by-default
  between layers (only the Application Boundary reaches Data/Event/Agent; only
  adapters reach the internet).

---

## 12. Security invariants

1. No side effect without a valid, in-scope, unexpired, unrevoked governance
   token.
2. Agents hold no secrets and have no network egress.
3. Tenant isolation is enforced at the data layer regardless of upstream checks.
4. Only the Governance Authority mints tokens; only it holds the ECDSA P-384 key.
5. Kill-switch / suspension state overrides all authority.
6. Audit and governance records are immutable and replayable.
