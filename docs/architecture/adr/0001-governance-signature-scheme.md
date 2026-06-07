# ADR 0001 — Governance Token Signature Scheme: ECDSA P-384 (not Ed25519)

**Status:** Accepted
**Date:** 2026-06-01
**Deciders:** Principal Architect, Security Architect, human owner
**Supersedes:** the "Ed25519" wording in the foundation documents (see §Consequences)
**Related:** [05_security_architecture.md §5](../05_security_architecture.md#5-the-governance-token-chain-of-trust) · [agent_governance.md §4](../../03_agents/agent_governance.md#4-governance-token) · [agent_contract_registry.md §3](../../03_agents/agent_contract_registry.md#3-governance-token) · `src/skylize/contracts/token.py` · `src/skylize/security/ecc_service.py`

---

## Context

The foundation documents specified **Ed25519** as the governance-token signature
scheme — the root of trust that lets agent intent become real-world action. The
Sprint-1 implementation instead signs governance tokens with **ECDSA on curve
P-384 (SECP384R1)**, via the already-built, already-tested `ECCService`
(`security/ecc_service.py`), whose `sign_governance_token` /
`verify_governance_token` default to P-384.

The Sprint-1 audit flagged this as an architectural contradiction (finding C-1):
the docs (source of truth) and the code disagreed on the most security-critical
primitive, and `token.py` unilaterally "reinterpreted" the docs in a code comment
rather than the divergence being recorded as a decision. A reinterpretation
buried in a docstring is not a decision; this ADR makes it one.

## Decision

**Standardize the governance-token signature scheme on ECDSA P-384** and align
the documentation to the implementation.

Rationale for keeping P-384 rather than re-implementing Ed25519:

1. **Already implemented and tested.** `ECCService` provides P-384 ECDSA signing,
   verification, ECDH, and AEAD, with comprehensive unit coverage
   (`tests/unit/test_ecc_service.py`). Switching to Ed25519 would re-touch the
   crypto core and its token pipeline for no security gain.
2. **FIPS 140 posture.** ECDSA P-384 is on the FIPS 186-4 approved-algorithm
   list; Ed25519 (FIPS 186-5) has narrower hardware/HSM and FIPS-validated-module
   support today. For an enterprise / SOC2 trajectory, P-384 is the safer
   custody/attestation choice.
3. **Single curve across the platform.** The same `ECCService` curve underpins
   ECDH/ECIES for payload confidentiality; one curve family (P-384) keeps key
   custody, rotation, and HSM integration uniform.

The security properties the docs depend on are unchanged: asymmetric signatures
minted only by the Governance Authority, verified by the tool proxy / adapters,
over the canonical serialization of every token field except `signature`.

## Scope / invariants preserved

- **Curve is fixed at P-384.** `GOVERNANCE_CURVE = Curve.P384`
  (`contracts/token.py`); key loading rejects any other curve
  (`app/governance/keys.py::_assert_p384`).
- **Canonical signing bytes** (stable key order, no whitespace, UTC ISO-8601)
  are unchanged — the signed message is scheme-independent.
- **Validation order** (signature → expiry → revocation → scope → budget →
  delegation) is unchanged.
- **Key custody** requirements are unchanged: single root private key, secrets
  manager, restricted custody, stable & shared across replicas (see Task 3 /
  `keys.py`).

## Consequences

- All foundation docs are updated from "Ed25519" to "ECDSA P-384", each pointing
  back to this ADR. Affected files:
  `02_architecture/system_boundaries.md`, `02_architecture/service_map.md`,
  `03_agents/agent_governance.md`, `03_agents/agent_contract_registry.md`,
  `04_decision_engine/kill_switch_protocol.md`,
  `architecture/03_agent_runtime.md`, `architecture/05_security_architecture.md`,
  `10_investor_materials/technical_due_diligence.md`.
- The `BaseEvent`/token field comments ("Ed25519 over canonical serialization")
  become "ECDSA P-384 over canonical serialization".
- **No code change** results from this ADR — it ratifies the implemented state.
- If a future FIPS 186-5 / EdDSA requirement appears, it is a new ADR and a
  curve/scheme migration behind the same `TokenSigner` / `verify_token_signature`
  seam (the scheme is already isolated to `ECCService` + `token.py`).

## Alternatives considered

- **Re-implement Ed25519 to match the docs.** Rejected: re-touches the crypto
  core, weakens current FIPS posture, no security benefit, and discards tested code.
- **Leave docs and code divergent.** Rejected: the root-of-trust primitive must
  be unambiguous for security review and diligence.
