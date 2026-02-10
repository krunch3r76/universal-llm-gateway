"""
Chunked model executor with parallel dispatch and per-chunk fallback.

Pattern: Template Method — fixed algorithm structure with strategy hooks.

Invariant: ∀ item: item processed exactly once
Invariant: ∀ chunk_error: fallback invoked ∨ error propagated
Invariant: results merged in original item order
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .chunk_types import Chunk, ChunkedResult, ChunkResult, ProcessResult
from .strategies import (
    BySize,
    ChunkStrategy,
    FallbackHandler,
    ModelSelector,
    RaiseFallback,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ChunkedModelExecutor:
    """
    Execute processing across items with chunking and model distribution.

    Usage:
        executor = ChunkedModelExecutor(
            model_selector=RoundRobin(pool=["phi", "qwen", "llama"]),
            chunk_strategy=BySize(chunk_size=10),
            fallback_handler=CallableFallback(heuristic_classify),
        )
        result = await executor.execute(
            items=claims,
            process_fn=classify_chunk,  # async (chunk, model_id) -> list[result]
        )

    Invariant: len(result.results) == len(items)
    """

    def __init__(
        self,
        model_selector: ModelSelector,
        chunk_strategy: ChunkStrategy | None = None,
        fallback_handler: FallbackHandler | None = None,
        max_concurrent: int | None = None,
        timeout_per_chunk_ms: float | None = None,
    ):
        """
        Args:
            model_selector: Strategy for assigning models to chunks (required).
                Use FirstAvailable([model_id]) for single-model execution.
            chunk_strategy: Strategy for partitioning items. Default: BySize(10).
            fallback_handler: Error handler for failed chunks. Default: RaiseFallback.
            max_concurrent: Max parallel chunk processing. None = unlimited.
            timeout_per_chunk_ms: Per-chunk timeout. None = no timeout.

        Raises:
            ValueError: If max_concurrent <= 0 or timeout_per_chunk_ms < 0.
        """
        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")
        if timeout_per_chunk_ms is not None and timeout_per_chunk_ms < 0:
            raise ValueError(
                f"timeout_per_chunk_ms must be >= 0, got {timeout_per_chunk_ms}"
            )

        self._chunk_strategy = chunk_strategy or BySize(chunk_size=10)
        self._model_selector = model_selector
        self._fallback_handler = fallback_handler or RaiseFallback()
        self._max_concurrent = max_concurrent
        self._timeout_per_chunk_ms = timeout_per_chunk_ms

    async def execute(
        self,
        items: list[Any],
        process_fn: Callable[[Chunk, str], Coroutine[Any, Any, list[Any]]],
    ) -> ChunkedResult:
        """
        Execute chunked processing with parallel dispatch.

        Args:
            items: Items to process
            process_fn: async (chunk, model_id) -> list[result]
                Must return one result per item in chunk.items

        Returns:
            ChunkedResult with merged results in original order

        Invariant: len(result.results) == len(items)
        """
        if not items:
            return ChunkedResult(
                results=[],
                chunk_results=[],
                total_latency_ms=0.0,
            )

        start_time = time.time()

        # 1. Chunk items
        chunks = self._chunk_strategy.chunk(items)

        logger.debug(f"ChunkedModelExecutor: {len(items)} items → {len(chunks)} chunks")

        # 2. Pre-compute model assignments
        model_assignments: dict[int, str] = {}
        for chunk in chunks:
            model_assignments[chunk.index] = self._model_selector.select(chunk)

        # 3. Dispatch chunks in parallel
        async def process_chunk(chunk: Chunk) -> ChunkResult:
            """Process single chunk with error handling."""
            if chunk.index not in model_assignments:
                raise ValueError(
                    f"No model assigned for chunk {chunk.index}. "
                    f"Configure model_selector or ensure all chunks have assignments."
                )
            model_id = model_assignments[chunk.index]
            chunk_start = time.time()
            fallback_used = False
            error_msg = None

            prompt_tokens = 0
            completion_tokens = 0

            try:
                if self._timeout_per_chunk_ms:
                    raw_result = await asyncio.wait_for(
                        process_fn(chunk, model_id),
                        timeout=self._timeout_per_chunk_ms / 1000.0,
                    )
                else:
                    raw_result = await process_fn(chunk, model_id)

                # Handle both list and ProcessResult return types
                if isinstance(raw_result, ProcessResult):
                    results = raw_result.results
                    prompt_tokens = raw_result.prompt_tokens
                    completion_tokens = raw_result.completion_tokens
                else:
                    results = raw_result

            except Exception as e:
                # TimeoutError has empty str() - provide explicit message
                if isinstance(e, TimeoutError):
                    timeout_sec = self._timeout_per_chunk_ms / 1000.0
                    error_msg = f"Timeout after {timeout_sec:.1f}s"
                else:
                    error_msg = str(e) or type(e).__name__

                logger.warning(
                    f"ChunkedModelExecutor: chunk {chunk.index} failed "
                    f"(model={model_id}): {error_msg}"
                )

                # Attempt fallback
                raw_result = await self._fallback_handler.handle(chunk, e, process_fn)
                fallback_used = True
                if isinstance(raw_result, ProcessResult):
                    results = raw_result.results
                    prompt_tokens = raw_result.prompt_tokens
                    completion_tokens = raw_result.completion_tokens
                else:
                    results = raw_result

            # Validate result count (hard failure - contract violation, not recoverable)
            if len(results) != len(chunk.items):
                raise ValueError(
                    f"process_fn returned {len(results)} results for "
                    f"{len(chunk.items)} items in chunk {chunk.index}. "
                    f"This is a contract violation, not a recoverable error."
                )

            chunk_latency = (time.time() - chunk_start) * 1000

            return ChunkResult(
                chunk_index=chunk.index,
                item_indices=chunk.item_indices,
                results=results,
                model_used=model_id,
                latency_ms=chunk_latency,
                fallback_used=fallback_used,
                error=error_msg,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        # Execute with optional concurrency limit
        if self._max_concurrent:
            semaphore = asyncio.Semaphore(self._max_concurrent)

            async def limited_process(chunk: Chunk) -> ChunkResult:
                async with semaphore:
                    return await process_chunk(chunk)

            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(limited_process(c)) for c in chunks]
        else:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(process_chunk(c)) for c in chunks]

        chunk_results = [t.result() for t in tasks]

        # 4. Merge results in original order
        results_by_index: dict[int, Any] = {}
        models_used: set[str] = set()
        fallback_count = 0

        for cr in chunk_results:
            models_used.add(cr.model_used)
            if cr.fallback_used:
                fallback_count += 1

            for item_idx, result in zip(cr.item_indices, cr.results, strict=True):
                results_by_index[item_idx] = result

        # Reconstruct in original order
        merged_results = [results_by_index[i] for i in range(len(items))]

        total_latency = (time.time() - start_time) * 1000

        logger.info(
            f"ChunkedModelExecutor: {len(items)} items, {len(chunks)} chunks, "
            f"{len(models_used)} models, {fallback_count} fallbacks, "
            f"{total_latency:.0f}ms"
        )

        return ChunkedResult(
            results=merged_results,
            chunk_results=chunk_results,
            total_latency_ms=total_latency,
            models_used=models_used,
            fallback_count=fallback_count,
        )
