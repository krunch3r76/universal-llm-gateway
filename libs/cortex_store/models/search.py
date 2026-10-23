"""Search response models — hybrid FTS projection shapes."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ._shared import AssertionConfidence
from .assertions import ActionHint, DerivationType


def fulltext_search_action_hints(*, query: str) -> list[ActionHint]:
    """Hints when hybrid search degraded to FTS-only (vector branch returned nothing).

    total=0 under search_mode=fulltext does not establish absence — vector recall
    was not exercised. Name alternate recall surfaces the seat can invoke next.
    """
    preview = query.strip()[:80] or "(empty query)"
    return [
        ActionHint(
            category="search_recall_degraded",
            message=(
                f"search_mode=fulltext: vector recall was unavailable for "
                f'"{preview}"; zero hits do not establish absence.'
            ),
            action=(
                "Before inferring absence, try another recall surface: "
                'cortex(op="entity_get", entity_id=...) when the entity is known, '
                'cortex(op="list", entity_id=...) or GET /assertions with '
                "entity_type filter, recall_matter/recall_continuity for hub "
                'orientation, or rag(query=...) for document recall.'
            ),
        )
    ]


def entity_search_action_hints(*, entity_id: str) -> list[ActionHint]:
    """Hints on entity search hits — card is the primary deepen surface."""
    return [
        ActionHint(
            category="entity_search_hit",
            entity_id=entity_id,
            message=f"Entity identity match for {entity_id!r}.",
            action=(
                f'cortex(op="entity_get", entity_id="{entity_id}", intent="card")'
            ),
        )
    ]


class AssertionSearchSummaryItem(BaseModel):
    """Compact assertion search hit."""

    kind: Literal["assertion"] = "assertion"
    id: int
    entity_id: str | None = None
    entity_name: str | None = None
    claim: str
    confidence: AssertionConfidence
    review_status: str | None = None
    score: float | None = Field(None, description="Tier1 RRF fused score")
    bm25_score: float | None = Field(
        None, description="Absolute BM25 magnitude from FTS5 rank"
    )
    cosine_similarity: float | None = Field(
        None, description="Cosine similarity from vector search (0-1)"
    )
    combmax_score: float | None = Field(
        None,
        description="Deprecated alias of score for older clients",
    )
    retrieval_source: str = Field(
        "fts", description="Retrieval branch: 'fts', 'vector', or 'both'"
    )


class EntitySearchSummaryItem(BaseModel):
    """Compact entity identity search hit (tier0 strict or tier1 loose RRF)."""

    kind: Literal["entity"] = "entity"
    entity_id: str
    entity_type: str
    entity_name: str | None = None
    score: float = Field(description="Prior-weighted BM25 (tier0) or RRF (tier1)")
    retrieval_source: Literal["entity"] = "entity"
    assertion_count: int = Field(
        ge=0, description="Active (non-superseded) assertions on the entity"
    )
    action_hints: list[ActionHint] | None = None


class AssertionSearchItem(BaseModel):
    """Assertion search result with hybrid retrieval scores."""

    kind: Literal["assertion"] = "assertion"
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
    score: float | None = Field(None, description="Tier1 RRF fused score")
    bm25_score: float | None = Field(
        None, description="Absolute BM25 magnitude from FTS5 rank"
    )
    cosine_similarity: float | None = Field(
        None, description="Cosine similarity from vector search (0-1)"
    )
    combmax_score: float | None = Field(
        None,
        description="Deprecated alias of score for older clients",
    )
    retrieval_source: str = Field(
        "fts", description="Retrieval branch: 'fts', 'vector', or 'both'"
    )
    rank: float | None = Field(
        None, description="Raw FTS5 BM25 rank (lower = better match)"
    )
    created_at: str


class EntitySearchItem(BaseModel):
    """Full-intent entity identity search hit."""

    kind: Literal["entity"] = "entity"
    entity_id: str
    entity_type: str
    entity_name: str | None = None
    score: float
    retrieval_source: Literal["entity"] = "entity"
    assertion_count: int = Field(ge=0)
    action_hints: list[ActionHint] | None = None


SearchSummaryItem = Annotated[
    AssertionSearchSummaryItem | EntitySearchSummaryItem,
    Field(discriminator="kind"),
]
SearchFullItem = Annotated[
    AssertionSearchItem | EntitySearchItem,
    Field(discriminator="kind"),
]


class AssertionSearchResult(BaseModel):
    query: str
    intent: Literal["summary", "full"] = "summary"
    items: list[SearchSummaryItem | SearchFullItem]
    total: int
    search_mode: str = Field(
        "hybrid", description="'hybrid' when vector is available, 'fulltext' otherwise"
    )
    action_hints: list[ActionHint] | None = None
