"""GCP Workload Identity Federation support.

Skylize acts as a public OIDC ISSUER so a customer's Google Cloud workload
identity pool can federate a Skylize-signed assertion into a short-lived Google
credential. Design: docs/06_integrations/gcp_wif_killswitch_design.md.

FOUNDATION ONLY. This package contains the issuer surface (discovery + JWKS),
the signing key, the trust-state store, and the health probe. It deliberately
contains NO trigger path, NO HITL wiring, and NO Compute mutation verb: nothing
here can stop a virtual machine. The `stop` verb arrives in a later pass, behind
the Decision Engine gate.
"""
