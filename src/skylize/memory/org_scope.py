"""The org-scope rule for the shared vector collection — one definition, two stores.

``platform_knowledge`` is a single Qdrant collection holding every tenant's
points, so there is no store-level equivalent of Postgres RLS: the ``org_id``
filter IS the tenant boundary. This module is the only place that boundary is
defined, so ``QdrantAdapter`` and its in-memory twin cannot drift apart and let a
test double prove an isolation guarantee the real store does not make.

Driver-free by construction (no ``qdrant_client`` import), so the memory backend
does not pull the vector client in just to know what "scoped" means.

The rule, in full:
  1. An org scope is a non-empty string. Anything else fails closed rather than
     widening to the whole collection.
  2. Extra filters may only NARROW the scope. Restating ``org_id`` inside them is
     rejected, so a call site cannot shadow, widen, or blank the scope it was
     handed.
"""

from __future__ import annotations

from typing import Any

# The payload key every point is stamped with and every query is filtered on.
ORG_FIELD = "org_id"


class OrgScopeRequired(ValueError):
    """A collection operation was attempted without a usable org scope.

    Raised rather than silently widening to the whole collection: an unscoped
    query against a shared collection is a cross-tenant read.
    """


def require_org(org_id: str) -> str:
    """Return ``org_id`` if it can scope a query, else fail closed."""
    if not isinstance(org_id, str) or not org_id.strip():
        raise OrgScopeRequired(
            f"org_id must be a non-empty string to scope a shared-collection "
            f"operation; got {org_id!r}"
        )
    return org_id


def scoped_filters(org_id: str, extra: dict[str, Any] | None) -> dict[str, Any]:
    """Exact-match filter map that ALWAYS carries the org condition.

    ``extra`` narrows further; it may not restate ``org_id``. Callers pass the
    result straight to their store's matcher, so the org condition is present by
    construction and not by the caller remembering it.
    """
    extra = extra or {}
    if ORG_FIELD in extra:
        raise OrgScopeRequired(
            f"{ORG_FIELD} is supplied via the org_id argument and must not appear "
            "in filters (a call site must not be able to shadow its own scope)"
        )
    return {ORG_FIELD: require_org(org_id), **extra}
