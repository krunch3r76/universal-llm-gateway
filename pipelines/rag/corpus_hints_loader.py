"""Load corpus hints for rag-context suggest_terms injection.

Used by Stargate executor and pipeline_call when running rag-context so
suggest_terms receives current corpus vocabulary from the metadata database.
"""

from __future__ import annotations

from services.rag.corpus_hints import get_hints_for_scopes, load_corpus_hints


def fetch_corpus_hints_text() -> str:
    """Load corpus hints from rag_metadata.db for suggest_terms injection.

    For suggest_terms (no scope yet), returns all scopes concatenated.
    """
    hints = load_corpus_hints()
    return get_hints_for_scopes(hints, scopes=None)
