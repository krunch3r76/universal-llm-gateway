"""
Timeout wrapper for async execution.

Async-safety:
- asyncio.wait_for() is async-safe
- Cancellation propagates correctly to wrapped coroutine
- No cleanup required (wrapped fn handles its own resources)

Timeout Hierarchy:
Step timeout wraps the entire execution including all retries:
  execute_with_step_timeout(
    execute_with_retry(
      execute_with_handler_timeout(handler)
    )
  )

Cancellation behavior:
- When step timeout expires, asyncio cancels the entire task tree
- This includes any in-progress handler execution or retry delay
- Handler should handle CancelledError for cleanup if needed
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import HandlerTimeoutError, StepTimeoutError

T = TypeVar("T")


async def execute_with_step_timeout[T](
    fn: Callable[[], Awaitable[T]],
    timeout_seconds: float,
    step_name: str,
) -> T:
    """
    Execute async function with total step timeout.

    This wraps the entire step execution (including retries).

    Raises:
        StepTimeoutError: If execution exceeds timeout_seconds
    """
    try:
        return await asyncio.wait_for(fn(), timeout=timeout_seconds)
    except TimeoutError:
        raise StepTimeoutError(
            step_name=step_name,
            timeout_seconds=timeout_seconds,
        )


async def execute_with_handler_timeout[T](
    fn: Callable[[], Awaitable[T]],
    timeout_seconds: float,
    step_name: str,
    attempt: int = 1,
) -> T:
    """
    Execute async function with handler-level timeout.

    This wraps a single handler execution (one retry attempt).

    Raises:
        HandlerTimeoutError: If execution exceeds timeout_seconds
    """
    try:
        return await asyncio.wait_for(fn(), timeout=timeout_seconds)
    except TimeoutError:
        raise HandlerTimeoutError(
            step_name=step_name,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
        )
