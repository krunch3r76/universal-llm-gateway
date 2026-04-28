"""Shared helper for deriving pipeline stage from property-index state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.rag.models import PipelineStage


def get_pipeline_stage(
    source_path: str, prop_idx: Any
) -> tuple[PipelineStage, str | None, int]:
    """Derive pipeline stage, queue_state, and global queue depth.

    Returns (stage, queue_state, queue_depth).
    queue_state is the precise extraction_queue state when stage == "queued", else None.
    "contextualized" requires: is_indexed AND has_contextualized_chunks AND no queue_row.
    Removing a source from extraction_queue only happens on full extraction success
    (complete_extraction), so the absence of a queue_row implies completeness.
    """
    source_state = prop_idx.get_source_pipeline_state(source_path)
    queue_depth: int = prop_idx.get_extraction_queue_count()
    queue_row: dict | None = source_state.get("queue_row")
    is_indexed: bool = source_state.get("is_indexed", False)
    ctx_count: int = source_state.get("contextualized_chunks", 0)
    if queue_row is not None:
        return "queued", queue_row.get("state", "ready"), queue_depth
    if ctx_count > 0:
        return "contextualized", None, queue_depth
    if is_indexed:
        return "chunked", None, queue_depth
    return "registered", None, queue_depth
