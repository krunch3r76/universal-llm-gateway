"""
Model execution configuration bridge.

Reads execution hints from ModelRef and creates matching ChunkStrategy.

Invariant: chunk_size >= 1
Invariant: sequential ⟹ max_concurrent = 1
"""

import sys
from dataclasses import dataclass

from .strategies import (
    ByFieldThenBySize,
    BySize,
    ChunkStrategy,
    Individual,
)

# Sentinel: model imposes no cap — step chunk_size drives the batch size.
_UNLIMITED: int = sys.maxsize


@dataclass(slots=True, kw_only=True)
class ModelExecutionConfig:
    """
    Execution configuration extracted from ModelRef.execution field.

    Invariant: chunk_size >= 1
    Invariant: sequential ⟹ max_concurrent = 1
    """

    chunk_size: int = _UNLIMITED
    max_concurrent: int | None = None
    timeout_ms: int | None = None
    sequential: bool = False

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {self.chunk_size}")
        if self.sequential:
            self.max_concurrent = 1


def get_execution_config(model_config: object) -> ModelExecutionConfig:
    """
    Extract execution config from a ModelRef.

    No ``execution`` block ⟹ model imposes no cap (chunk_size = sys.maxsize).
    Step-level chunk_size then drives the effective batch size.

    Args:
        model_config: Resolved ModelRef with optional `execution` dict field

    Returns:
        ModelExecutionConfig from model definition or defaults
    """
    execution = getattr(model_config, "execution", None) or {}

    return ModelExecutionConfig(
        chunk_size=execution.get("chunk_size", _UNLIMITED),
        max_concurrent=execution.get("max_concurrent", None),
        timeout_ms=execution.get("timeout_ms", None),
        sequential=execution.get("sequential", False),
    )


def create_chunk_strategy(
    config: ModelExecutionConfig, use_domain_chunking: bool = False
) -> ChunkStrategy:
    """
    Create chunk strategy from execution config.

    Mapping:
        chunk_size = 1 → Individual()
        use_domain_chunking + chunk_size > 1 → ByFieldThenBySize("domain", chunk_size)
        chunk_size > 1 → BySize(chunk_size)

    Args:
        config: Model execution configuration
        use_domain_chunking: If True, group by domain then split by size
    """
    if config.chunk_size == 1:
        return Individual()

    if use_domain_chunking:
        return ByFieldThenBySize("domain", config.chunk_size)

    return BySize(config.chunk_size)
