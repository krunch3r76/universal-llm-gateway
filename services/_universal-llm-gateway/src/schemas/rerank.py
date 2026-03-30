"""Rerank API schemas — request/response models for cross-encoder reranking."""

from pydantic import BaseModel, Field


class RerankRequest(BaseModel):
    """Request schema for rerank endpoint."""

    model: str = Field(..., description="Reranker model ID")
    query: str = Field(..., description="Query to score against passages")
    passages: list[str] = Field(..., description="Passages to score")

    class Config:
        extra = "allow"


class RerankResponse(BaseModel):
    """Response schema for rerank endpoint."""

    scores: list[float] = Field(..., description="Relevance scores, one per passage")
    model: str = Field(..., description="Model used")
