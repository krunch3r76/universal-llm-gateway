"""Step and handler execution timeout errors.

Defines the two retryable timeout errors in the pipeline hierarchy:
:class:`StepTimeoutError` (the whole step, including retries, with optional
progress reporting in its message) and :class:`HandlerTimeoutError` (a single
handler attempt). Both override ``retryable`` to ``True`` and serialize via
``to_dict()`` for API response envelopes.
"""

from dataclasses import dataclass

from .pipeline_error import PipelineError


@dataclass
class StepTimeoutError(PipelineError):
    """Raised when entire step (including retries) exceeds timeout."""

    step_name: str
    timeout_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_call_count: int = 0
    items_total: int | None = None
    items_completed: int | None = None

    @property
    def retryable(self) -> bool:
        return True

    def __str__(self) -> str:
        message = (
            f"[Step '{self.step_name}'] "
            f"Exceeded total timeout of {self.timeout_seconds}s"
        )
        has_progress = (
            self.prompt_tokens > 0
            or self.completion_tokens > 0
            or self.model_call_count > 0
            or self.items_total is not None
            or self.items_completed is not None
        )
        if not has_progress:
            return message

        progress_parts: list[str] = []
        if self.items_total is not None and self.items_completed is not None:
            progress_parts.append(
                f"{self.items_completed}/{self.items_total} claims verified",
            )
        elif self.items_total is not None:
            progress_parts.append(f"{self.items_total} claims tracked")

        progress_parts.append(f"{self.model_call_count} model calls attempted")
        progress_parts.append(
            f"{self.prompt_tokens + self.completion_tokens} tokens used",
        )
        return f"{message}\n  Progress: {', '.join(progress_parts)}"

    def to_dict(self) -> dict:
        return {
            "error_type": "StepTimeoutError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model_call_count": self.model_call_count,
            "items_total": self.items_total,
            "items_completed": self.items_completed,
        }


@dataclass
class HandlerTimeoutError(PipelineError):
    """Raised when single handler execution exceeds timeout."""

    step_name: str
    timeout_seconds: float
    attempt: int = 1

    @property
    def retryable(self) -> bool:
        return True

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Handler execution exceeded timeout of "
            f"{self.timeout_seconds}s (attempt {self.attempt})"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "HandlerTimeoutError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "attempt": self.attempt,
        }
