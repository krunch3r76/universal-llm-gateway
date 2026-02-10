"""
Canonical field definitions for structured logging.

INVARIANT: ∀ field ∈ log_output: field ∈ CANONICAL_FIELDS
"""

from dataclasses import dataclass
from typing import Any

# Field names follow JSON conventions (snake_case, no @ prefix for custom fields)
# @timestamp uses @ prefix as it's a standard convention in ELK/logging ecosystems
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        "@timestamp",  # ISO 8601 with milliseconds
        "level",  # Uppercase: DEBUG, INFO, WARNING, ERROR, CRITICAL
        "logger",  # Logger name (e.g., "src.core.workers")
        "message",  # Log message (after % formatting)
        "caller",  # Nested: file, func, line
        "error",  # Nested: type, message, traceback (only if exception)
        "extra",  # User-provided extra fields
        "process",  # Process ID
        "thread",  # Thread name
    }
)


@dataclass(frozen=True, slots=True)
class CallerInfo:
    """Caller location information."""

    file: str
    func: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "func": self.func, "line": self.line}


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Exception information for error logs."""

    type: str
    message: str
    traceback: str | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type, "message": self.message}
        if self.traceback:
            result["traceback"] = self.traceback
        return result
