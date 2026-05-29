"""Handler output-contract validation error.

Defines :class:`OutputValidationError`, raised when a step handler's returned
keys do not satisfy the step's declared outputs. It serializes the declared,
actual, and missing key sets via ``to_dict()`` for API response envelopes and
inherits the non-retryable default from :class:`PipelineError`.
"""

from dataclasses import dataclass

from .pipeline_error import PipelineError


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
            "retryable": self.retryable,
            "step_name": self.step_name,
            "declared_outputs": self.declared_outputs,
            "actual_keys": self.actual_keys,
            "missing_keys": self.missing_keys,
        }
