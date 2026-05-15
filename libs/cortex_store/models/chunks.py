"""Chunk-shape Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from ._shared import reject_cortex_dropbox_source_uri


class ChunkCreate(BaseModel):
    content: str
    source_uri: str
    source_date: str | None = None
    observer: str = "web-claude"
    chunk_index: int = 0
    extraction_run: int | None = None
    token_count: int | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


class ChunkItem(BaseModel):
    id: int
    content: str
    source_uri: str
    source_date: str | None = None
    observer: str | None = None
    chunk_index: int | None = None
    extraction_run: int | None = None
    token_count: int | None = None
    created_at: str


class ChunkList(BaseModel):
    items: list[ChunkItem]
