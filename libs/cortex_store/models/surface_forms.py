"""Surface-form resolution-cache Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel


class SurfaceFormCreate(BaseModel):
    mention: str
    entity_id: str
    chunk_id: int | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    context_hash: str | None = None
    mention_type: str | None = None


class SurfaceFormItem(BaseModel):
    id: int
    mention: str
    entity_id: str
    chunk_id: int | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    context_hash: str | None = None
    mention_type: str | None = None
    created_at: str


class SurfaceFormList(BaseModel):
    items: list[SurfaceFormItem]


class SurfaceFormCacheResult(BaseModel):
    hit: bool
    entity_id: str | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
