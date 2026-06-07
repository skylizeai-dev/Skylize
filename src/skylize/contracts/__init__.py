"""
Agent contracts and the governance token.

`base.py` defines the `AgentContract` (the static, auditable definition of one
agent) and the `GovernanceToken` (the signed proof of an agent's right to act).
`token.py` implements the ECDSA P-384 signing/validation foundation. `registry.py`
is the loader the Orchestrator resolves contracts through.

Business logic (the live revocation set, circuit breaker, kill-switch state) is
NOT here — those are injected at runtime via the `LiveStateChecker` protocol in
`token.py` and implemented by the Governance Authority in a later sprint.
"""
