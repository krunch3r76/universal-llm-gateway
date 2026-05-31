"""Idle monitor - background task for stream timeout.

Single responsibility: Detect idle entries and trigger notification.
Control-plane messaging delegated to RegistryControlMixin.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

from universal_logging import get_logger

from universal_protocol.ws.registry.entries import StreamEntry
from universal_protocol.ws.registry.protocols import IdleMonitorHostProtocol

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Pure helpers (no side effects except logging)
# -----------------------------------------------------------------------------


def _iter_idle_entries(
    entries: dict[str, StreamEntry],
    idle_timeout: float,
    now: float,
) -> Iterator[tuple[StreamEntry, float]]:
    """Yield (entry, idle_seconds) for STREAM entries exceeding idle timeout.

    Pure iteration - no side effects.

    Inputs:
        entries: Entry dict to scan
        idle_timeout: Timeout threshold in seconds
        now: Current timestamp (time.time())

    Outputs:
        Iterator of (entry, idle_seconds) tuples for idle stream entries

    Note: Request-kind entries (non-streaming inference) are skipped.
    They rely on HTTP-level timeouts instead of idle monitoring.
    """
    for entry in entries.values():
        # Only apply idle timeout to streams, not requests
        # Requests use HTTP-level timeout instead
        if entry.kind != "stream":
            continue
        if entry.is_idle(idle_timeout):
            idle_seconds = now - entry.last_activity
            yield entry, idle_seconds


def _log_monitor_started(timeout: float, interval: float) -> None:
    """Log that idle monitor has started.

    Inputs:
        timeout: Idle timeout in seconds
        interval: Check interval in seconds
    """
    logger.info(f"⏱️ Started idle monitor (timeout={timeout}s, interval={interval}s)")


def _log_monitor_stopped() -> None:
    """Log that idle monitor has stopped."""
    logger.info("⏱️ Stopped idle monitor")


def _log_monitor_cancelled() -> None:
    """Log that idle monitor was cancelled."""
    logger.debug("Idle monitor cancelled")


def _log_monitor_error(error: Exception) -> None:
    """Log an error in idle monitor.

    Inputs:
        error: The exception that occurred
    """
    logger.exception(f"Error in idle monitor: {error}")


# -----------------------------------------------------------------------------
# Task factory
# -----------------------------------------------------------------------------


def _create_idle_monitor_task(
    coro: asyncio.coroutines,
) -> asyncio.Task[None]:
    """Create the idle monitor background task.

    Inputs:
        coro: The coroutine to run (typically _run_idle_monitor)

    Outputs:
        The created Task
    """
    return asyncio.create_task(coro, name="stream_idle_monitor")


# -----------------------------------------------------------------------------
# Mixin
# -----------------------------------------------------------------------------


class IdleMonitorMixin:
    """Idle monitor methods for StreamRegistry.

    Contract: Host class must satisfy IdleMonitorHostProtocol.

    Invariant: |idle_monitor_tasks| ≤ 1 (at most one background task)
    """

    _entries: dict[str, StreamEntry]
    _idle_monitor_task: asyncio.Task[None] | None
    _idle_timeout: float
    _idle_check_interval: float

    def configure_idle_monitor(
        self: IdleMonitorHostProtocol,
        timeout: float = 30.0,
        check_interval: float = 5.0,
    ) -> None:
        """Configure idle monitor parameters.

        Inputs:
            timeout: Idle timeout in seconds (default 30.0)
            check_interval: Check interval in seconds (default 5.0)
        """
        self._idle_timeout = timeout
        self._idle_check_interval = check_interval

    def _validate_idle_config(self: IdleMonitorHostProtocol) -> None:
        """Validate idle monitor configuration exists.

        Raises:
            RuntimeError: If configuration is missing or invalid

        Precondition: Called before starting idle monitor
        """
        if not hasattr(self, "_idle_timeout") or not hasattr(
            self, "_idle_check_interval"
        ):
            raise RuntimeError(
                "Idle monitor not configured. Call configure_idle_monitor() first "
                "or ensure host class initializes _idle_timeout and "
                "_idle_check_interval."
            )
        if self._idle_timeout <= 0 or self._idle_check_interval <= 0:
            raise RuntimeError(
                f"Invalid idle config: timeout={self._idle_timeout}, "
                f"interval={self._idle_check_interval}. Both must be > 0."
            )

    async def start_idle_monitor(self: IdleMonitorHostProtocol) -> None:
        """Start background task to monitor all streams for idle timeout.

        Idempotent: Safe to call multiple times.
        Postcondition: |idle_monitor_tasks| = 1

        Raises:
            RuntimeError: If idle monitor not configured
        """
        if self._idle_monitor_task is not None:
            return

        # Guard: validate configuration exists
        IdleMonitorMixin._validate_idle_config(self)

        # Create task (segregated)
        self._idle_monitor_task = _create_idle_monitor_task(
            IdleMonitorMixin._run_idle_monitor(self)
        )

        # Log start (segregated)
        _log_monitor_started(self._idle_timeout, self._idle_check_interval)

    async def stop_idle_monitor(self: IdleMonitorHostProtocol) -> None:
        """Stop the idle monitor task.

        Idempotent: Safe to call multiple times.
        """
        if self._idle_monitor_task is None:
            return

        self._idle_monitor_task.cancel()
        try:
            await self._idle_monitor_task
        except asyncio.CancelledError:
            pass
        self._idle_monitor_task = None

        # Log stop (segregated)
        _log_monitor_stopped()

    async def _run_idle_monitor(self: IdleMonitorHostProtocol) -> None:
        """Check all streams for idle timeout periodically.

        Invariant: Single task for all streams (not per-stream)

        Flow:
            1. Sleep for check_interval
            2. Iterate idle entries (via _iter_idle_entries)
            3. Delegate to control API (notify_idle_timeout)
            4. Repeat
        """
        while True:
            try:
                await asyncio.sleep(self._idle_check_interval)

                now = time.time()
                # Use extracted helper for iteration (pure, testable)
                for entry, idle_seconds in _iter_idle_entries(
                    self._entries, self._idle_timeout, now
                ):
                    self.notify_idle_timeout(entry.entry_id, idle_seconds)

            except asyncio.CancelledError:
                _log_monitor_cancelled()
                break
            except Exception as e:
                _log_monitor_error(e)
