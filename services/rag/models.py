from __future__ import annotations

from pydantic import BaseModel

DECAY_LAMBDA = 0.01  # half-life ~= 69 days


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    recency_weight: float = 0.0
    max_distance: float | None = None  # None = return all (backward compat)
    source_prefixes: list[str] | None = None
    scope: str | None = (
        None  # Resolved to source_prefixes via config; mutually exclusive
    )


class SearchResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, str | int | float | bool]]
    distances: list[float]


class IndexRequest(BaseModel):
    path: str
    metadata_overrides: dict[str, str | int | float | bool] | None = None


class IndexResult(BaseModel):
    indexed: int
    deleted: int
    unchanged: bool
    file: str
    duplicate: bool = False
    duplicate_of: str | None = None


class IndexDirectoryRequest(BaseModel):
    path: str
    extensions: list[str] | None = None
    metadata_overrides: dict[str, str | int | float | bool] | None = None


class IndexDirectoryResponse(BaseModel):
    indexed: int
    deleted: int
    unchanged: int
    files: int
    duplicates: int = 0


class StatsResponse(BaseModel):
    count: int
    collection: str


class ClearResponse(BaseModel):
    deleted: int
    collection: str


class SourceResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, str | int | float | bool]]


class ScopeInfo(BaseModel):
    prefixes: list[str]
    description: str


class ScopesResponse(BaseModel):
    scopes: dict[str, ScopeInfo]
