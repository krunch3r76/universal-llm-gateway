"""Map-reduce partial-failure error.

Defines :class:`MapPartialFailureError`, raised when a map step finishes with a
completed-iteration count below the configured success threshold. It carries
ordered per-iteration results for debugging and computes ``failed_indices``
lazily from :class:`IterationStatus` so importing this module does not pull in
the sibling map-reduce iteration-state package at definition time.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .pipeline_error import PipelineError

if TYPE_CHECKING:
    from ..map_reduce.iteration_state import IterationResult


@dataclass
class MapPartialFailureError(PipelineError):
    """Raised when map step completes with partial success below threshold.

    Contains per-iteration details for debugging:
    - Model and gateway routing
    - Status (completed/timeout/failed)
    - Duration for completed iterations
    - Error messages for failed iterations
    """

    @property
    def retryable(self) -> bool:
        return True

    step_name: str
    completed_count: int
    failed_count: int
    total_count: int
    threshold: int | float
    timeout_seconds: float | None
    iteration_results: tuple["IterationResult", ...]  # Ordered by index
    gateway_serialization: tuple[str, ...] | None = (
        None  # Gateways with multiple iterations
    )

    # Backward compat - computed from iteration_results
    @property
    def failed_indices(self) -> tuple[int, ...]:
        """Indices of non-completed iterations."""
        from ..map_reduce.iteration_state import IterationStatus

        return tuple(
            r.index
            for r in self.iteration_results
            if r.status != IterationStatus.COMPLETED
        )

    def __str__(self) -> str:
        threshold_str = (
            f"{self.threshold * 100:.0f}%"
            if isinstance(self.threshold, float)
            else f"{self.threshold}"
        )

        lines = [
            f"Map step '{self.step_name}' did not meet success threshold: "
            f"{self.completed_count}/{self.total_count} succeeded "
            f"(required: {threshold_str})",
        ]

        # Add per-iteration details
        for result in self.iteration_results:
            lines.append(f"  {result.format_line(self.timeout_seconds)}")

        # Add serialization warning if detected
        if self.gateway_serialization:
            gateways = ", ".join(self.gateway_serialization)
            lines.append(f"⚠️ Gateway serialization detected: {gateways}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "error_type": "MapPartialFailureError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "threshold": self.threshold,
            "timeout_seconds": self.timeout_seconds,
            "iteration_results": [r.to_dict() for r in self.iteration_results],
            "failed_indices": list(self.failed_indices),
            "gateway_serialization": (
                list(self.gateway_serialization) if self.gateway_serialization else None
            ),
        }
