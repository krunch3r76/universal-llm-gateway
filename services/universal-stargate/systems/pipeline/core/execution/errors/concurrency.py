"""Concurrency-lock timeout error.

Defines :class:`ConcurrencyLockTimeoutError`, raised when a pipeline that
declares a ``concurrency:`` block cannot acquire its serialization lock within
the configured timeout. Marked retryable because lock contention is transient
by definition; serializes via ``to_dict()`` for API response envelopes.
"""

from dataclasses import dataclass

from .pipeline_error import PipelineError


@dataclass
class ConcurrencyLockTimeoutError(PipelineError):
    """Raised when pipeline execution exceeds the concurrency-lock timeout.

    Phase 5 of cortex-chat-openai. Pipelines that declare a
    ``concurrency:`` block serialise on a resolved string key (typically
    ``chat:{context.chat_id}``). When a second execution for the same
    key cannot acquire the lock within ``timeout_seconds``, this
    structured error surfaces so callers distinguish concurrency
    contention from generic ``TimeoutError`` / ``StepTimeoutError``.

    Marked retryable because the contention is transient by definition
    — the holding execution will release, and a fresh acquire attempt
    is well-defined.
    """

    pipeline_id: str
    execution_id: str
    key: str
    timeout_seconds: float

    @property
    def retryable(self) -> bool:
        return True

    def __str__(self) -> str:
        return (
            f"Pipeline '{self.pipeline_id}' execution {self.execution_id} "
            f"timed out waiting for concurrency lock on key '{self.key}' "
            f"after {self.timeout_seconds}s"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "ConcurrencyLockTimeoutError",
            "code": "concurrency_lock_timeout",
            "retryable": self.retryable,
            "pipeline_id": self.pipeline_id,
            "execution_id": self.execution_id,
            "key": self.key,
            "timeout_seconds": self.timeout_seconds,
        }
