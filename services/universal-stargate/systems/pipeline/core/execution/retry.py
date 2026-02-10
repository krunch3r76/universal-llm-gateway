"""
Retry policies with backoff strategies.

Async-safety note:
- ∀ state is per-call (no shared mutable state)
- Jitter uses random.uniform() which is NOT async-safe in tight loops
  but acceptable here (one call per retry attempt, not high-frequency)
- If handler caching is used, ensure handlers are stateless (see Phase 1 notes)

Backoff strategies:
- fixed: Always wait initial_interval_seconds
- linear: initial * attempt_number
- exponential: initial * (multiplier ** (attempt - 1))

Timeout Integration:
When wrapped in execute_with_step_timeout(), retry delays count against
the total step timeout. If step timeout expires during a retry delay,
asyncio cancels the sleep and raises StepTimeoutError.

Example timeline with step_timeout=10s, handler_timeout=3s:
  00:00 - Attempt 1 starts
  00:03 - Attempt 1 timeout
  00:03 - Retry delay 2s begins
  00:05 - Attempt 2 starts
  00:08 - Attempt 2 timeout
  00:08 - Retry delay 4s begins
  00:10 - Step timeout reached → StepTimeoutError
          (Attempt 3 never starts)
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """
    Retry configuration for step execution.

    Invariant: max_attempts ≥ 1 (at least one attempt)
    Invariant: initial_interval_seconds > 0
    Invariant: max_interval_seconds ≥ initial_interval_seconds
    """

    max_attempts: int = 1
    backoff_strategy: Literal["fixed", "linear", "exponential"] = "exponential"
    initial_interval_seconds: float = 1.0
    max_interval_seconds: float = 300.0
    multiplier: float = 2.0  # Only for exponential
    jitter: bool = True  # ±25% randomness

    # Exception filtering (by class name for YAML compatibility)
    retry_on: tuple[str, ...] = field(default=("Exception",))
    dont_retry_on: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        """Validate invariants."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_interval_seconds <= 0:
            raise ValueError("initial_interval_seconds must be > 0")
        if self.max_interval_seconds < self.initial_interval_seconds:
            raise ValueError("max_interval_seconds must be >= initial_interval_seconds")

    def should_retry(self, exception: Exception) -> bool:
        """
        Determine if exception is retryable.

        Logic:
        1. If dont_retry_on matches → don't retry
        2. If retry_on matches → retry
        3. Default → don't retry (fail-safe)
        """
        exc_mro_names = [c.__name__ for c in type(exception).__mro__]

        # Check blacklist first (exact match or inheritance)
        for pattern in self.dont_retry_on:
            if pattern in exc_mro_names:
                return False

        # Check whitelist (exact match or inheritance)
        for pattern in self.retry_on:
            if pattern in exc_mro_names:
                return True

        return False  # Default: don't retry unknown exceptions

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for given attempt (1-indexed).

        Returns delay in seconds with optional jitter.
        """
        match self.backoff_strategy:
            case "fixed":
                delay = self.initial_interval_seconds
            case "linear":
                delay = self.initial_interval_seconds * attempt
            case "exponential":
                delay = self.initial_interval_seconds * (
                    self.multiplier ** (attempt - 1)
                )
            case _:
                delay = self.initial_interval_seconds

        # Cap at max_interval
        delay = min(delay, self.max_interval_seconds)

        # Apply jitter (±25%)
        if self.jitter:
            delay *= random.uniform(0.75, 1.25)

        return delay


async def execute_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    step_name: str,
) -> T:
    """
    Execute async function with retry policy.

    Invariant: ∀ attempt ∈ [1, max_attempts],
               (exception ∧ should_retry) ⟹ delay_and_retry

    Async-safety:
    - fn() is called sequentially (no concurrent attempts)
    - asyncio.sleep() is async-safe
    - No shared state between attempts

    Returns:
        Result of fn() on success

    Raises:
        Last exception if all retries exhausted or non-retryable
    """
    last_exception: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except Exception as e:
            last_exception = e

            # Check if retryable
            if not policy.should_retry(e):
                logger.error(
                    "[%s] Non-retryable error (attempt %d/%d): %s: %s",
                    step_name,
                    attempt,
                    policy.max_attempts,
                    type(e).__name__,
                    e,
                )
                raise

            # Check if retries exhausted
            if attempt >= policy.max_attempts:
                logger.error(
                    "[%s] Max retries (%d) exhausted. Last error: %s: %s",
                    step_name,
                    policy.max_attempts,
                    type(e).__name__,
                    e,
                )
                raise

            # Calculate backoff and retry
            delay = policy.calculate_delay(attempt)
            logger.warning(
                "[%s] Attempt %d/%d failed (%s: %s). Retrying in %.2fs...",
                step_name,
                attempt,
                policy.max_attempts,
                type(e).__name__,
                e,
                delay,
            )
            # Note: If wrapped in execute_with_step_timeout, asyncio.TimeoutError
            # from the outer timeout will cancel this entire function, including
            # any in-progress sleep(). This is the desired behavior - step timeout
            # is absolute.
            await asyncio.sleep(delay)

    # Should never reach here
    assert last_exception is not None
    raise last_exception
