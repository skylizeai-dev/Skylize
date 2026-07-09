from __future__ import annotations

import re

# Only unescape the specific Markdown special-char set, not all backslashes.
# Anytype escapes: \ ` * _ { } [ ] ( ) # + - . ! |
_UNESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|])")


def unescape_anytype_markdown(text: str) -> str:
    return _UNESCAPE_RE.sub(r"\1", text)
