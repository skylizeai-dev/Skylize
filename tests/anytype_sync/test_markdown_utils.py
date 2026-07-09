from __future__ import annotations

import pytest

from anytype_sync.markdown_utils import unescape_anytype_markdown


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Escaped pipe (common in Anytype table exports)
        (r"\|column\|", "|column|"),
        # Escaped underscore (italic marker)
        (r"hello\_world", "hello_world"),
        # Escaped brackets (link syntax)
        (r"\[link\]\(url\)", "[link](url)"),
        # Escaped backtick (code)
        (r"\`code\`", "`code`"),
        # Escaped asterisk (bold/italic)
        (r"\*bold\*", "*bold*"),
        # Escaped backslash itself
        ("\\\\path", "\\path"),
        # Escaped hash
        (r"\# not a heading", "# not a heading"),
        # Mix of escaped and unescaped
        (r"plain \| escaped", "plain | escaped"),
        # No escapes — unchanged
        ("just text", "just text"),
        # Escape at end of string
        (r"end\!", "end!"),
        # Double-escaped backslash followed by char — only first pair unescapes
        (r"\\\_", "\\_"),
        # Dot and minus
        (r"\. \-", ". -"),
    ],
)
def test_unescape(raw: str, expected: str) -> None:
    assert unescape_anytype_markdown(raw) == expected


def test_does_not_strip_unrecognised_escapes() -> None:
    # \n is not in the Markdown special-char set; must stay intact
    assert unescape_anytype_markdown(r"\n") == r"\n"


def test_empty_string() -> None:
    assert unescape_anytype_markdown("") == ""
