"""Package fixtures for the RAG test tree (friction:39359 N-f)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from services.rag.chroma_writer import init_chroma_executor, shutdown_chroma_executor


@pytest.fixture(autouse=True)
def _chroma_writer_executor() -> Iterator[None]:
    """Initialize the single-thread Chroma writer for every RAG test."""
    init_chroma_executor()
    yield
    shutdown_chroma_executor()
