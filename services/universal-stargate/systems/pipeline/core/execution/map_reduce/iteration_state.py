"""
Iteration state tracking for map step observability.

Captures per-iteration metadata for debugging and error reporting.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class IterationStatus(StrEnum):
    """Iteration completion status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class IterationResult:
    """
    Captures iteration execution result with context.

    Used for:
    - Rich error messages in MapPartialFailureError
    - Progress event payloads
    - Pipeline summary generation
    """

    index: int
    status: IterationStatus
    model_id: str | None = None
    gateway_id: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    started_at: float | None = None  # monotonic time
    truncated_response: str | None = (
        None  # path to file containing full truncated response, or short excerpt on write failure
    )
    truncation_tokens: int | None = None  # completion_tokens at truncation

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON/events."""
        return {
            "index": self.index,
            "status": self.status.value,
            "model_id": self.model_id,
            "gateway_id": self.gateway_id,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
        }

    def format_line(self, timeout_seconds: float | None = None) -> str:
        """Format as single-line summary for error messages."""
        model_part = self.model_id or "unknown"
        gateway_part = self.gateway_id or "unknown"
        location = f"{model_part} → {gateway_part}"

        match self.status:
            case IterationStatus.COMPLETED:
                duration = (
                    f"{self.duration_seconds:.1f}s" if self.duration_seconds else "?"
                )
                return f"Iteration {self.index} ({location}): completed in {duration}"
            case IterationStatus.TIMEOUT:
                timeout_str = f"{timeout_seconds:.1f}s" if timeout_seconds else "?"
                return (
                    f"Iteration {self.index} ({location}): "
                    f"TIMEOUT after {timeout_str} (request pending)"
                )
            case IterationStatus.FAILED:
                error = self.error_message or "unknown error"
                # Truncate long errors
                if len(error) > 80:
                    error = error[:77] + "..."
                return f"Iteration {self.index} ({location}): FAILED - {error}"
            case IterationStatus.CANCELLED:
                return f"Iteration {self.index} ({location}): CANCELLED"
            case _:
                return f"Iteration {self.index} ({location}): {self.status.value}"
