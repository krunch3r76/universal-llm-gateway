"""Load corpus hints for rag-context suggest_terms injection.

Used by Stargate executor and pipeline_call when running rag-context so
suggest_terms receives current corpus vocabulary. Path from RAG config or
default ~/.rag/corpus_hints.yaml.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_DEFAULT_HINTS_PATH = Path.home() / ".rag" / "corpus_hints.yaml"


def fetch_corpus_hints_text() -> str:
    """Load corpus hints and return a single line for prompt injection.

    Uses RAG config corpus_hints_path if set, else default ~/.rag/corpus_hints.yaml.
    For suggest_terms (no scope yet), returns all scopes concatenated.
    """
    path: Path | None = None
    try:
        from services.rag.config import load_config

        config = load_config()
        path = getattr(config, "corpus_hints_path", None)
    except Exception as e:
        logger.debug("Could not load RAG config for corpus_hints_path: %s", e)
    if not path:
        path = _DEFAULT_HINTS_PATH
    from services.rag.corpus_hints import get_hints_for_scopes, load_corpus_hints

    hints = load_corpus_hints(path)
    return get_hints_for_scopes(hints, scopes=None)
