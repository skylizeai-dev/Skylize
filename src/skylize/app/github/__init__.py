"""GitHub App foundation: platform key custody, token minting, connection probe.

Scope of this package, per integration_inputs.md 2.4 (APPROVED 2026-09-05):

  keys.py    platform-level App private key custody (RSA, the platform's first)
  tokens.py  RS256 App JWT -> narrowed, short-lived installation access token
  probe.py   is the customer's branch protection actually protecting them?

DELIBERATELY ABSENT, and absent by owner decision rather than by omission:

  * NO branch-deletion capability, anywhere, ever. 2.4 Q2.4b.2 chose
    verb-surface minimalism over a runtime gate: Skylize never builds a
    delete-branch verb, so no gate is needed and none can be refactored away.
  * NO webhook ingress (2.4 Q2.4e) and NO PR-merge verb (2.4 Q2.4b.3) yet.
    Foundation only this pass.
  * NO tool registration. Nothing here is reachable by an agent yet.
"""
