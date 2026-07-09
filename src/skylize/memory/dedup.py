"""
Content canonicalization, fact hashing, and importance decay.

Pure functions only — no I/O, no driver import (import-linter forbids a database
driver in `memory`). These define the *identity* of a memory fact: two writes
that canonicalize to the same (namespace, content) are the same fact and must
collapse to one row with merged provenance.

The hash is the dedup key, so canonicalization is fully specified and stable
across processes / Python versions / unicode encodings:
  1. Unicode NFC normalization (composed form).
  2. Lowercase.
  3. Whitespace runs collapsed to one ASCII space; leading/trailing stripped.

`compute_fact_hash` hashes a canonical JSON object with sorted keys, binding the
namespace into the identity (the same sentence in two namespaces is two facts).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata

# Compiled once. \s matches unicode whitespace; the input is already NFC text.
_WHITESPACE_RUN = re.compile(r"\s+")

# Default half-life for importance reinforcement, in seconds. 30 days: a fact not
# re-seen for a month contributes ~half of a fresh sighting to its prior weight.
DEFAULT_HALF_LIFE_SECONDS: float = 30.0 * 24.0 * 60.0 * 60.0

# Importance added per fresh sighting.
DEFAULT_REINFORCEMENT: float = 1.0


def canonicalize_content(text: str) -> str:
    """Canonical form of `text` used for hashing.

    NFC-normalize → lowercase → collapse whitespace runs → strip. Idempotent:
    ``canonicalize_content(canonicalize_content(x)) == canonicalize_content(x)``.
    """
    normalized = unicodedata.normalize("NFC", text)
    collapsed = _WHITESPACE_RUN.sub(" ", normalized.lower())
    return collapsed.strip()


def compute_fact_hash(namespace: str, content_text: str) -> str:
    """SHA-256 hex of the canonical JSON of ``{namespace, content_canonical}``.

    Both the namespace and the content are canonicalized, so trivial casing /
    whitespace differences never fork a fact's identity. Returns a 64-char
    lowercase hex digest (fits CHAR(64)).
    """
    canonical = {
        "content_canonical": canonicalize_content(content_text),
        "namespace": canonicalize_content(namespace),
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decay_fn(
    prior_score: float,
    seconds_since_last_seen: float,
    *,
    half_life_seconds: float = DEFAULT_HALF_LIFE_SECONDS,
    reinforcement: float = DEFAULT_REINFORCEMENT,
) -> float:
    """New importance score when a fact is reinforced.

    The prior decays exponentially by the elapsed time since it was last seen,
    then a fresh sighting adds ``reinforcement``::

        new = prior * 0.5 ** (Δt / half_life) + reinforcement

    Properties (asserted in tests):
      - Δt = 0 → ``prior + reinforcement`` (maximum for a given prior).
      - Longer gaps decay the prior contribution monotonically toward 0.
      - Δt ≫ half_life → approaches ``reinforcement`` from above.
      - Result is always ≥ ``reinforcement`` (the fresh-sighting floor).
    """
    if seconds_since_last_seen < 0:
        raise ValueError("seconds_since_last_seen must be non-negative")
    if half_life_seconds <= 0:
        raise ValueError("half_life_seconds must be positive")
    decay_factor = math.pow(0.5, seconds_since_last_seen / half_life_seconds)
    return prior_score * decay_factor + reinforcement
