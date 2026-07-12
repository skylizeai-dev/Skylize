"""Unit tests for deliverable exporters."""

from __future__ import annotations

import io
import zipfile

import pytest

from skylize.app.deliverables.exporters.base import ExportFormat, sanitize_filename
from skylize.app.deliverables.exporters.docx_exporter import DocxExporter
from skylize.app.deliverables.exporters.factory import get_exporter
from skylize.app.deliverables.exporters.markdown_exporter import MarkdownExporter
from skylize.app.deliverables.exporters.pdf_exporter import PdfExporter

_MD = "# Hello\n\nThis is a test.\n\n- item 1\n- item 2\n"
_TITLE = "Test Document"


# ---------------------------------------------------------------------------
# MarkdownExporter
# ---------------------------------------------------------------------------

def test_markdown_exporter_returns_utf8_bytes() -> None:
    result = MarkdownExporter().export(_MD, _TITLE)
    assert isinstance(result, bytes)
    assert result.decode("utf-8") == _MD


def test_markdown_exporter_content_type() -> None:
    assert MarkdownExporter().content_type == "text/markdown"


def test_markdown_exporter_filename() -> None:
    assert MarkdownExporter().filename("Q3 Strategy") == "q3_strategy.md"


# ---------------------------------------------------------------------------
# PdfExporter
# ---------------------------------------------------------------------------

def test_pdf_exporter_returns_pdf_bytes() -> None:
    result = PdfExporter().export(_MD, _TITLE)
    assert isinstance(result, bytes)
    assert result.startswith(b"%PDF-")


def test_pdf_exporter_content_type() -> None:
    assert PdfExporter().content_type == "application/pdf"


def test_pdf_exporter_filename() -> None:
    assert PdfExporter().filename("Q3 Strategy") == "q3_strategy.pdf"


# ---------------------------------------------------------------------------
# DocxExporter
# ---------------------------------------------------------------------------

def test_docx_exporter_returns_zipfile() -> None:
    result = DocxExporter().export(_MD, _TITLE)
    assert isinstance(result, bytes)
    assert result[:2] == b"PK"
    assert zipfile.is_zipfile(io.BytesIO(result))


def test_docx_exporter_content_type() -> None:
    assert DocxExporter().content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_docx_exporter_filename() -> None:
    assert DocxExporter().filename("Q3 Strategy") == "q3_strategy.docx"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_returns_markdown_exporter() -> None:
    assert isinstance(get_exporter(ExportFormat.MD), MarkdownExporter)


def test_factory_returns_pdf_exporter() -> None:
    assert isinstance(get_exporter(ExportFormat.PDF), PdfExporter)


def test_factory_returns_docx_exporter() -> None:
    assert isinstance(get_exporter(ExportFormat.DOCX), DocxExporter)


def test_factory_unknown_format_raises() -> None:
    with pytest.raises((ValueError, KeyError)):
        get_exporter("xyz")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

def test_sanitize_basic() -> None:
    assert sanitize_filename("Q3 Marketing Strategy v2") == "q3_marketing_strategy_v2"


def test_sanitize_special_chars() -> None:
    assert sanitize_filename("Hello! World? #1") == "hello_world_1"


def test_sanitize_unicode_non_empty() -> None:
    result = sanitize_filename("Ünïcödé Têst")
    assert result  # must produce something


def test_sanitize_empty_falls_back_to_deliverable() -> None:
    assert sanitize_filename("") == "deliverable"


def test_sanitize_punctuation_only_falls_back() -> None:
    assert sanitize_filename("!!!") == "deliverable"


def test_sanitize_length_limit() -> None:
    assert len(sanitize_filename("a" * 200)) <= 100
