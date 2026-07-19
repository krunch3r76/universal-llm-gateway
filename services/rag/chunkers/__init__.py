"""Document chunking for RAG indexing."""

from __future__ import annotations

from services.rag.chunkers.epub_html import normalize_html_to_markdown
from services.rag.chunkers.office_dispatch import chunk_file
from services.rag.chunkers.paragraph_utils import Chunk

__all__ = ["Chunk", "chunk_file", "normalize_html_to_markdown"]
