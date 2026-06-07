"""
Generate a governance signing key (ECDSA P-384) for production.

The Governance Authority requires a STABLE, SHARED signing key across all
replicas (Sprint-2 Task 3). Generate one here and inject the PEM via the
secrets manager as SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM.

    python scripts/gen_governance_key.py            # prints PKCS8 PEM to stdout
    python scripts/gen_governance_key.py --password # prompts for an encryption password

Do NOT commit the output. Store it in your secrets manager.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from skylize.security.ecc_service import Curve, ECCService


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a P-384 governance signing key (PEM).")
    parser.add_argument(
        "--password", action="store_true",
        help="encrypt the private key with a password (prompted)",
    )
    args = parser.parse_args()

    pair = ECCService.generate_key_pair(Curve.P384)
    password = getpass.getpass("Key password: ").encode() if args.password else None
    sys.stdout.write(pair.private_pem(password=password).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
