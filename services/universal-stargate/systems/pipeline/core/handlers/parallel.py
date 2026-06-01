"""
Parallel model call utilities for pipeline handlers.

Provides reusable patterns for concurrent LLM requests with:
- Optional FIFO-fair concurrency gating (FifoCapacityGate)
- Consistent error handling
- Result ordering preservation
- Structured logging

Invariants:
    ∀ item: isolated_request (no cross-contamination)
    ∀ result: order_preserved (results align with input order)
    ∀ failure: logged_and_filtered (None results excluded)
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from universal_concurrency import FifoCapacityGate

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


async def parallel_model_calls[T, R](
    items: list[T],
    call_fn: Callable[[T], Awaitable[R | None]],
    *,
    max_concurrency: int | None = None,
    description: str = "parallel calls",
) -> list[R]:
    """
    Execute model calls in parallel with optional concurrency limit.

    Args:
        items: Input items to process (one request per item)
        call_fn: Async function that processes one item, returns result or None
            on failure
        max_concurrency: Optional limit on concurrent requests (None = unlimited)
        description: Label for logging

    Returns:
        List of successful results (None values filtered out).
        Order corresponds to input items (with gaps for failures).

    Example:
        evaluations = await parallel_model_calls(
            statements,
            lambda stmt: self._verify_single(stmt, model_id, context),
            max_concurrency=10,
            description="math verification",
        )
    """
    if not items:
        return []

    gate = (
        FifoCapacityGate(max_concurrency, gate_id=description)
        if max_concurrency
        else None
    )

    async def bounded_call(idx: int, item: T) -> R | None:
        try:
            if gate:
                await gate.acquire(str(idx))
                try:
                    return await call_fn(item)
                finally:
                    await gate.release()
            return await call_fn(item)
        except Exception as e:
            logger.warning(f"{description}: call failed: {e}")
            return None

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(bounded_call(i, item)) for i, item in enumerate(items)]

    results: list[R] = []
    for task in tasks:
        result = task.result()
        if result is not None:
            results.append(result)

    logger.debug(f"{description}: {len(results)}/{len(items)} succeeded")
    return results


async def parallel_model_calls_with_index[T, R](
    items: list[T],
    call_fn: Callable[[int, T], Awaitable[R | None]],
    *,
    max_concurrency: int | None = None,
    description: str = "parallel calls",
) -> list[tuple[int, R]]:
    """
    Like parallel_model_calls but provides index to call_fn and returns
    (index, result) pairs.

    Useful when result ordering matters and you need to track which items
    succeeded.

    Example:
        indexed_results = await parallel_model_calls_with_index(
            statements,
            lambda i, stmt: self._verify_single(i, stmt, model_id, context),
            description="indexed verification",
        )
        # indexed_results = [(0, result0), (2, result2), ...]  # index 1 failed
    """
    if not items:
        return []

    gate = (
        FifoCapacityGate(max_concurrency, gate_id=description)
        if max_concurrency
        else None
    )

    async def bounded_call(idx: int, item: T) -> tuple[int, R] | None:
        try:
            if gate:
                await gate.acquire(str(idx))
                try:
                    result = await call_fn(idx, item)
                finally:
                    await gate.release()
            else:
                result = await call_fn(idx, item)
            return (idx, result) if result is not None else None
        except Exception as e:
            logger.warning(f"{description}[{idx}]: call failed: {e}")
            return None

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(bounded_call(i, item)) for i, item in enumerate(items)]

    results: list[tuple[int, R]] = []
    for task in tasks:
        result = task.result()
        if result is not None:
            results.append(result)

    logger.debug(f"{description}: {len(results)}/{len(items)} succeeded")
    return results
