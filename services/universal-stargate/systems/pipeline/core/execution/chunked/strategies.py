"""
Chunking and model selection strategies.

Pattern: Strategy — interchangeable algorithms for chunking and selection.

Three pluggable families consumed by ``ChunkedModelExecutor``: ``ChunkStrategy``
subclasses partition items into ``Chunk`` objects (``BySize``, ``ByField``,
``Individual``, ``ByFieldThenBySize``); ``ModelSelector`` subclasses assign a model ID
per chunk (``RoundRobin``, ``FirstAvailable``, ``ByChunkKey``, ``Weighted``); and
``FallbackHandler`` subclasses produce results when a chunk fails (``RaiseFallback``,
``SkipFallback``, ``DefaultValueFallback``, ``CallableFallback``). ``model_config``'s
``create_chunk_strategy`` picks among the chunk strategies from ``chunk_size`` config.
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
    """Abstract base for strategies that partition an item list into indexed ``Chunk``
    objects.

    Subclasses implement ``chunk(items)``, which must place every item in exactly one
    chunk and record its original position in ``item_indices`` so
    ``ChunkedModelExecutor`` can merge per-chunk results back into input order.
    Instances are stateless and reusable.
    """

    @abstractmethod
    def chunk(self, items: list[Any]) -> list[Chunk]:
        """
        Partition items into chunks.

        Invariant: ∀ item ∈ items: ∃! chunk containing item
        """
        ...


class BySize(ChunkStrategy):
    """Split items into consecutive fixed-size chunks, preserving input order.

    The last chunk may be smaller. ``chunk_size=1`` is ``ChunkedModelExecutor``'s
    default strategy. Chunks carry no grouping ``key``. Raises ``ValueError`` on
    construction when ``chunk_size`` is below 1.
    """

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
    """Group items into one chunk per distinct field value (domain, originator, etc.).

    ``field_getter`` defaults to ``item.get(field_name, "unknown")`` so dict items work
    out of the box. Each chunk's ``key`` is the field value, which ``ByChunkKey`` can
    use for exclude-self model selection. Chunk sizes are unbounded; see
    ``ByFieldThenBySize``.
    """

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
    """Place each item in its own single-item chunk (maximum parallelism).

    Chunk ``index`` equals the item's original index and no ``key`` is set.
    ``create_chunk_strategy`` returns this when the configured ``chunk_size`` is 1, so
    every item gets an independent model call and independent fallback handling.
    """

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
    """Abstract base for strategies assigning a model ID from a fixed pool to each
    chunk.

    Constructed with a non-empty ``pool`` (``ValueError`` otherwise) and exposed via the
    ``pool`` property. ``ChunkedModelExecutor`` calls ``select(chunk)`` once per chunk
    before dispatch; selection is deterministic from chunk index/key, with no health
    checks.
    """

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
    """Assign models in rotation by chunk index: ``pool[chunk.index % len(pool)]``.

    Spreads chunks evenly across all pooled models for parallel throughput; ignores the
    chunk key. Deterministic, so the same chunking always yields the same assignment.
    """

    def select(self, chunk: Chunk) -> str:
        return self._pool[chunk.index % len(self._pool)]


class FirstAvailable(ModelSelector):
    """Assign every chunk to the first model in the pool, ignoring index and key.

    The single-model selector: ``ChunkedModelExecutor`` recommends
    ``FirstAvailable([model_id])`` when chunking is wanted without model distribution.
    Despite the name it performs no availability probing.
    """

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
    """Abstract base for handlers that recover results when a chunk's ``process_fn``
    fails.

    ``ChunkedModelExecutor`` calls ``handle(chunk, error, process_fn)`` after a chunk
    raises or times out, then marks the ``ChunkResult`` with ``fallback_used=True``. The
    handler must return one result per chunk item, or raise to propagate the failure.
    """

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
    """Fail-fast handler that re-raises the original chunk error instead of recovering.

    ``ChunkedModelExecutor``'s default when no ``fallback_handler`` is given, so any
    chunk failure (including per-chunk timeouts) propagates out of ``execute``.
    """

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        raise error


class SkipFallback(FallbackHandler):
    """Tolerant handler that returns ``None`` for every item of a failed chunk.

    Lets the remaining chunks succeed while leaving placeholder gaps in the merged
    results; callers must treat ``None`` as "no result". The error is otherwise
    discarded.
    """

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return [None] * len(chunk.items)


class DefaultValueFallback(FallbackHandler):
    """Tolerant handler filling every item of a failed chunk with a freshly built
    default.

    ``default_factory`` is called once per item, so mutable defaults (dicts, lists) are
    not shared between results. The original error is discarded.
    """

    def __init__(self, default_factory: Callable[[], Any]):
        self._default_factory = default_factory

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return [self._default_factory() for _ in chunk.items]


class CallableFallback(FallbackHandler):
    """Handler delegating a failed chunk to a caller-supplied synchronous
    ``fallback_fn``.

    ``fallback_fn(chunk)`` must return one result per chunk item, e.g. a heuristic
    classifier replacing a failed model call. The error and ``process_fn`` are not
    passed to it; exceptions it raises propagate from ``ChunkedModelExecutor.execute``.
    """

    def __init__(self, fallback_fn: Callable[[Chunk], list[Any]]):
        self._fallback_fn = fallback_fn

    async def handle(
        self, chunk: Chunk, error: Exception, process_fn: Callable
    ) -> list[Any]:
        return self._fallback_fn(chunk)
