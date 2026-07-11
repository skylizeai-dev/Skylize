"""Deterministic, injective tenant-scoped identity for the knowledge vector store.

Tenant isolation depends on the ``(org_id, doc_id) -> point_id`` map being
INJECTIVE: two distinct pairs must never collide onto one Qdrant point, or one
tenant's write could silently overwrite another's. The pre-remediation scheme
built the key as ``f"{org_id}:{doc_id}"`` and md5-hashed it, which is NOT
injective — ``("a:b", "c")`` and ``("a", "b:c")`` both yield ``"a:b:c"``. Here
every field is LENGTH-PREFIXED before hashing, so the concatenation is
unambiguous and the map is injective by construction (proved in CI by a
hypothesis property test). Identifiers that cross a trust boundary — ``org_id``
at registration and ``doc_id`` at the ingest webhook — are additionally charset
validated and MUST reject ``:`` as defence in depth on top of the encoding.
"""

from __future__ import annotations

import hashlib
import re
import uuid

# Fixed namespace for every Skylize knowledge point id. NEVER regenerate: this
# value is part of the on-disk identity of every stored vector.
_NAMESPACE = uuid.UUID("b1f9a7c4-3e2d-5a68-9c14-7d0e5f2a8b31")

# Lowercase slug: first char alnum, then alnum / underscore / hyphen, 1..128
# chars. Deliberately excludes ':' (the delimiter that broke injectivity) and
# every other separator, so a validated identifier can never forge a field
# boundary in the length-prefixed encoding.
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}")


class InvalidIdentifier(ValueError):
    """An org_id or doc_id violated the identifier charset (fail closed)."""


def validate_identifier(value: str, *, field: str) -> str:
    """Return ``value`` unchanged if it is a valid identifier, else raise."""
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise InvalidIdentifier(
            f"{field} must match ^[a-z0-9][a-z0-9_-]{{0,127}}$ "
            f"(lowercase slug, no ':'); got {value!r}"
        )
    return value


def _encode(*parts: str) -> bytes:
    """Length-prefixed, unambiguous encoding of a tuple of strings.

    Each part is UTF-8 encoded and preceded by its byte length as an 8-byte
    big-endian integer. Because the boundaries are explicit, no combination of
    field contents can encode to the same bytes as a different tuple — the
    encoding, and therefore any hash of it, is injective over input tuples.
    """
    out = bytearray()
    for part in parts:
        raw = part.encode("utf-8")
        out += len(raw).to_bytes(8, "big")
        out += raw
    return bytes(out)


def point_id(org_id: str, doc_id: str) -> str:
    """Injective ``(org_id, doc_id) -> Qdrant point id`` (UUIDv5 string).

    UUIDv5 over the length-prefixed encoding: distinct pairs -> distinct
    encodings -> distinct ids. This is the tenant-isolation invariant the
    hypothesis property test pins in CI.
    """
    return str(uuid.uuid5(_NAMESPACE, _encode(org_id, doc_id).hex()))


def content_doc_id(content: bytes, *, prefix: str) -> str:
    """Content-derived stable doc_id.

    Identical bytes -> identical id, so the content-hash idempotency check
    dedups a re-upload of the same file; distinct bytes -> distinct id, so two
    uploads in the same second never collide onto one document.
    """
    return f"{prefix}/{hashlib.sha256(content).hexdigest()}"


def chunk_doc_id(doc_id: str, chunk_index: int) -> str:
    """The per-chunk doc_id string for chunk ``chunk_index`` of ``doc_id``."""
    return f"{doc_id}#chunk{chunk_index}"


def chunk_point_id(org_id: str, doc_id: str, chunk_index: int) -> str:
    """Injective point id for chunk ``chunk_index`` of ``doc_id`` under ``org_id``."""
    return point_id(org_id, chunk_doc_id(doc_id, chunk_index))
