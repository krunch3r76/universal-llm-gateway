"""
Simple integration test for ChunkedModelExecutor.

Run: python test_integration_simple.py
(from services/universal-stargate/systems/pipeline/core/execution/chunked/)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure we can import from parent directories
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))

# Now import using absolute paths
from systems.pipeline.core.execution.chunked.chunk_types import Chunk
from systems.pipeline.core.execution.chunked.executor import ChunkedModelExecutor
from systems.pipeline.core.execution.chunked.strategies import (
    ByField,
    BySize,
    FirstAvailable,
    Individual,
    RoundRobin,
    SkipFallback,
    Weighted,
)


async def test_basic_chunking():
    """Test basic chunked execution with round-robin."""
    items = [{"id": i, "text": f"Statement {i}"} for i in range(25)]

    executor = ChunkedModelExecutor(
        model_selector=RoundRobin(["phi", "qwen", "llama"]),
        chunk_strategy=BySize(chunk_size=10),
    )

    async def process_fn(chunk: Chunk, model_id: str) -> list[dict]:
        # Simulate classification
        return [{"domain": "test", "model": model_id} for _ in chunk.items]

    result = await executor.execute(items, process_fn)

    assert len(result.results) == 25, f"Expected 25, got {len(result.results)}"
    assert len(result.chunk_results) == 3, (
        f"Expected 3 chunks, got {len(result.chunk_results)}"
    )
    assert result.models_used == {
        "phi",
        "qwen",
        "llama",
    }, f"Models: {result.models_used}"
    print("✓ test_basic_chunking passed")


async def test_field_chunking():
    """Test chunking by field value."""
    items = [
        {"id": 1, "domain": "math", "text": "2+2=4"},
        {"id": 2, "domain": "math", "text": "3*3=9"},
        {"id": 3, "domain": "history", "text": "WW2 ended 1945"},
        {"id": 4, "domain": "history", "text": "Moon landing 1969"},
        {"id": 5, "domain": "physics", "text": "E=mc²"},
    ]

    executor = ChunkedModelExecutor(
        model_selector=FirstAvailable(["phi"]),
        chunk_strategy=ByField("domain"),
    )

    async def process_fn(chunk: Chunk, model_id: str) -> list[dict]:
        return [{"processed": True, "chunk_key": chunk.key} for _ in chunk.items]

    result = await executor.execute(items, process_fn)

    assert len(result.results) == 5
    assert len(result.chunk_results) == 3  # math, history, physics
    print("✓ test_field_chunking passed")


async def test_fallback_on_error():
    """Test fallback handler invocation."""
    items = [{"id": i} for i in range(5)]
    call_count = {"process": 0, "fallback": 0}

    def fallback_fn(chunk: Chunk) -> list[dict]:
        call_count["fallback"] += 1
        return [{"fallback": True} for _ in chunk.items]

    executor = ChunkedModelExecutor(
        model_selector=FirstAvailable(["phi"]),
        chunk_strategy=Individual(),  # 1 item per chunk
        fallback_handler=SkipFallback(),  # Returns None on error
    )

    async def process_fn(chunk: Chunk, model_id: str) -> list[dict]:
        call_count["process"] += 1
        if chunk.index == 2:  # Fail on third chunk
            raise ValueError("Simulated failure")
        return [{"success": True} for _ in chunk.items]

    result = await executor.execute(items, process_fn)

    assert len(result.results) == 5
    assert result.fallback_count == 1
    assert result.results[2] is None  # SkipFallback returns None
    print("✓ test_fallback_on_error passed")


async def test_weighted_selector():
    """Test weighted model selection."""
    items = [{"id": i} for i in range(20)]

    executor = ChunkedModelExecutor(
        model_selector=Weighted(
            ["fast", "slow"],
            weights={"fast": 3.0, "slow": 1.0},  # 3:1 ratio
        ),
        chunk_strategy=BySize(chunk_size=1),  # 20 chunks
    )

    model_counts = {"fast": 0, "slow": 0}

    async def process_fn(chunk: Chunk, model_id: str) -> list[dict]:
        model_counts[model_id] = model_counts.get(model_id, 0) + 1
        return [{"model": model_id} for _ in chunk.items]

    await executor.execute(items, process_fn)

    # With 3:1 weighting, expect ~75% fast, ~25% slow (with rounding)
    assert model_counts["fast"] > model_counts["slow"], f"Counts: {model_counts}"
    print(
        f"✓ test_weighted_selector passed "
        f"(fast={model_counts['fast']}, slow={model_counts['slow']})"
    )


async def test_concurrency_limit():
    """Test max_concurrent limits parallel execution."""
    items = [{"id": i} for i in range(10)]
    concurrent_count = {"current": 0, "max": 0}

    executor = ChunkedModelExecutor(
        model_selector=FirstAvailable(["phi"]),
        chunk_strategy=Individual(),  # 10 chunks
        max_concurrent=3,  # Only 3 at a time
    )

    async def process_fn(chunk: Chunk, model_id: str) -> list[dict]:
        concurrent_count["current"] += 1
        concurrent_count["max"] = max(
            concurrent_count["max"], concurrent_count["current"]
        )
        await asyncio.sleep(0.01)  # Simulate work
        concurrent_count["current"] -= 1
        return [{"done": True} for _ in chunk.items]

    await executor.execute(items, process_fn)

    assert concurrent_count["max"] <= 3, f"Max concurrent: {concurrent_count['max']}"
    print(f"✓ test_concurrency_limit passed (max_concurrent={concurrent_count['max']})")


async def test_missing_selector_raises():
    """Test that missing model_selector raises TypeError (required positional arg)."""
    try:
        ChunkedModelExecutor(chunk_strategy=BySize(chunk_size=10))  # type: ignore
        assert False, "Should have raised TypeError"
    except TypeError as e:
        assert "model_selector" in str(e)
        print("✓ test_missing_selector_raises passed")


async def test_zero_weight_raises():
    """Test that zero total weight raises ValueError."""
    try:
        Weighted(["a", "b"], weights={"a": 0, "b": 0})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "positive" in str(e).lower()
        print("✓ test_zero_weight_raises passed")


async def test_negative_weight_raises():
    """Test that negative weight raises ValueError."""
    try:
        Weighted(["a", "b"], weights={"a": -1.0, "b": 2.0})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "must be >= 0" in str(e)
        print("✓ test_negative_weight_raises passed")


async def test_invalid_max_concurrent_raises():
    """Test that max_concurrent <= 0 raises ValueError."""
    try:
        ChunkedModelExecutor(
            model_selector=FirstAvailable(["phi"]),
            max_concurrent=0,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "max_concurrent must be > 0" in str(e)
        print("✓ test_invalid_max_concurrent_raises passed")


async def test_negative_timeout_raises():
    """Test that negative timeout_per_chunk_ms raises ValueError."""
    try:
        ChunkedModelExecutor(
            model_selector=FirstAvailable(["phi"]),
            timeout_per_chunk_ms=-100,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "timeout_per_chunk_ms must be >= 0" in str(e)
        print("✓ test_negative_timeout_raises passed")


async def main():
    """Run all integration tests."""
    print("\n=== ChunkedModelExecutor Integration Tests ===\n")

    # Validation tests
    await test_missing_selector_raises()
    await test_zero_weight_raises()
    await test_negative_weight_raises()
    await test_invalid_max_concurrent_raises()
    await test_negative_timeout_raises()

    # Functional tests
    await test_basic_chunking()
    await test_field_chunking()
    await test_fallback_on_error()
    await test_weighted_selector()
    await test_concurrency_limit()

    print("\n=== All tests passed ===\n")


if __name__ == "__main__":
    asyncio.run(main())
