"""Markdown exporter — returns raw content as UTF-8 bytes."""

from __future__ import annotations

from .base import sanitize_filename


class MarkdownExporter:
    content_type = "text/markdown"

    def export(self, content_markdown: str, title: str) -> bytes:
        return content_markdown.encode("utf-8")

    def filename(self, title: str) -> str:
        return f"{sanitize_filename(title)}.md"
