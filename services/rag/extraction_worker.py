"""Async background worker for decoupled knowledge extraction.

Extraction is decoupled from the indexing hot path: files become searchable
immediately after chunk/embed/upsert. This worker processes the extraction
queue independently — model contention, timeouts, and retries never block
indexing or search.

Architecture:
  indexing._index_file_impl  →  ChromaDB upsert  →  enqueue_extraction(source)
  extraction_worker (this)   →  dequeue  →  extract  →  patch ChromaDB metadata
                                                     →  write property index

The worker runs as a single asyncio task started by lifecycle.py.
"""

from __future__ import annotations

from services.rag.extraction.worker_loop import run_extraction_worker

__all__ = ("run_extraction_worker",)
