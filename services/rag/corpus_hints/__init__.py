"""Corpus hints: term co-occurrence statistics for vocabulary-aware retrieval."""

from __future__ import annotations

from services.rag.corpus_hints.cooccurrence import filter_hints_by_cooccurrence
from services.rag.corpus_hints.formatting import format_register_hints, get_hints_for_scopes
from services.rag.corpus_hints.freshness import (
    compute_scope_files_hash,
    detect_stale_scopes,
    scopes_touching_watch_path,
)
from services.rag.corpus_hints.loaders import load_corpus_hints, load_scope_vocabulary
from services.rag.corpus_hints.update import update_corpus_hints

__all__ = [
    "compute_scope_files_hash",
    "detect_stale_scopes",
    "filter_hints_by_cooccurrence",
    "format_register_hints",
    "get_hints_for_scopes",
    "load_corpus_hints",
    "load_scope_vocabulary",
    "scopes_touching_watch_path",
    "update_corpus_hints",
]

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from services.rag.corpus_hints.cli import main

    main()
