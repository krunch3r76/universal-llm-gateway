"""
Chunked model execution infrastructure.

Provides configurable chunking + model distribution with per-chunk fallback.

Usage:
    from systems.pipeline.core.execution.chunked import (
        ChunkedModelExecutor,
        BySize,
        ByField,
        RoundRobin,
        CallableFallback,
    )

    executor = ChunkedModelExecutor(
        model_selector=RoundRobin(["phi", "qwen"]),
        chunk_strategy=BySize(10),
        fallback_handler=CallableFallback(heuristic_classify),
    )
    result = await executor.execute(items, process_fn)

Token Counting:
    Process functions can return ProcessResult for token tracking:

    from systems.pipeline.core.execution.chunked import ProcessResult

    async def process_chunk(chunk, model_id):
        # ... call model ...
        return ProcessResult(
            results=[...],
            prompt_tokens=100,
            completion_tokens=50,
        )

    result = await executor.execute(items, process_chunk)
    print(
        f"Total: {result.total_prompt_tokens} prompt, "
        f"{result.total_completion_tokens} completion"
    )

    Backward compatible: plain list return defaults tokens to 0.
"""

from .chunk_types import Chunk, ChunkedResult, ChunkResult, ProcessResult
from .executor import ChunkedModelExecutor
from .model_config import (
    ModelExecutionConfig,
    create_chunk_strategy,
    get_execution_config,
)
from .strategies import (
    # Chunk strategies
    ByChunkKey,
    ByField,
    ByFieldThenBySize,
    BySize,
    CallableFallback,
    ChunkStrategy,
    DefaultValueFallback,
    # Fallback handlers
    FallbackHandler,
    FirstAvailable,
    Individual,
    # Model selectors
    ModelSelector,
    RaiseFallback,
    RoundRobin,
    SkipFallback,
    Weighted,
)

__all__ = [
    # Executor
    "ChunkedModelExecutor",
    # Model execution config
    "ModelExecutionConfig",
    "get_execution_config",
    "create_chunk_strategy",
    # Types
    "Chunk",
    "ChunkResult",
    "ChunkedResult",
    "ProcessResult",
    # Chunk strategies
    "ChunkStrategy",
    "BySize",
    "ByField",
    "ByFieldThenBySize",
    "Individual",
    # Model selectors
    "ModelSelector",
    "RoundRobin",
    "FirstAvailable",
    "ByChunkKey",
    "Weighted",
    # Fallback handlers
    "FallbackHandler",
    "RaiseFallback",
    "SkipFallback",
    "DefaultValueFallback",
    "CallableFallback",
]
