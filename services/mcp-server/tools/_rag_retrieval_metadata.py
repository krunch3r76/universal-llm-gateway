"""Extract compact retrieval metadata from rag-context pipeline responses.

Called by ``tools/_rag_search_exec.py`` to shape the ``retrieval`` block of the
``rag_search`` envelope from Stargate's ``pipeline.retrieval`` payload: scope
resolution fields plus — when the pipeline's rerank step emitted them — the
per-chunk relevance rows (``chunks[]``) and the ``weak_match`` verdict that let
an author gate on relevance without reading every chunk.
"""

from __future__ import annotations

from typing import Any

# Relevance keys pass through verbatim; their shape is owned by
# ``pipelines/rag/rag_context_v1/rerank_scoring.relevance_summary``.
_RELEVANCE_KEYS = (
    "chunks",
    "weak_match",
    "top_relevance",
    "weak_match_threshold",
    "rerank_status",
    "weak_match_basis",
    "rerank_error",
)


def retrieval_metadata_from_response(
    response: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return ``pipeline.retrieval`` when present and non-empty."""
    if not response:
        return None
    pipeline = response.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    retrieval = pipeline.get("retrieval")
    if not isinstance(retrieval, dict) or not retrieval:
        return None
    return retrieval


def envelope_retrieval_fields(
    retrieval: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape MCP envelope fields from pipeline retrieval metadata."""
    if not retrieval:
        return {}
    scope_source = retrieval.get("scope_source", "default_scope")
    envelope: dict[str, Any] = {
        "retrieval": {
            "resolved_scope": retrieval.get("resolved_scope"),
            "chunks_found": retrieval.get("chunks_found"),
            "scope_rejected": retrieval.get("scope_rejected", False),
            "scope_source": scope_source,
            "auto_classified": scope_source == "classifier",
        }
    }
    if "scope_confidence" in retrieval:
        envelope["retrieval"]["scope_confidence"] = retrieval["scope_confidence"]
    if retrieval.get("scope_key") is not None:
        envelope["retrieval"]["scope_key"] = retrieval["scope_key"]
    rejection_reason = retrieval.get("scope_rejection_reason")
    if isinstance(rejection_reason, str) and rejection_reason:
        envelope["retrieval"]["scope_rejection_reason"] = rejection_reason
    retrieval_rejection = retrieval.get("retrieval_rejection_reason")
    if isinstance(retrieval_rejection, str) and retrieval_rejection:
        envelope["retrieval"]["retrieval_rejection_reason"] = retrieval_rejection
    empty_reason = retrieval.get("empty_reason")
    if isinstance(empty_reason, str) and empty_reason:
        envelope["retrieval"]["empty_reason"] = empty_reason
    for key in _RELEVANCE_KEYS:
        if key in retrieval:
            envelope["retrieval"][key] = retrieval[key]
    return envelope
