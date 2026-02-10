"""
Timing extraction and validation for CPU measurements.

Parses [TIMING] lines from stderr and validates monotonicity
(smaller contexts must not take longer than larger contexts).
"""

import re
from dataclasses import dataclass
from typing import Any, override

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True, kw_only=True)
class TimingInfo:
    """Parsed timing information from measurement stderr."""

    load_time_sec: float | None = None
    warmup_time_sec: float | None = None
    total_time_sec: float | None = None


def parse_timing_from_stderr(stderr: str) -> TimingInfo:
    """
    Extract timing values from stderr output.

    Parses:
      [TIMING] Model load: X.Xs
      [TIMING] Warmup: X.Xs

    Returns TimingInfo with load_time_sec, warmup_time_sec, total_time_sec.
    total_time_sec = load_time_sec + warmup_time_sec (when both available).
    """
    load_time: float | None = None
    warmup_time: float | None = None

    load_match = re.search(r"\[TIMING\] Model load: ([\d.]+)s", stderr)
    warmup_match = re.search(r"\[TIMING\] Warmup: ([\d.]+)s", stderr)

    if load_match:
        load_time = float(load_match.group(1))
    if warmup_match:
        warmup_time = float(warmup_match.group(1))

    # Total time is sum of both (for validation)
    total_time: float | None = None
    if load_time is not None and warmup_time is not None:
        total_time = load_time + warmup_time
    elif load_time is not None:
        # Warmup may have failed/skipped - use load time only
        total_time = load_time

    return TimingInfo(
        load_time_sec=load_time,
        warmup_time_sec=warmup_time,
        total_time_sec=total_time,
    )


def add_timing_to_profile(profile: dict[str, Any]) -> TimingInfo | None:
    """
    Parse timing from profile stderr and add timing fields to profile dict.

    Returns TimingInfo if parsing succeeded, None otherwise.
    Mutates profile to add load_time_sec, warmup_time_sec, total_time_sec.
    """
    stderr_raw = profile.get("stderr")
    if not stderr_raw:
        return None
    if not isinstance(stderr_raw, str):
        logger.warning(
            f"Invalid stderr type: {type(stderr_raw).__name__}, expected str"
        )
        return None

    timing = parse_timing_from_stderr(stderr_raw)

    # Add timing fields to profile
    if timing.load_time_sec is not None:
        profile["load_time_sec"] = timing.load_time_sec
    if timing.warmup_time_sec is not None:
        profile["warmup_time_sec"] = timing.warmup_time_sec
    if timing.total_time_sec is not None:
        profile["total_time_sec"] = timing.total_time_sec

    return timing if timing.total_time_sec is not None else None


@dataclass(slots=True)
class TimingViolation:
    """Describes a timing anomaly where smaller context is slower."""

    smaller_ctx: int
    smaller_time: float
    larger_ctx: int
    larger_time: float

    @override
    def __str__(self) -> str:
        return (
            f"{self.smaller_ctx} ({self.smaller_time:.1f}s) > "
            f"{self.larger_ctx} ({self.larger_time:.1f}s)"
        )


class TimingTracker:
    """
    Track CPU context timing for monotonicity validation.

    Invariant (with tolerance): ∀ ctx₁, ctx₂ ∈ tracked:
        ctx₁ < ctx₂ ⟹ time(ctx₁) ≤ time(ctx₂) + tolerance
    """

    def __init__(self, tolerance_sec: float = 5.0) -> None:
        """
        Initialize timing tracker.

        Args:
            tolerance_sec: Allowed timing variance in seconds (default: 5.0).
                          Accounts for system load, OS scheduling, etc.
        """
        self._times: dict[int, float] = {}
        self._tolerance_sec = tolerance_sec

    def check_and_record(
        self, context: int, total_time: float
    ) -> list[TimingViolation]:
        """
        Check if total_time for context violates monotonicity with tolerance.

        Returns list of violations (empty if valid).
        If valid, records the timing for future checks.

        Violation: ∃ larger_ctx ∈ tracked:
            larger_ctx > context ∧ total_time > time(larger_ctx) + tolerance
        """
        violations: list[TimingViolation] = []

        for larger_ctx, larger_time in self._times.items():
            threshold = larger_time + self._tolerance_sec
            if larger_ctx > context and total_time > threshold:
                violations.append(
                    TimingViolation(
                        smaller_ctx=context,
                        smaller_time=total_time,
                        larger_ctx=larger_ctx,
                        larger_time=larger_time,
                    )
                )

        if not violations:
            # Valid: record for future checks
            self._times[context] = total_time

        return violations

    def get_tracked_contexts(self) -> dict[int, float]:
        """Return copy of tracked context -> total_time mapping."""
        return dict(self._times)


def create_timing_anomaly_error(
    violations: list[TimingViolation],
    timing: TimingInfo,
) -> dict[str, Any]:
    """
    Create error profile for timing anomaly.

    Returns dict with error field and timing info for debugging.
    The error field ensures profile is not added to catalog.
    """
    violation_msgs = [str(v) for v in violations]
    error_msg = (
        "Timing anomaly: smaller context slower than larger "
        f"(model/engine issue): {', '.join(violation_msgs)}"
    )
    return {
        "error": error_msg,
        "total_time_sec": timing.total_time_sec,
        "load_time_sec": timing.load_time_sec,
        "warmup_time_sec": timing.warmup_time_sec,
    }



def validate_and_log_timing(
    profile: dict[str, Any],
    ctx: int,
    timing_tracker: "TimingTracker | None",
    emit_log: Any,
    mode_label: str = "",
) -> dict[str, Any]:
    """
    Parse timing, log it, validate monotonicity, return (possibly error) profile.

    Shared helper for CPU and GPU measurement - consolidates timing logic.

    Args:
        profile: Measurement profile dict (must have 'success' and optionally 'stderr')
        ctx: Context size being measured
        timing_tracker: TimingTracker instance (None to skip validation)
        emit_log: Logging callback function
        mode_label: Optional label for anomaly message (e.g., "in hybrid")

    Returns:
        Original profile if valid, or error profile if timing anomaly detected.
    """
    if not profile.get("success"):
        return profile

    # Parse timing from stderr
    timing = add_timing_to_profile(profile)

    # Log timing lines for visibility
    if stderr := profile.get("stderr"):
        for line in stderr.splitlines():
            if "[TIMING]" in line:
                emit_log(f"  {line.strip()}")

    # Timing validation: reject if smaller context is slower than larger
    if timing_tracker and timing and timing.total_time_sec is not None:
        violations = timing_tracker.check_and_record(ctx, timing.total_time_sec)
        if violations:
            violation_msgs = [str(v) for v in violations]
            label = f" {mode_label}" if mode_label else ""
            emit_log(
                f"  ❌ {ctx}: Timing anomaly{label} - smaller context slower "
                f"than larger (model/engine issue): {', '.join(violation_msgs)}"
            )
            return create_timing_anomaly_error(violations, timing)

    return profile
