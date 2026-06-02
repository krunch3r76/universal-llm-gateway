"""Deterministic document text extraction (EML, shared across MCP + pipelines)."""

from document_text.eml import EmlParseResult, parse_eml_file, render_eml_markdown

__all__ = ["EmlParseResult", "parse_eml_file", "render_eml_markdown"]
