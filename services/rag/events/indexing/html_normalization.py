"""RAG indexing event factories — HTML normalization events.

HTML sources are converted to deterministic markdown before chunking; these
three signals (``rag.html.normalization.started``, ``.completed`` with output
character count, and ``.failed``, after which the file is skipped) let
operators trace that pre-chunking step. Emitted by
``rag_service.indexing.index_file``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_html_normalization_started(*, file: str) -> Event:
    """Emitted when HTML ingest enters the normalization pipeline."""
    return Event(signal="rag.html.normalization.started", payload={"file": file})


@event_factory
def rag_html_normalization_completed(
    *,
    file: str,
    output_chars: int,
) -> Event:
    """Emitted when HTML normalization succeeds with deterministic markdown output."""
    return Event(
        signal="rag.html.normalization.completed",
        payload={"file": file, "output_chars": output_chars},
    )


@event_factory
def rag_html_normalization_failed(*, file: str, error: str) -> Event:
    """Emitted when HTML normalization fails and file is skipped from indexing."""
    return Event(
        signal="rag.html.normalization.failed",
        payload={"file": file, "error": error},
    )
