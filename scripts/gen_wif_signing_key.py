"""
Generate the GCP WIF issuer signing key (ECDSA P-256 / ES256) for production.

This is the DELIBERATE TWIN of scripts/gen_governance_key.py, for a DIFFERENT key
serving a DIFFERENT trust domain. Read that distinction before using either:

  * The GOVERNANCE key (P-384) signs an internal authority artifact validated
    inside the platform. It is never published.
  * THIS key (P-256) has its PUBLIC half published on an internet-facing JWKS,
    where it tells Google's Security Token Service which assertions to trust on
    behalf of a customer, against that customer's production infrastructure.

The two must never be the same key material, and cannot be even if someone tried:
Google accepts only RS256 or ES256 (verified 2026-09-04), and the governance
curve signs as ES384. `app/gcp/keys.py` asserts P-256 at boot and refuses a P-384
PEM with a message naming this mistake, because pasting the governance key into
SKYLIZE_WIF_SIGNING_KEY_PEM is the specific error that would cross the domains.

The key must be STABLE and SHARED across replicas. It is published to customers'
Google Cloud workload identity pool providers; a per-pod key would make
federation fail on every replica that did not mint the assertion, intermittently
and unreproducibly.

    python scripts/gen_wif_signing_key.py            # prints PKCS8 PEM to stdout
    python scripts/gen_wif_signing_key.py --password # prompts for an encryption password

Inject the PEM as SKYLIZE_WIF_SIGNING_KEY_PEM from your secrets manager, exactly
as the governance key is injected. Do NOT commit the output.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from skylize.app.gcp.keys import WIF_CURVE
from skylize.security.ecc_service import ECCService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a P-256 WIF issuer signing key (PEM)."
    )
    parser.add_argument(
        "--password", action="store_true",
        help="encrypt the private key with a password (prompted)",
    )
    args = parser.parse_args()

    pair = ECCService.generate_key_pair(WIF_CURVE)
    password = getpass.getpass("Key password: ").encode() if args.password else None
    sys.stdout.write(pair.private_pem(password=password).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
