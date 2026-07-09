"""Export format enum, Exporter protocol, and shared filename sanitizer."""

from __future__ import annotations

import re
from enum import Enum
from typing import Protocol


class ExportFormat(str, Enum):
    MD = "md"
    PDF = "pdf"
    DOCX = "docx"


class Exporter(Protocol):
    content_type: str

    def export(self, content_markdown: str, title: str) -> bytes: ...
    def filename(self, title: str) -> str: ...


def sanitize_filename(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^\w\s]", "_", s)
    s = re.sub(r"[\s_]+", "_", s)
    s = s.strip("_")
    return (s[:100] or "deliverable")
