# ADR 0003 — n8n Admin BFF Governance Gap: Gated Default-Off Until a Governed Rewrite

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** Principal Integration/Platform Engineer, human owner
**Related:** [../../02_architecture/system_boundaries.md §4.6](../../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration) · [../../06_integrations/n8n_reality_map_2026-07-15.md §1, §4, §8](../../06_integrations/n8n_reality_map_2026-07-15.md) · [ADR-0001](./0001-governance-signature-scheme.md) · `website/src/app/api/console/workflows/route.ts` · `src/skylize/adapters/llm/content_gate.py` · `src/skylize/edge/routes/knowledge.py` · commits `3aa2bed3` (finding), `9565119d` (interim gate)

---

## Context

The `website/` BFF console ships a **live n8n admin endpoint**. `website/src/app/api/console/workflows/route.ts:66-106` is a server-side bridge to n8n's **admin** public REST API that a caller holding a valid `skylize_console` session cookie can use to:

- **create** a workflow — `POST {N8N_API_URL}/api/v1/workflows`,
- **activate** a workflow — `POST /api/v1/workflows/{id}/activate`, and
- **discard/delete** a workflow — `DELETE /api/v1/workflows/{id}`.

n8n workflows can contain arbitrary **Code** and **HTTP Request** nodes, so this is, in effect, an **arbitrary-code-execution / arbitrary-egress surface**. The only controls on the path are a session-cookie check and zod **shape** validation of the request body (`nodes`/`connections` are typed `z.unknown()`). There is **no `GovernanceToken`, no Decision Engine / OPA check, no org scoping, and no audit-log entry** anywhere on it, and the credential used (`N8N_API_KEY`, presented as `X-N8N-API-KEY`) is a standing n8n admin key.

This directly contradicts the platform's egress invariant in [system_boundaries.md §4.6](../../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration) (lines 208-216): outbound network egress is reached **"only by the infrastructure layer's Integration Adapters, never by agents,"** and n8n specifically is documented to hold **"no Skylize credentials"** (lines 224-228). The implemented BFF path is the **inverse** of that model — here Skylize holds an n8n admin credential and drives n8n's admin API directly, from the `website/` layer the integration docs never mention.

This is the **same category of gap** the governance model exists to prevent everywhere else: an execution/egress capability that reaches an external system **without passing the enforcement point** that governs every other side effect. The canonical enforcement patterns it bypasses are the **Decision Engine / OPA** authorization path (the sole governed emitter of terminal `decision.*` actions) and the composition-root gating used for the LLM surface in `src/skylize/adapters/llm/content_gate.py` (a `GuardedLLMGateway` wrapping the shared gateway so every caller is gated uniformly). The n8n admin path has no equivalent.

The finding was raised, read-only and DD-grade, in [n8n_reality_map_2026-07-15.md](../../06_integrations/n8n_reality_map_2026-07-15.md) §1/§4/§8 (commit `3aa2bed3`), classified **HIGH (latent)**: the endpoint has no console UI button driving it (dormant), but it is **live and reachable** by any authenticated session via a direct `POST` whenever `N8N_API_URL` / `N8N_API_KEY` are configured. Dormant ≠ absent.

## Decision

**1. Interim band-aid (already implemented — this ADR records it, it is not a proposal).**
The route is **gated default-OFF** behind `SKYLIZE_ENABLE_N8N_ADMIN` (commit `9565119d`). Unless that flag is exactly `"true"`, the handler short-circuits with **HTTP 501** *before* touching n8n credentials or the network. This shrinks the exposure from "any authenticated session" to "any authenticated session **in a build that explicitly opted in**," but it changes nothing about the path's governance: when enabled, it is still session-only and still ungoverned. **This is a band-aid, not the fix.**

**2. Hard gate (binding).**
`SKYLIZE_ENABLE_N8N_ADMIN=true` **MUST NOT** be set in any production environment until the governed rewrite in (3) has landed. This is a **hard gate, not a recommendation**: enabling the flag in production without the rewrite re-opens an ungoverned arbitrary-code-execution egress surface and is a launch-blocking violation of [system_boundaries.md §4.6](../../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration). The flag exists only to preserve the code and to permit the raw path in a **controlled, non-production** context where the ungoverned admin path is knowingly acceptable.

**3. Required follow-up (the actual fix).**
Before this path can **ever** be enabled in production, it MUST be rewritten to route through the **Decision Engine / OPA**, matching the governed Integration Adapter pattern the rest of the system uses — the same shape as the LLM gating in `src/skylize/adapters/llm/content_gate.py`. Concretely, every `create` / `activate` / `discard` call must carry:

- a **`GovernanceToken` check** (chain-of-trust validation per [ADR-0001](./0001-governance-signature-scheme.md)),
- **org scoping** (the action bound to and authorized for a specific `org_id`, not a standing admin key with unbounded reach), and
- an **audit-log entry** for the attempted and completed action,

so the n8n admin surface becomes a governed egress path rather than a raw credential-holding bridge. **Do not** graft a token check onto the BFF route as a façade — the enforcement must be the real Decision Engine/OPA path, not a look-alike. **Target: Scale-tier, and in all cases before any production enablement of `SKYLIZE_ENABLE_N8N_ADMIN`.**

## Scope / invariants preserved

- **Default-off is the invariant.** Absent an explicit `SKYLIZE_ENABLE_N8N_ADMIN=true`, the route returns `501` and never reaches n8n. Shipping with the flag unset is the safe default and the expected production posture until (3) lands.
- **No env change is made by this ADR.** This is a docs-only record; it does **not** set, template, or recommend a value for `SKYLIZE_ENABLE_N8N_ADMIN` (the var is documented, empty, in `.env.example` and `website/.env.local.example` by prior commits `e8759ff6` / `9565119d`).
- **Inbound n8n paths are unaffected and out of scope here.** `GET /api/v1/agent-prompts/{id}` (static-key) and `POST /api/v1/knowledge/ingest` (HMAC) keep their existing behavior; this ADR concerns only the **outbound BFF admin** path.
- **The code is preserved on purpose.** The rewrite in (3) replaces the governance model, not the n8n API plumbing; deleting the route now is explicitly *not* the decision (see Alternatives).

## Consequences

- The HIGH-severity finding is now **formally tracked** rather than living only in a dated audit snapshot, and the boundary rule it violates carries a pointer back here: a short note was added at [system_boundaries.md §4.6](../../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration) (the egress-must-be-governed paragraph) referencing this ADR, so anyone reading the invariant sees the known, tracked exception.
- The interim gate remains acceptable for non-production/controlled use, but the **hard gate** in Decision (2) now governs any move toward production enablement — reviewers have an explicit, citable blocker.
- **Related open item — do not fold into this ADR's scope.** The `knowledge/ingest` path has a smaller instance of the same *"ungoverned / under-audited integration surface"* pattern: the `governance.integration_bad_signature` audit event promised on a bad-signature rejection is **not emitted** (`src/skylize/edge/routes/knowledge.py:53-54`, a deferred `TODO` tracked by commit `bf2f5009`). It is **linked here as a related gap only** — it is a separate, lower-severity item (a missing audit event on an already-HMAC-gated path, not an ungoverned execution surface) and must be resolved on its own track, not merged into the n8n-admin rewrite.
- No dedicated ADR yet exists for the broader "execution capability bypassing the Decision Engine/OPA enforcement point" pattern as a class; this ADR documents the n8n-admin instance specifically. If that class is later captured in its own ADR, this one should be cross-linked to it.

## Alternatives considered

- **Delete the route entirely.** Rejected (for now): the create/activate/delete plumbing is the intended future capability once governed; deleting it discards working n8n API integration that the Scale-tier rewrite will re-govern rather than re-author. Default-off + a binding rewrite requirement preserves the code without carrying the live risk.
- **Graft a `GovernanceToken` check directly onto the BFF route.** Rejected: a token check bolted onto `route.ts` would be a look-alike, not the real enforcement point. The governed path must run through the Decision Engine/OPA (the actual authorizer), consistent with how `content_gate.py` gates the LLM surface at the composition root — a façade check in the BFF would give false assurance while the standing admin credential and unbounded workflow definition remain.
- **Leave it as-is (dormant, ungoverned, no flag).** Rejected: "dormant" is not a control — the endpoint was live and reachable by any authenticated session. The default-off flag is the minimum honest interim posture, and even that is explicitly marked a band-aid pending (3).
