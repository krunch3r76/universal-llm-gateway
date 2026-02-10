"""
Configuration for federation load orchestration.

INVARIANT: ∀ config values: sensible defaults exist
INVARIANT: Missing config key ⟹ default, not error

TIMEOUT LAYERING:
- load_timeout: Wall-clock authority for entire load operation
- coalesce_wait_timeout: Max time followers wait (>= load_timeout + buffer)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OrchestrationConfig:
    """
    Immutable configuration for federation load orchestration.

    Frozen to prevent accidental mutation after initialization.

    Defaults chosen for:
    - load_timeout=120: Allows large model loads
    - telemetry_staleness_threshold=10: Conservative freshness
    - retry=2 attempts: Handles transient failures without excessive delay
    - backoff=1.5x: Moderate exponential growth
    """

    # === Timeouts (seconds) ===
    load_timeout: int = 180
    """Max seconds for model load. Increased for large models like qwen3-32B."""

    coalesce_wait_timeout: int = 210
    """Max seconds follower waits (>= load_timeout + 30s RPC buffer)."""

    # === Telemetry Trust (seconds) ===
    telemetry_staleness_threshold: float = 10.0
    """Max telemetry age in seconds before forcing explicit load."""

    # === Retry Policy ===
    load_retry_count: int = 2
    """Number of retry attempts on 5xx errors."""

    load_retry_delay: float = 1.0
    """Initial delay between retries in seconds."""

    load_retry_backoff: float = 1.5
    """Exponential backoff multiplier."""

    load_retry_max_delay: float = 10.0
    """Maximum delay cap in seconds."""

    load_retry_jitter: float = 0.1
    """
    Jitter factor (0.0-1.0) to add randomness to retry delays.

    Prevents thundering herd.
    """

    def __post_init__(self) -> None:
        """Runtime assertions for configuration constraints."""
        if self.coalesce_wait_timeout < self.load_timeout + 30:
            raise ValueError(
                f"coalesce_wait_timeout ({self.coalesce_wait_timeout}) must be >= "
                f"load_timeout ({self.load_timeout}) + 30s buffer. "
                f"Followers would timeout before primary completes."
            )
        if self.load_retry_count < 0:
            raise ValueError(
                f"load_retry_count must be >= 0, got {self.load_retry_count}"
            )
        if self.load_retry_delay <= 0:
            raise ValueError(
                f"load_retry_delay must be > 0, got {self.load_retry_delay}"
            )
        if self.load_retry_backoff < 1.0:
            raise ValueError(
                f"load_retry_backoff must be >= 1.0, got {self.load_retry_backoff}"
            )
        if self.load_retry_max_delay <= 0:
            raise ValueError(
                f"load_retry_max_delay must be > 0, got {self.load_retry_max_delay}"
            )
        if not 0.0 <= self.load_retry_jitter <= 1.0:
            raise ValueError(
                f"load_retry_jitter must be 0.0-1.0, got {self.load_retry_jitter}"
            )

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> OrchestrationConfig:
        """
        Create from config dict with defaults for missing keys.

        INVARIANT: Never raises on missing keys.

        Args:
            config: Full configuration dictionary (e.g., from YAML)

        Returns:
            OrchestrationConfig with values from dict or defaults
        """
        orch = config.get("federation", {}).get("orchestration", {})
        return cls(
            load_timeout=orch.get("load_timeout", 180),
            coalesce_wait_timeout=orch.get("coalesce_wait_timeout", 210),
            telemetry_staleness_threshold=orch.get(
                "telemetry_staleness_threshold", 10.0
            ),
            load_retry_count=orch.get("load_retry_count", 2),
            load_retry_delay=orch.get("load_retry_delay", 1.0),
            load_retry_backoff=orch.get("load_retry_backoff", 1.5),
            load_retry_max_delay=orch.get("load_retry_max_delay", 10.0),
            load_retry_jitter=orch.get("load_retry_jitter", 0.1),
        )

    def calculate_retry_delay(self, retry_index: int) -> float:
        """
        Calculate delay for retry attempt with exponential backoff + jitter.

        Formula:
            min(delay * backoff^(retry_index-1), max_delay)
            * (1 + random(-jitter, +jitter))

        NAMING CONVENTION (CRITICAL):
        - retry_index: 1-indexed for each attempt (1 = first attempt, 2 = first
          retry, etc.)
        - attempt_index: Same as retry_index (both 1-indexed)
        - Relationship: retry_index = attempt_index (simplified from previous
          off-by-one semantics)

        Jitter prevents thundering herd when multiple requests retry simultaneously.

        NOTE: Jitter is applied after capping, so final delay can exceed
        max_delay by up to jitter%. For jitter=0.1, max overshoot is 10%.

        Args:
            retry_index: 1-indexed attempt number (1 = first attempt, 2 = first
                retry, etc.)

        Returns:
            Delay in seconds, capped at max_delay, with jitter applied

        Raises:
            ValueError: If retry_index < 1

        Example:
            delay=1.0, backoff=1.5, max_delay=10.0, jitter=0.1
            retry_index=1: base=1.0s, with jitter=0.9-1.1s
            retry_index=2: base=1.5s, with jitter=1.35-1.65s
            retry_index=3: base=2.25s, with jitter=2.025-2.475s
        """
        if retry_index < 1:
            raise ValueError(f"retry_index must be >= 1, got {retry_index}")

        base_delay = self.load_retry_delay * (
            self.load_retry_backoff ** (retry_index - 1)
        )
        capped_delay = min(base_delay, self.load_retry_max_delay)

        # Apply jitter: multiply by (1 + random value in [-jitter, +jitter])
        jitter_factor = 1.0 + random.uniform(
            -self.load_retry_jitter, self.load_retry_jitter
        )
        final_delay = capped_delay * jitter_factor

        # Log if we hit the cap (observability)
        if base_delay > self.load_retry_max_delay:
            logger.debug(
                f"Retry delay capped: base={base_delay:.1f}s > "
                f"max={self.load_retry_max_delay}s"
            )

        return final_delay


# Default config instance (for convenience)
DEFAULT_ORCHESTRATION_CONFIG = OrchestrationConfig()
