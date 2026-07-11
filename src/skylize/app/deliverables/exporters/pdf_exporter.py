"""PDF exporter — markdown → HTML → PDF via xhtml2pdf (pure Python, no system deps)."""

from __future__ import annotations

import io

import markdown as md_lib
from xhtml2pdf import pisa

from .base import sanitize_filename


class PdfExporter:
    content_type = "application/pdf"

    def export(self, content_markdown: str, title: str) -> bytes:
        html = (
            f"<html><head><meta charset='utf-8'>"
            f"<title>{title}</title></head>"
            f"<body>{md_lib.markdown(content_markdown)}</body></html>"
        )
        buf = io.BytesIO()
        pisa.CreatePDF(html, dest=buf)
        return buf.getvalue()

    def filename(self, title: str) -> str:
        return f"{sanitize_filename(title)}.pdf"
