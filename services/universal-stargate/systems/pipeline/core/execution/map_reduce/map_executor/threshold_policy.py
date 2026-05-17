"""Threshold policy for map iteration success requirements.

Encapsulates the success-count threshold semantics used by map execution
modes to decide partial-success acceptance vs. MapPartialFailureError.

The policy preserves the exact historical behavior for three cases:
- threshold=None (all iterations must succeed)
- threshold=int (absolute minimum successes required)
- threshold=float (fraction of total, with math.ceil for required count)
"""

from __future__ import annotations

import math


def success_count_meets_threshold(
    success_count: int,
    total: int,
    threshold: int | float | None,
) -> bool:
    """Return True when the observed success count satisfies the threshold.

    Semantics (unchanged from inline implementation):
    - If threshold is None: success only when every iteration succeeded
      (success_count == total).
    - If threshold is int: success when at least that many succeeded.
    - If threshold is float: success when (success_count / total) >= threshold.
      Division guard: if total==0 the predicate is treated as satisfied.

    This function is stateless and side-effect free.
    """
    if threshold is None:
        return success_count == total
    if isinstance(threshold, int):
        return success_count >= threshold
    return (success_count / total) >= threshold if total > 0 else True


def compute_required_success_count(total: int, threshold: int | float | None) -> int:
    """Compute the minimum number of successes required to meet the threshold.

    - threshold=None  -> total (every iteration is required)
    - threshold=int   -> that integer (clamped by caller expectations)
    - threshold=float -> math.ceil(total * threshold)

    The ceil behavior for fractional thresholds is intentional and must be
    preserved so that e.g. 0.5 on 3 items requires 2 successes.
    """
    if threshold is None:
        return total
    if isinstance(threshold, int):
        return threshold
    return math.ceil(total * threshold)
