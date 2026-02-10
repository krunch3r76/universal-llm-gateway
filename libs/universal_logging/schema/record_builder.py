"""
Canonical record builder — single code path for LogRecord → dict conversion.

INVARIANT: ∀ log_output: json.loads(output) == build_canonical_record(log_record)
INVARIANT: build_canonical_record is THE conversion function, no alternatives exist

This is the ONLY place that reads from logging.LogRecord.
Renderers receive the dict output, never the raw LogRecord.
"""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, datetime
from typing import Any

from .fields import CallerInfo, ErrorInfo


class CanonicalRecordBuilder:
    """
    Builds canonical log record dicts from Python LogRecords.

    This class owns the conversion logic. All renderers consume its output.
    No renderer may bypass this builder or derive fields independently.
    """

    # Fields that are internal to logging and should not appear in extra
    _RESERVED_FIELDS: frozenset[str] = frozenset(
        {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
            # Also exclude any caller_* fields we inject
            "caller_file",
            "caller_func",
            "caller_line",
        }
    )

    def __init__(
        self,
        truncate: bool = False,
        max_field_size: int = 2000,
    ):
        """
        Initialize record builder.

        Args:
            truncate: Whether to truncate large string fields
            max_field_size: Maximum size for string fields before truncation
        """
        self.truncate = truncate
        self.max_field_size = max_field_size
        # Protected paths: fields that should never be truncated
        # Tracebacks must remain complete for debugging
        self.protected_paths = frozenset({"error.traceback"})

    def build(self, record: logging.LogRecord) -> dict[str, Any]:
        """
        Convert LogRecord to canonical dict.

        Args:
            record: Python logging LogRecord

        Returns:
            Canonical dict with standardized fields
        """
        # Timestamp: ISO 8601 with milliseconds, UTC
        timestamp = datetime.fromtimestamp(record.created, tz=UTC)

        # Caller info
        caller = CallerInfo(
            file=record.filename,
            func=record.funcName,
            line=record.lineno,
        )

        # Base record
        canonical: dict[str, Any] = {
            "@timestamp": timestamp.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "caller": caller.to_dict(),
            "process": record.process,
            "thread": record.threadName,
        }

        # Error info (only if exception present)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, exc_tb = record.exc_info
            error = ErrorInfo(
                type=exc_type.__name__ if exc_type else "Unknown",
                message=str(exc_value) if exc_value else "",
                traceback=self._format_traceback(record.exc_info),
            )
            canonical["error"] = error.to_dict()

        # Extra fields (user-provided)
        extra = self._extract_extra(record)
        if extra:
            canonical["extra"] = extra

        # Apply truncation if configured
        if self.truncate:
            from ..json_utils import truncate_json_fields

            canonical = truncate_json_fields(
                canonical,
                max_field_size=self.max_field_size,
                protected_paths=self.protected_paths,
            )

        return canonical

    def _format_traceback(self, exc_info: tuple) -> str | None:
        """Format exception traceback as string."""
        if not exc_info or exc_info[0] is None:
            return None
        try:
            return "".join(traceback.format_exception(*exc_info)).rstrip()
        except Exception as e:
            # Policy: caught exceptions must log WARN/ERROR or re-raise
            import logging

            logging.getLogger(__name__).warning(f"Failed to format traceback: {e}")
            return f"<traceback formatting failed: {e}>"

    def _extract_extra(self, record: logging.LogRecord) -> dict[str, Any]:
        """Extract user-provided extra fields from record."""
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self._RESERVED_FIELDS and not key.startswith("_"):
                extra[key] = value
        return extra


# Module-level singleton for convenience
_builder = CanonicalRecordBuilder()


def build_canonical_record(record: logging.LogRecord) -> dict[str, Any]:
    """
    Build canonical record dict from LogRecord.

    This is the single entry point for LogRecord → dict conversion.
    All renderers must use this function or the CanonicalRecordBuilder class.

    Args:
        record: Python logging LogRecord

    Returns:
        Canonical dict ready for JSON serialization
    """
    return _builder.build(record)
