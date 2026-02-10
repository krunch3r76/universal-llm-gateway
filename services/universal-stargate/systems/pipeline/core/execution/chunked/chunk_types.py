"""
Types for chunked model execution.

Invariant: ∀ ChunkResult: len(results) == len(item_indices)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class Chunk:
    """A chunk of items to process together."""

    index: int  # Chunk index (0-based)
    items: list[Any]  # Items in this chunk
    item_indices: list[int]  # Original indices in source list
    key: str | None = None  # Optional grouping key (domain, originator)


@dataclass(slots=True, kw_only=True)
class ProcessResult:
    """
    Return type for process_fn when token counting is needed.

    Callers can return either:
    - list[Any]: Just results (backward compat, tokens default to 0)
    - ProcessResult: Results with token counts
    """

    results: list[Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class ChunkResult:
    """Result from processing one chunk."""

    chunk_index: int
    item_indices: list[int]  # Original indices
    results: list[Any]  # Per-item results (same order as item_indices)
    model_used: str
    latency_ms: float
    fallback_used: bool = False
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class ChunkedResult:
    """Aggregated result from all chunks."""

    results: list[Any]  # Merged results in original order
    chunk_results: list[ChunkResult]  # Per-chunk metadata
    total_latency_ms: float  # Wall-clock time (parallel)
    models_used: set[str] = field(default_factory=set)
    fallback_count: int = 0

    @property
    def success_rate(self) -> float:
        """Fraction of chunks that succeeded without fallback."""
        if not self.chunk_results:
            return 1.0
        succeeded = sum(1 for cr in self.chunk_results if not cr.fallback_used)
        return succeeded / len(self.chunk_results)

    @property
    def total_prompt_tokens(self) -> int:
        """Sum of prompt tokens from all chunks."""
        return sum(cr.prompt_tokens for cr in self.chunk_results)

    @property
    def total_completion_tokens(self) -> int:
        """Sum of completion tokens from all chunks."""
        return sum(cr.completion_tokens for cr in self.chunk_results)
