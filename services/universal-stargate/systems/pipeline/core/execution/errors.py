"""
Pipeline error hierarchy with structured serialization.

∀ error: error.to_dict() → JSON-compatible dict for API responses
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .map_reduce.iteration_state import IterationResult


class PipelineError(RuntimeError, ABC):
    """Base class for pipeline validation/runtime errors."""

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        ...


@dataclass
class BindingResolutionError(PipelineError):
    """Raised when an input binding cannot be resolved."""

    step_name: str
    field_name: str
    binding_repr: str  # String repr of binding
    reason: str

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Cannot resolve input '{self.field_name}'\n"
            f"  Binding: {self.binding_repr}\n"
            f"  Reason: {self.reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "BindingResolutionError",
            "step_name": self.step_name,
            "field_name": self.field_name,
            "binding": self.binding_repr,
            "reason": self.reason,
        }


@dataclass
class OutputValidationError(PipelineError):
    """Raised when handler output doesn't match declared outputs."""

    step_name: str
    declared_outputs: list[str]
    actual_keys: list[str]
    missing_keys: list[str]

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Handler output validation failed\n"
            f"  Declared: {self.declared_outputs}\n"
            f"  Missing: {self.missing_keys}\n"
            f"  Available: {self.actual_keys}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "OutputValidationError",
            "step_name": self.step_name,
            "declared_outputs": self.declared_outputs,
            "actual_keys": self.actual_keys,
            "missing_keys": self.missing_keys,
        }


@dataclass
class InputTypeMismatchError(PipelineError):
    """Raised when resolved value doesn't match handler's input_type."""

    step_name: str
    field_name: str
    expected_type: str
    actual_type: str
    value_preview: str

    def __str__(self) -> str:
        preview = (
            self.value_preview[:100] + "..."
            if len(self.value_preview) > 100
            else self.value_preview
        )
        return (
            f"[Step '{self.step_name}'] Type mismatch for input '{self.field_name}'\n"
            f"  Expected: {self.expected_type}\n"
            f"  Got: {self.actual_type}\n"
            f"  Value: {preview}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "InputTypeMismatchError",
            "step_name": self.step_name,
            "field_name": self.field_name,
            "expected_type": self.expected_type,
            "actual_type": self.actual_type,
            "value_preview": self.value_preview[:100],
        }


@dataclass
class InvalidNamespaceError(PipelineError):
    """Raised when a namespace is used in invalid context."""

    namespace: str
    context: str
    hint: str = ""

    def __str__(self) -> str:
        msg = f"Invalid namespace '{self.namespace}' in context: {self.context}"
        if self.hint:
            msg += f"\n  Hint: {self.hint}"
        return msg

    def to_dict(self) -> dict:
        return {
            "error_type": "InvalidNamespaceError",
            "namespace": self.namespace,
            "context": self.context,
            "hint": self.hint,
        }


@dataclass
class StepTimeoutError(PipelineError):
    """Raised when entire step (including retries) exceeds timeout."""

    step_name: str
    timeout_seconds: float

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] "
            f"Exceeded total timeout of {self.timeout_seconds}s"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "StepTimeoutError",
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class HandlerTimeoutError(PipelineError):
    """Raised when single handler execution exceeds timeout."""

    step_name: str
    timeout_seconds: float
    attempt: int = 1

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Handler execution exceeded timeout of "
            f"{self.timeout_seconds}s (attempt {self.attempt})"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "HandlerTimeoutError",
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class MapPartialFailureError(PipelineError):
    """
    Raised when map step completes with partial success below threshold.

    Contains per-iteration details for debugging:
    - Model and gateway routing
    - Status (completed/timeout/failed)
    - Duration for completed iterations
    - Error messages for failed iterations
    """

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
        from .map_reduce.iteration_state import IterationStatus

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
            f"(required: {threshold_str})"
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
