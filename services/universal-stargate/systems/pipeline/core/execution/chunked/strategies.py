"""
Chunking and model selection strategies.

Pattern: Strategy — interchangeable algorithms for chunking and selection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from itertools import batched
from typing import Any

from .chunk_types import Chunk

# =============================================================================
# Chunk Strategies
# =============================================================================


class ChunkStrategy(ABC):
    """Base class for chunking strategies."""

    @abstractmethod
    def chunk(self, items: list[Any]) -> list[Chunk]:
        """
        Partition items into chunks.

        Invariant: ∀ item ∈ items: ∃! chunk containing item
        """
        ...


class BySize(ChunkStrategy):
    """Chunk by fixed size."""

    def __init__(self, chunk_size: int):
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        self._chunk_size = chunk_size

    def chunk(self, items: list[Any]) -> list[Chunk]:
        chunks = []
        for chunk_idx, batch in enumerate(batched(enumerate(items), self._chunk_size)):
            batch_list = list(batch)
            indices = [i for i, _ in batch_list]
            chunk_items = [item for _, item in batch_list]
            chunks.append(
                Chunk(
                    index=chunk_idx,
                    items=chunk_items,
                    item_indices=indices,
                )
            )
        return chunks


class ByField(ChunkStrategy):
    """Chunk by field value (domain, originator, etc.)."""

    def __init__(
        self, field_name: str, field_getter: Callable[[Any], str] | None = None
    ):
        self._field_name = field_name
        self._field_getter = field_getter or (lambda x: x.get(field_name, "unknown"))

    def chunk(self, items: list[Any]) -> list[Chunk]:
        groups: dict[str, list[tuple[int, Any]]] = defaultdict(list)

        for idx, item in enumerate(items):
            key = self._field_getter(item)
            groups[key].append((idx, item))

        chunks = []
        for chunk_idx, (key, group) in enumerate(groups.items()):
            indices = [i for i, _ in group]
            chunk_items = [item for _, item in group]
            chunks.append(
                Chunk(
                    index=chunk_idx,
                    items=chunk_items,
                    item_indices=indices,
                    key=key,
                )
            )
        return chunks


class Individual(ChunkStrategy):
    """One item per chunk (maximum parallelism)."""

    def chunk(self, items: list[Any]) -> list[Chunk]:
        return [
            Chunk(index=idx, items=[item], item_indices=[idx])
            for idx, item in enumerate(items)
        ]


class ByFieldThenBySize(ChunkStrategy):
    """
    Composite strategy: group by field, then split each group by size.

    Use case: Domain-aware chunking with size limits (e.g., verification).
    Ensures homogeneous domains per chunk while respecting model batch limits.

    Invariant: ∀ chunk: |chunk.items| ≤ chunk_size
    Invariant: ∀ chunk: ∀ i, j ∈ chunk.items: field_getter(i) = field_getter(j)
    """

    def __init__(
        self,
        field_name: str,
        chunk_size: int,
        field_getter: Callable[[Any], str] | None = None,
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        self._field_name = field_name
        self._chunk_size = chunk_size
        self._field_getter = field_getter or (lambda x: x.get(field_name, "unknown"))

    def chunk(self, items: list[Any]) -> list[Chunk]:
        # First, group by field (domain homogeneity)
        groups: dict[str, list[tuple[int, Any]]] = defaultdict(list)

        for idx, item in enumerate(items):
            key = self._field_getter(item)
            groups[key].append((idx, item))

        # Then, split each group into size-limited chunks
        chunks = []
        for group_key, group_items in groups.items():
            # Split group into batches of chunk_size
            for batch in batched(group_items, self._chunk_size):
                batch_list = list(batch)
                indices = [i for i, _ in batch_list]
                chunk_items = [item for _, item in batch_list]
                chunks.append(
                    Chunk(
                        index=len(chunks),  # Global chunk index
                        items=chunk_items,
                        item_indices=indices,
                        key=group_key,  # Preserve domain key
                    )
                )

        return chunks


# =============================================================================
# Model Selection Strategies
# =============================================================================


class ModelSelector(ABC):
    """Base class for model selection strategies."""

    def __init__(self, pool: list[str]):
        if not pool:
            raise ValueError("model pool cannot be empty")
        self._pool = pool

    @abstractmethod
    def select(self, chunk: Chunk) -> str:
        """Select model for a chunk."""
        ...

    @property
    def pool(self) -> list[str]:
        return self._pool


class RoundRobin(ModelSelector):
    """Rotate through models by chunk index."""

    def select(self, chunk: Chunk) -> str:
        return self._pool[chunk.index % len(self._pool)]


class FirstAvailable(ModelSelector):
    """Always use first model in pool."""

    def select(self, chunk: Chunk) -> str:
        return self._pool[0]


class ByChunkKey(ModelSelector):
    """
    Select based on chunk key with exclude-self option.

    Useful for cross-verification where originator should not verify own output.
    """

    def __init__(self, pool: list[str], exclude_self: bool = False):
        super().__init__(pool)
        self._exclude_self = exclude_self

    def select(self, chunk: Chunk) -> str:
        candidates = self._pool

        if self._exclude_self and chunk.key:
            candidates = [m for m in self._pool if m != chunk.key]
            if not candidates:
                # Fallback to originator if pool exhausted
                candidates = [chunk.key] if chunk.key in self._pool else self._pool

        # Round-robin among remaining candidates
        return candidates[chunk.index % len(candidates)]


class Weighted(ModelSelector):
    """
    Select based on weights (higher weight = more chunks).

    Weights are relative throughput indicators.
    Example: phi=1.0, qwen=0.8, llama=1.2
    """

    def __init__(self, pool: list[str], weights: dict[str, float] | None = None):
        super().__init__(pool)
        self._weights = weights or {m: 1.0 for m in pool}

        # Validate individual weights (negative weights are invalid)
        for model in pool:
            weight = self._weights.get(model, 1.0)
            if weight < 0:
                raise ValueError(
                    f"Weight for model '{model}' must be >= 0, got {weight}"
                )

        # Validate total weight
        total_weight = sum(self._weights.get(m, 1.0) for m in pool)
        if total_weight <= 0:
            raise ValueError(
                f"Total weight must be positive, got {total_weight}. "
                f"Weights: {self._weights}"
            )

        # Precompute assignment sequence based on weights
        self._sequence = []
        for model in pool:
            weight = self._weights.get(model, 1.0)
            count = max(1, round(10 * weight / total_weight))  # Normalize to ~10 slots
            self._sequence.extend([model] * count)

    def select(self, chunk: Chunk) -> str:
        return self._sequence[chunk.index % len(self._sequence)]


# =============================================================================
# Fallback Handlers
# =============================================================================


class FallbackHandler(ABC):
    """Base class for handling chunk processing errors."""

    @abstractmethod
    async def handle(
        self,
        chunk: Chunk,
        error: Exception,
        process_fn: Callable,
    ) -> list[Any]:
        """
        Handle chunk processing failure.

        Returns:
            Results for all items in chunk (same length as chunk.items)

        Raises:
            Exception if fallback also fails
        """
        ...


class RaiseFallback(FallbackHandler):
    """Re-raise errors (no fallback)."""

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        raise error


class SkipFallback(FallbackHandler):
    """Return None for all items in failed chunk."""

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return [None] * len(chunk.items)


class DefaultValueFallback(FallbackHandler):
    """Return default value for all items in failed chunk."""

    def __init__(self, default_factory: Callable[[], Any]):
        self._default_factory = default_factory

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return [self._default_factory() for _ in chunk.items]


class CallableFallback(FallbackHandler):
    """Call custom fallback function for failed chunk."""

    def __init__(self, fallback_fn: Callable[[Chunk], list[Any]]):
        self._fallback_fn = fallback_fn

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return self._fallback_fn(chunk)
