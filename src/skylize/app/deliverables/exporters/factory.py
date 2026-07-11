"""Exporter registry — maps ExportFormat to the correct Exporter instance."""

from __future__ import annotations

from .base import ExportFormat, Exporter
from .docx_exporter import DocxExporter
from .markdown_exporter import MarkdownExporter
from .pdf_exporter import PdfExporter

_REGISTRY: dict[ExportFormat, Exporter] = {
    ExportFormat.MD: MarkdownExporter(),
    ExportFormat.PDF: PdfExporter(),
    ExportFormat.DOCX: DocxExporter(),
}


def get_exporter(fmt: ExportFormat) -> Exporter:
    try:
        return _REGISTRY[fmt]
    except KeyError:
        raise ValueError(f"no exporter for format: {fmt!r}")
