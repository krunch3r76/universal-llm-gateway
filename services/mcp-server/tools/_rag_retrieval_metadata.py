"""Extract compact retrieval metadata from rag-context pipeline responses."""

from __future__ import annotations

from typing import Any


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
    return envelope
