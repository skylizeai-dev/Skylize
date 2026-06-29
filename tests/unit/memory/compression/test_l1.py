"""Unit tests for L1 deterministic pruning.

Covers each operation in isolation (HTML→Markdown, null/base64/whitespace
stripping, image allow-list, truncation) and the full `compress_l1` dispatch for
both JSON and text payloads, including the acceptance criterion that a realistic
Shopify webhook payload shrinks by ≥40%.
"""

from __future__ import annotations

import json

import pytest

from skylize.memory.compression.budget import count_tokens
from skylize.memory.compression.l1_deterministic import (
    DEFAULT_MAX_STRING_CHARS,
    TRUNCATION_MARKER,
    collapse_whitespace,
    compress_l1,
    html_to_markdown,
    prune_json,
    strip_base64,
    truncate_string,
)


class TestStripBase64:
    def test_replaces_data_uri_with_placeholder(self) -> None:
        text = "before data:image/png;base64,iVBORw0KGgoAAAANSUhEUg== after"
        assert strip_base64(text) == "before [base64 stripped] after"

    def test_leaves_plain_text_untouched(self) -> None:
        assert strip_base64("no blobs here") == "no blobs here"

    def test_strips_multiple_blobs(self) -> None:
        text = "data:image/png;base64,AAAA x data:font/woff;base64,BBBB"
        assert strip_base64(text) == "[base64 stripped] x [base64 stripped]"


class TestCollapseWhitespace:
    def test_collapses_runs_and_strips_ends(self) -> None:
        assert collapse_whitespace("  a\n\n\t  b   c \n") == "a b c"

    def test_empty_stays_empty(self) -> None:
        assert collapse_whitespace("   \n\t  ") == ""


class TestHtmlToMarkdown:
    def test_converts_html(self) -> None:
        out = html_to_markdown("<h1>Title</h1><p>body</p>")
        assert "Title" in out
        assert "<h1>" not in out

    def test_passes_non_html_through(self) -> None:
        assert html_to_markdown("a < b and c > d") == "a < b and c > d"


class TestTruncateString:
    def test_truncates_with_marker(self) -> None:
        out = truncate_string("x" * 100, 10)
        assert out == "x" * 10 + TRUNCATION_MARKER

    def test_short_string_unchanged(self) -> None:
        assert truncate_string("short", 10) == "short"


class TestPruneJson:
    def test_strips_null_values(self) -> None:
        assert prune_json({"a": 1, "b": None, "c": "x"}) == {"a": 1, "c": "x"}

    def test_strips_nulls_in_lists(self) -> None:
        assert prune_json([1, None, 2, None]) == [1, 2]

    def test_image_object_restricted_to_allow_list(self) -> None:
        obj = {
            "src": "http://x/y.png",
            "alt": "pic",
            "width": 10,
            "exif_gps": "1,2",
            "thumbnail_blob": "junk",
        }
        pruned = prune_json(obj)
        assert pruned == {"src": "http://x/y.png", "alt": "pic", "width": 10}

    def test_non_image_object_keeps_unknown_fields(self) -> None:
        obj = {"name": "order", "total": 5, "note": "keep me"}
        assert prune_json(obj) == obj

    def test_truncates_long_string_fields(self) -> None:
        pruned = prune_json({"body": "y" * (DEFAULT_MAX_STRING_CHARS + 50)})
        assert pruned["body"].endswith(TRUNCATION_MARKER)
        assert len(pruned["body"]) == DEFAULT_MAX_STRING_CHARS + len(TRUNCATION_MARKER)

    def test_strips_base64_in_string_fields(self) -> None:
        pruned = prune_json({"img": "data:image/png;base64,AAAABBBB"})
        assert pruned["img"] == "[base64 stripped]"

    def test_recurses_nested_structures(self) -> None:
        value = {"outer": {"inner": {"a": None, "b": 2}, "list": [None, {"c": None, "d": 4}]}}
        assert prune_json(value) == {"outer": {"inner": {"b": 2}, "list": [{"d": 4}]}}

    def test_scalars_pass_through(self) -> None:
        assert prune_json(42) == 42
        assert prune_json(True) is True

    def test_does_not_mutate_input(self) -> None:
        original = {"a": None, "b": 1}
        prune_json(original)
        assert original == {"a": None, "b": 1}


class TestCompressL1Dispatch:
    def test_json_payload_pruned_and_compacted(self) -> None:
        payload = json.dumps({"a": None, "b": "  spaced  ", "c": 1})
        out = compress_l1(payload)
        # Compact separators, null dropped.
        assert out == '{"b":"  spaced  ","c":1}'

    def test_malformed_json_falls_through_to_text_path(self) -> None:
        # Looks like JSON (leading brace) but is not parseable.
        out = compress_l1("{not valid json at all")
        assert out == "{not valid json at all"

    def test_html_text_payload_converted(self) -> None:
        out = compress_l1("<h1>Hello</h1>\n\n<p>World</p>")
        assert "<h1>" not in out
        assert "Hello" in out and "World" in out

    def test_text_payload_whitespace_collapsed(self) -> None:
        assert compress_l1("a\n\n\n   b") == "a b"

    def test_respects_custom_max_chars(self) -> None:
        out = compress_l1("z" * 100, max_chars=5)
        assert out == "z" * 5 + TRUNCATION_MARKER

    def test_empty_payload(self) -> None:
        assert compress_l1("") == ""

    def test_never_raises_on_arbitrary_input(self) -> None:
        # A grab-bag of awkward inputs must all return a string, never raise.
        for payload in ["[", "]{", "\x00\x01", "data:;base64,", "<<<>>>"]:
            assert isinstance(compress_l1(payload), str)


@pytest.fixture
def shopify_webhook_payload() -> str:
    """A realistic Shopify order webhook with the usual noise.

    Carries the bloat L1 targets: null fields, a base64 image blob, image objects
    with off-allow-list metadata, and HTML in the note attributes.
    """
    body = {
        "id": 820982911946154508,
        "email": "jon@example.com",
        "closed_at": None,
        "created_at": "2026-01-01T00:00:00-05:00",
        "updated_at": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "note": "<div class='wrapper'><p>Leave at <b>back door</b></p></div>",
        "token": "123456abcd",
        "gateway": None,
        "test": False,
        "total_price": "199.00",
        "subtotal_price": None,
        "currency": "USD",
        "financial_status": "paid",
        "line_items": [
            {
                "id": 866550311766439020,
                "title": "Widget",
                "quantity": 1,
                "sku": None,
                "vendor": None,
                "price": "199.00",
                "image": {
                    "src": "data:image/png;base64," + "QUJDRA==" * 200,
                    "alt": "Widget photo",
                    "width": 800,
                    "height": 600,
                    "exif": "lots of camera metadata here that nobody needs",
                    "color_profile": "sRGB IEC61966-2.1 long descriptor string",
                },
            }
            for _ in range(5)
        ],
        "shipping_address": {
            "first_name": "Jon",
            "last_name": "Doe",
            "company": None,
            "address2": None,
            "latitude": None,
            "longitude": None,
        },
    }
    return json.dumps(body, indent=2)


class TestAcceptanceReduction:
    def test_shopify_webhook_reduced_by_at_least_40_percent(
        self, shopify_webhook_payload: str
    ) -> None:
        before = count_tokens(shopify_webhook_payload)
        after = count_tokens(compress_l1(shopify_webhook_payload))
        reduction = 1.0 - (after / before)
        assert reduction >= 0.40, f"L1 reduced only {reduction:.1%} (need ≥40%)"
