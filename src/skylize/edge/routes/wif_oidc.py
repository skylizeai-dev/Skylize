"""Public OIDC issuer endpoints for GCP Workload Identity Federation.

    GET /w/{issuer_slug}/.well-known/openid-configuration
    GET /w/{issuer_slug}/jwks.json

THESE ARE THE FIRST DELIBERATELY UNAUTHENTICATED, EXTERNALLY-CONSUMED ROUTES IN
THIS GATEWAY, and that is the whole risk of this module. Every other router here
resolves a `RequestContext` and refuses without one; `/health`
(edge/gateway.py) is the only prior public route and it is not consumed by a
third party. Google fetches these two with NO credentials, from an IP Skylize
does not control, so they carry no `Depends(get_context)` and must never acquire
one - adding auth here silently breaks every customer federation, and the
breakage surfaces only when a customer's connection is needed.

FOUR PROPERTIES THAT MAKE THAT SAFE, each load-bearing:

1. NO DATABASE ACCESS. Every field of both documents derives from the slug plus
   platform configuration (app/gcp/oidc.py). These handlers never touch
   `gcp_wif_connections`, so the RLS-scoped table needs no cross-tenant carve-out
   and a database outage cannot take the issuer surface down. That last point
   matters more than it looks: if Google cannot fetch the JWKS, the token
   exchange fails with `invalid_grant` at exactly the moment a customer is
   relying on the connection.

2. NO TENANT-EXISTENCE ORACLE. A well-formed slug gets a well-formed document
   whether or not any org owns it, because the handler never looks. Returning 404
   for an unknown slug would turn this endpoint into an enumeration oracle over
   the customer list - and since it cannot enumerate what it never reads, the
   property holds by construction rather than by remembering to preserve it.

3. NO PRIVATE KEY MATERIAL. The JWKS is built from
   `WifSigningKey.public_jwk()`, which reads `private_key.public_key()` - the EC
   private scalar `d` is absent from the object the serialiser sees, not merely
   omitted by it. Pinned by test at both the unit and HTTP layers.

4. SLUG SHAPE IS VALIDATED BEFORE USE. `is_valid_issuer_slug` is an anchored
   allow-list of exactly 26 lowercase alphanumerics, so no caller-controlled text
   is ever concatenated into an issuer URL. A malformed slug is a 404 - the same
   answer an unowned one gets, which is what keeps property 2 true.

CACHING. Both documents are static per deployment, so both are served with a
public `Cache-Control`. The JWKS max-age is deliberately SHORT relative to the
document's stability: when key rotation is eventually built, the wait between
publishing a new key and signing with it is bounded by how long Google may hold a
stale copy, and a shorter max-age shortens that wait. Rotation is NOT implemented
in this pass; the header is set now so the caching behaviour a rotation runbook
must reason about is already in place and measurable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from ...app.gcp.oidc import (
    ISSUER_PATH_PREFIX,
    discovery_document,
    is_valid_issuer_slug,
    jwks_document,
)
from ...bootstrap import Container
from ..deps import get_container

#: No `/api/v1` prefix, deliberately. This is not part of the tenant-facing API;
#: it is an identity-provider surface whose URLs are pasted into customers' Google
#: Cloud configuration and must stay stable independently of API versioning.
router = APIRouter(prefix=f"/{ISSUER_PATH_PREFIX}", tags=["wif-oidc"])

#: The discovery document changes only on deployment.
_DISCOVERY_CACHE = "public, max-age=300"
#: Shorter: this is the document a rotation must wait out. See module docstring.
_JWKS_CACHE = "public, max-age=300"


def _issuer_surface(container: Container) -> tuple[str, Any]:
    """Return `(base_url, key)` or refuse with 404 when the feature is off.

    404 rather than 503: to an unauthenticated internet caller, a deployment that
    does not offer an issuer surface and a deployment that does not exist should
    be indistinguishable. A 503 would advertise that the feature exists here and
    is merely unconfigured.
    """
    # Direct attribute access, NOT `getattr(..., None)`. The Container declares
    # `wif_signing_key`, so a missing attribute would be a wiring bug — and a
    # defaulted getattr would silently convert that bug into a 404, i.e. into
    # "every customer's federation is broken and nothing says why".
    base_url = container.settings.wif_issuer_base_url.strip()
    key = container.wif_signing_key
    if not base_url or key is None:
        raise HTTPException(status_code=404, detail="not found")
    return base_url, key


@router.get("/{issuer_slug}/.well-known/openid-configuration")
async def openid_configuration(
    issuer_slug: str,
    container: Container = Depends(get_container),
) -> Response:
    base_url, _ = _issuer_surface(container)
    if not is_valid_issuer_slug(issuer_slug):
        raise HTTPException(status_code=404, detail="not found")
    return _json(discovery_document(base_url, issuer_slug), _DISCOVERY_CACHE)


@router.get("/{issuer_slug}/jwks.json")
async def jwks(
    issuer_slug: str,
    container: Container = Depends(get_container),
) -> Response:
    _, key = _issuer_surface(container)
    if not is_valid_issuer_slug(issuer_slug):
        raise HTTPException(status_code=404, detail="not found")
    return _json(jwks_document(key), _JWKS_CACHE)


def _json(payload: dict[str, Any], cache_control: str) -> Response:
    """Serialise with an explicit `application/json` and cache header.

    `JSONResponse` via a plain return would work, but these two documents are
    fetched by a third party whose parser Skylize does not control, so the
    content type and caching are set explicitly rather than inherited.
    """
    import json

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"Cache-Control": cache_control},
    )
