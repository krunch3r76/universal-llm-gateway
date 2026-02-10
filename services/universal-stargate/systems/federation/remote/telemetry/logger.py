"""
Edge-side telemetry logger with filtering and throttling.

Logs telemetry events to structured JSON logs without blocking HTTP responses.

INVARIANT: Logging is async (non-blocking)
INVARIANT: Only state changes logged at INFO
"""

import asyncio
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from universal_logging import get_logger

base_logger = get_logger(__name__)


class TelemetryLogLevel(StrEnum):
    """Telemetry logging levels."""

    DEBUG = "DEBUG"  # All snapshots + deltas
    INFO = "INFO"  # Only state changes (deltas)
    ERROR = "ERROR"  # Only errors


class TelemetryLogger:
    """
    Async telemetry logger for Remote nodes.

    Logs telemetry events without blocking HTTP responses.
    Uses fire-and-forget pattern with exception handling.
    """

    def __init__(
        self,
        node_id: str,
        log_level: TelemetryLogLevel = TelemetryLogLevel.INFO,
    ):
        """
        Initialize telemetry logger.

        Args:
            node_id: Remote node identifier
            log_level: Logging verbosity level
        """
        self._node_id = node_id
        self._log_level = log_level

    def log_delta(self, delta: dict[str, Any], sequence_number: int) -> None:
        """
        Log telemetry delta (fire-and-forget).

        Args:
            delta: Computed delta from TelemetryStateTracker
            sequence_number: Sequence number for ordering
        """
        if not self._should_log_delta(delta):
            return

        # Compute delta size for monitoring
        delta_json = json.dumps(delta)
        delta_size_bytes = len(delta_json.encode("utf-8"))

        log_entry = {
            "timestamp": self._iso_timestamp(),
            "node_id": self._node_id,
            "event": "STATE_CHANGE",
            "sequence_number": sequence_number,
            "delta": delta,
            "delta_size_bytes": delta_size_bytes,
        }

        self._async_log(json.dumps(log_entry), level="INFO")

    def log_snapshot(self, snapshot: dict[str, Any]) -> None:
        """
        Log full telemetry snapshot (DEBUG only).

        Args:
            snapshot: Full state snapshot
        """
        if self._log_level != TelemetryLogLevel.DEBUG:
            return

        log_entry = {
            "timestamp": self._iso_timestamp(),
            "node_id": self._node_id,
            "event": "SNAPSHOT",
            "telemetry": snapshot,
        }

        self._async_log(json.dumps(log_entry), level="DEBUG")

    def log_critical_event(self, event_type: str, data: dict[str, Any]) -> None:
        """
        Log critical event (always logged at INFO).

        Args:
            event_type: Event type (e.g., "MODEL_LOADED")
            data: Event data
        """
        log_entry = {
            "timestamp": self._iso_timestamp(),
            "node_id": self._node_id,
            "event": event_type,
            **data,
        }

        self._async_log(json.dumps(log_entry), level="INFO")

    def _should_log_delta(self, delta: dict[str, Any]) -> bool:
        """
        Check if delta should be logged based on log level.

        Args:
            delta: Delta to check

        Returns:
            True if should log, False otherwise
        """
        # ERROR level: don't log deltas (only errors)
        if self._log_level == TelemetryLogLevel.ERROR:
            return False

        # INFO level: only log non-empty deltas
        if self._log_level == TelemetryLogLevel.INFO:
            # Empty if only sequence_number (or completely empty)
            non_seq_keys = [k for k in delta.keys() if k != "sequence_number"]
            return len(non_seq_keys) > 0

        # DEBUG level: log everything
        return True

    def _async_log(self, message: str, level: str = "INFO") -> None:
        """
        Log message asynchronously (fire-and-forget).

        Args:
            message: JSON log message
            level: Log level (INFO, DEBUG, ERROR)
        """

        async def _log():
            try:
                # Use base logger for actual output
                if level == "DEBUG":
                    base_logger.debug(message)
                elif level == "ERROR":
                    base_logger.error(message)
                else:
                    base_logger.info(message)
            except Exception as e:
                # Never let logging errors propagate
                base_logger.error(f"Telemetry logging failed: {e}")

        # Fire-and-forget: don't await, don't block
        asyncio.create_task(_log())

    @staticmethod
    def _iso_timestamp() -> str:
        """Generate ISO 8601 UTC timestamp with Z suffix."""
        dt_utc = datetime.now(UTC).replace(tzinfo=None)
        return dt_utc.isoformat(timespec="seconds") + "Z"
