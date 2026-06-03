"""Search response models — hybrid FTS projection shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ._shared import AssertionConfidence
from .assertions import ActionHint, DerivationType


class AssertionSearchSummaryItem(BaseModel):
    """Compact search hit — projection-aware fetch for agent retrieval."""

    id: int
    entity_id: str | None = None
    entity_name: str | None = None
    claim: str
    confidence: AssertionConfidence
    review_status: str | None = None
    combmax_score: float | None = Field(
        None, description="CombMAX fused score: max(bm25_norm, cosine_sim)"
    )
    retrieval_source: str = Field(
        "fts", description="Retrieval branch: 'fts', 'vector', or 'both'"
    )


class AssertionSearchItem(BaseModel):
    """Assertion search result with hybrid retrieval scores."""

    id: int
    entity_id: str | None = None
    entity_name: str | None = None
    claim: str
    confidence: AssertionConfidence
    review_status: str | None = None
    confidence_score: float | None = None
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
    derivation_type: DerivationType | None = None
    prospective_summary: str | None = None
    events_json: str | None = None
    superseded_by: int | None = None
    entrenchment_score: float | None = None
    observed_at: str | None = None
    bm25_score: float | None = Field(None, description="Normalized BM25 score (0-1)")
    cosine_similarity: float | None = Field(
        None, description="Cosine similarity from vector search (0-1)"
    )
    combmax_score: float | None = Field(
        None, description="CombMAX fused score: max(bm25_norm, cosine_sim)"
    )
    retrieval_source: str = Field(
        "fts", description="Retrieval branch: 'fts', 'vector', or 'both'"
    )
    rank: float | None = Field(
        None, description="Raw FTS5 BM25 rank (lower = better match)"
    )
    created_at: str


class AssertionSearchResult(BaseModel):
    query: str
    intent: Literal["summary", "full"] = "summary"
    items: list[AssertionSearchSummaryItem | AssertionSearchItem]
    total: int
    search_mode: str = Field(
        "hybrid", description="'hybrid' when vector is available, 'fulltext' otherwise"
    )
    action_hints: list[ActionHint] | None = None
