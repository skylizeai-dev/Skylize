"""GitHub App private key: loading and validation. PLATFORM-level custody.

This is the THIRD key in this platform and the FIRST RSA one. The other two are
`app/governance/keys.py` (P-384, ES384, signs internal authority artifacts) and
`app/gcp/keys.py` (P-256, ES256, signs the OIDC assertions Google's STS accepts).
This module follows their custody discipline exactly — same resolution order, same
fail-closed posture, same ephemeral-only-on-memory-backend escape hatch — for a
different key serving a different trust domain.

WHY RSA, WHICH THIS CODEBASE OTHERWISE AVOIDS
---------------------------------------------
`app/gcp/keys.py:36-40` records a deliberate decision to prefer ES256 precisely
because "RS256 would make this the platform's first RSA key, with all of that
built from scratch, for no security or compatibility gain." That reasoning was
correct there because Google accepts either algorithm. It does not apply here,
because GitHub accepts only one:

    "Your JWT must be signed using the `RS256` algorithm"
    `[LIVE-VERIFIED]` 2026-09-05, docs.github.com/en/apps/creating-github-apps/
    authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app

So RSA is forced by the provider, not chosen. That is why this module does NOT go
through `ECCService` (which is P-256/P-384 only and has no RSA path) and instead
loads the key with `cryptography`'s PEM loader directly. A future reader must not
"unify" this with either EC loader: the key types are not interchangeable and
neither of the other two keys is a legal GitHub App key.

WHY THE KEY IS PLATFORM-LEVEL AND NOT PER-TENANT — the load-bearing fact
------------------------------------------------------------------------
`[LIVE-VERIFIED]` 2026-09-05, docs.github.com/en/apps/creating-github-apps/
authenticating-with-a-github-app/managing-private-keys-for-github-apps: private
keys are managed at the APP level, an App may hold up to 25 at once, and rotation
is generate-new -> switch -> delete-old. One key serves EVERY customer's
installation.

This is the single fact that makes GitHub a third credential shape rather than a
copy of WIF. Under WIF each customer configures their own trust relationship, so
`gcp_wif_connections` is per-tenant state. Here the trust is between Skylize and
GitHub, established once; what is per-tenant is only "which installation id is
this org's" — a non-secret. Hence migration 0026 has no encrypted column at all
and this key lives in the secrets manager, reached through `Settings`, exactly
like the Slack bot token (`bootstrap.py::resolve_slack_notifier_config`).

A consequence worth stating because it is easy to get wrong later: rotating this
key is a PLATFORM operation affecting all tenants at once, and — unlike WIF's
`jwks_delivery='uploaded'` case — it requires nothing from any customer. GitHub
holds the public half; Skylize just starts signing with a new key it has already
registered. There is no per-tenant rotation anchor to record, which is why 0026
has no `key_id`.

SEPARATION FROM THE OTHER TWO KEYS IS STRUCTURAL, NOT CONVENTIONAL
------------------------------------------------------------------
There is no import edge between this module and either `app.governance.keys` or
`app.gcp.keys`, in any direction. The three loaders read different `Settings`
fields, assert different key types, and return different dataclasses, so a caller
holding a `GithubAppKey` cannot reach the governance key or the WIF key through
it. The type assertion below is what enforces it at runtime: an EC PEM handed to
this loader is refused rather than silently accepted and then failing at GitHub
with an opaque `401`.

WHY A MINIMUM MODULUS SIZE IS ASSERTED
--------------------------------------
GitHub generates 2048-bit keys, but this loader takes a PEM from configuration and
an operator could paste anything. A 1024-bit RSA key would still sign a
syntactically valid JWT and would still be accepted by some verifiers; refusing it
here, at boot, with a message naming the variable, is cheaper than discovering it
in a security review. 2048 is the floor, matching GitHub's own generation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ...config import Settings

log = logging.getLogger(__name__)

#: GitHub generates 2048-bit App keys. Anything smaller is a configuration
#: mistake, not a deployment choice, and is refused at boot.
MIN_RSA_MODULUS_BITS = 2048


class GithubKeyError(RuntimeError):
    """The configured GitHub App key is unusable. Raised at composition time."""


@dataclass(frozen=True, slots=True)
class GithubAppKey:
    """The platform's GitHub App identity: one RSA private key plus the app id.

    `app_id` is carried alongside the key rather than looked up separately
    because it is the JWT's `iss` claim — a key without its app id cannot mint a
    usable assertion, so the two are useless apart and are resolved together.

    Deliberately NOT a per-tenant type. There is exactly one of these per
    deployment; anything per-tenant lives in `github_app_installations`.
    """

    private_key: RSAPrivateKey
    #: GitHub's App ID (or client id) — the `iss` claim. String, not int: GitHub
    #: documents client ids as opaque strings and accepts either form, and
    #: parsing a value we only ever re-serialise would add a failure mode for no
    #: gain.
    app_id: str

    def private_pem(self) -> bytes:
        """PKCS#8 PEM of the private half. For tests and key-identity checks only.

        Deliberately a method rather than a stored field so the serialised secret
        is not sitting in memory on every instance, and deliberately not
        `__repr__`-visible — see `__repr__` below.
        """
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def __repr__(self) -> str:
        """Never render key material.

        A dataclass' generated repr would print the key object, and while
        `cryptography`'s own repr is opaque today, relying on that is relying on a
        third party's formatting choice to keep a secret out of logs. This is the
        same reason `WifSigningKey` and the credential vault avoid it.
        """
        return f"GithubAppKey(app_id={self.app_id!r}, private_key=<redacted>)"


def load_github_app_key(settings: Settings) -> GithubAppKey | None:
    """Return the platform GitHub App key, or None when the connector is off.

    Resolution order mirrors `app/gcp/keys.py::load_wif_signing_key` and
    `bootstrap.py::resolve_credential_encryption_key`, which answer the identical
    question for the other two keys:

      1. Neither `github_app_id` nor `github_app_private_key_pem` set -> None.
         The GitHub connector is OPT-IN, exactly like the Slack notifier
         (`bootstrap.py::resolve_slack_notifier_config`): a deployment that does
         not use it must behave byte-identically to one built before it existed.
      2. Exactly one of the two set -> ConfigurationError. An app id with no key
         cannot sign, and a key with no app id cannot form `iss`; either alone is
         a half-finished deployment and failing the boot names the missing
         variable instead of failing at the first governed tool call.
      3. Both set -> parse, assert RSA, assert modulus >= 2048, return.

    Step 2 is the point, and it is the same asymmetry the Slack resolver draws:
    "Setting exactly one of the two is refused." Absence of the whole feature is
    fine; a partially configured feature is not.

    NOTE ON THE ABSENT EPHEMERAL PATH. The other two loaders mint an ephemeral key
    on the memory backend. This one does NOT, and the difference is not an
    oversight: an ephemeral GitHub App key is worthless. The other keys' public
    halves are either internal or published by this platform, so a self-generated
    pair is coherent. GitHub holds the public half of THIS key, registered when
    the App was created; a locally generated key corresponds to no registered App
    and every token mint would fail at GitHub with a `401`. Returning None (feature
    off) is honest; returning a fabricated key would not be.
    """
    app_id = settings.github_app_id.strip()
    # PEMs are routinely injected with literal backslash-n by secret managers and
    # shell exports; the WIF loader handles the same hazard the same way
    # (app/gcp/keys.py:177).
    pem = settings.github_app_private_key_pem.replace("\\n", "\n").strip()

    if not app_id and not pem:
        return None

    if not app_id:
        raise GithubKeyError(
            "SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM is set but SKYLIZE_GITHUB_APP_ID "
            "is empty. The app id is the JWT 'iss' claim, so a key without it "
            "cannot mint an installation token. Set both or neither."
        )
    if not pem:
        raise GithubKeyError(
            "SKYLIZE_GITHUB_APP_ID is set but SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM "
            "is empty. Nothing can be signed without the key. Set both or "
            "neither. The key is issued once, in GitHub's UI, when the App is "
            "registered (scripts/register_github_app.py) and must be held as a "
            "real secret — it authenticates Skylize to every customer's "
            "installation at once."
        )

    try:
        key: Any = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None
        )
    except Exception as exc:  # noqa: BLE001 — any parse failure is fatal
        raise GithubKeyError(
            "SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM is set but could not be parsed "
            f"({exc}). GitHub issues a PKCS#1 PEM ('BEGIN RSA PRIVATE KEY') at "
            "App registration; PKCS#8 ('BEGIN PRIVATE KEY') is also accepted "
            "here. The value must be the whole PEM including header and footer."
        ) from exc

    if not isinstance(key, RSAPrivateKey):
        raise GithubKeyError(
            "SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM parsed as "
            f"{type(key).__name__}, not RSA. GitHub App JWTs must be signed with "
            "RS256 (verified 2026-09-05), so only an RSA key is legal here. This "
            "is very likely the governance key (P-384) or the WIF signing key "
            "(P-256) pasted into the wrong variable — those are different keys "
            "for different trust domains and neither is usable as a GitHub App "
            "key."
        )

    if key.key_size < MIN_RSA_MODULUS_BITS:
        raise GithubKeyError(
            f"SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM is a {key.key_size}-bit RSA "
            f"key; the minimum accepted is {MIN_RSA_MODULUS_BITS}. GitHub "
            "generates 2048-bit App keys, so a smaller one did not come from "
            "GitHub and should not be trusted to authenticate this platform to "
            "every customer installation."
        )

    log.info(
        "GitHub App key loaded (app_id=%s, %d-bit RSA)", app_id, key.key_size
    )
    return GithubAppKey(private_key=key, app_id=app_id)
