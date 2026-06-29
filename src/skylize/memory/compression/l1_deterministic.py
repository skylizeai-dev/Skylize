"""L1 — deterministic pruning (<10ms, pure Python, no models).

The first compression tier. Cheap, lossless-ish structural cleanup that strips
the bulk of the noise out of raw tool payloads before any model touches them:
HTML chrome, base64 blobs, null fields, whitespace runs, and runaway strings.

Everything here is a pure function of its input — no I/O, no driver imports, no
randomness — so the same payload always prunes to the same text. That determinism
is what lets the acceptance benchmark assert a stable ≥40% reduction on a real
Shopify webhook fixture.
"""

from __future__ import annotations

import json
import re
from typing import Any

from markdownify import markdownify as _html_to_markdown

# Default ceiling for an individual string field before it is truncated with an
# ellipsis marker. Overridable per call via CompressionContext.max_string_chars.
DEFAULT_MAX_STRING_CHARS = 2000

# Marker appended to a truncated string so downstream readers (and the audit
# trail) can see the value was clipped rather than naturally short.
TRUNCATION_MARKER = "…[truncated]"

# data: URIs carrying base64 payloads (images, fonts, blobs). The base64 body can
# be megabytes of pure token waste; we keep a breadcrumb, not the bytes.
_BASE64_DATA_URI = re.compile(r"data:[^;,\s]*;base64,[A-Za-z0-9+/=]+")
_BASE64_PLACEHOLDER = "[base64 stripped]"

# Any run of whitespace (incl. newlines/tabs) collapses to a single space.
_WHITESPACE_RUN = re.compile(r"\s+")

# Heuristic: does this string look like HTML worth converting to Markdown? A bare
# "a < b" should not trigger a full HTML parse, so we require a tag-shaped token.
_LOOKS_LIKE_HTML = re.compile(r"<[a-zA-Z!/][^>]*>")

# Image-metadata field names kept when pruning an object that looks like an image
# descriptor. Everything else on such an object is dropped (allow-list, not
# deny-list, so unknown bloat fields never survive).
DEFAULT_IMAGE_METADATA_ALLOW = frozenset(
    {"id", "src", "alt", "url", "width", "height", "mime_type", "content_type"}
)
# A field whose presence marks an object as an image descriptor.
_IMAGE_MARKER_FIELDS = frozenset({"src", "url", "data_uri", "image", "base64"})


def strip_base64(text: str) -> str:
    """Replace every base64 data-URI in `text` with a short placeholder."""
    return _BASE64_DATA_URI.sub(_BASE64_PLACEHOLDER, text)


def collapse_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return _WHITESPACE_RUN.sub(" ", text).strip()


def html_to_markdown(text: str) -> str:
    """Convert HTML to Markdown when the text contains HTML tags.

    Non-HTML text is returned unchanged — the cheap tag probe avoids paying for a
    full parse on plain strings.
    """
    if not _LOOKS_LIKE_HTML.search(text):
        return text
    return _html_to_markdown(text)


def truncate_string(value: str, max_chars: int) -> str:
    """Truncate `value` to `max_chars`, appending the truncation marker if cut."""
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + TRUNCATION_MARKER


def _looks_like_image_object(obj: dict[str, Any]) -> bool:
    """An object is treated as an image descriptor if it carries a marker field."""
    return any(field in obj for field in _IMAGE_MARKER_FIELDS)


def prune_json(
    value: Any,
    *,
    max_chars: int = DEFAULT_MAX_STRING_CHARS,
    image_allow: frozenset[str] = DEFAULT_IMAGE_METADATA_ALLOW,
) -> Any:
    """Recursively prune a decoded JSON value.

    Drops null entries from objects, restricts image-descriptor objects to the
    allow-listed metadata fields, strips base64 from and truncates long strings,
    and recurses into nested containers. Returns a new structure; the input is
    not mutated.
    """
    if isinstance(value, dict):
        is_image = _looks_like_image_object(value)
        pruned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue  # strip null values
            if is_image and key not in image_allow:
                continue  # image metadata allow-list
            pruned[key] = prune_json(item, max_chars=max_chars, image_allow=image_allow)
        return pruned
    if isinstance(value, list):
        return [
            prune_json(item, max_chars=max_chars, image_allow=image_allow)
            for item in value
            if item is not None
        ]
    if isinstance(value, str):
        return truncate_string(strip_base64(value), max_chars)
    return value  # int / float / bool pass through untouched


def compress_l1(payload: str, *, max_chars: int = DEFAULT_MAX_STRING_CHARS) -> str:
    """Run the full L1 deterministic pass over a raw payload string.

    Dispatch by shape:
      - Valid JSON → structural prune (nulls, image allow-list, per-field
        base64-strip + truncation), then re-serialize compactly.
      - Otherwise → treat as text: HTML→Markdown, base64-strip, whitespace
        collapse, then truncate the whole thing.

    Pure and deterministic. Never raises on a malformed payload — a JSON parse
    failure falls through to the text path.
    """
    stripped = payload.strip()
    if stripped and stripped[0] in "{[":
        try:
            decoded = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            decoded = None
        if decoded is not None:
            pruned = prune_json(decoded, max_chars=max_chars)
            # Compact separators: no wasted whitespace tokens in the serialized form.
            return json.dumps(pruned, separators=(",", ":"), ensure_ascii=False)

    text = html_to_markdown(payload)
    text = strip_base64(text)
    text = collapse_whitespace(text)
    return truncate_string(text, max_chars)
