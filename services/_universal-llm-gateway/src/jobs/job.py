"""
Base Job class with status tracking and SSE log streaming.

Uses W3C-compliant SSE event types:
- event: log - Progress messages
- event: complete - Job finished successfully
- event: error - Job failed
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sse import SSEMessage, format_sse_message
from universal_logging import get_logger

logger = get_logger(__name__)


class JobEventType(str, Enum):
    """SSE event types for job streaming."""

    LOG = "log"
    COMPLETE = "complete"
    ERROR = "error"
    KEEPALIVE = "keepalive"


class JobStatus(str, Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """
    Base job class with status tracking and log streaming.

    Supports SSE streaming of log messages for real-time progress monitoring.
    """

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    _logs: list[str] = field(default_factory=list)
    _log_event: asyncio.Event | None = field(default=None, repr=False)
    _task: asyncio.Task[None] | None = None

    def _get_log_event(self) -> asyncio.Event:
        """Lazily create log event (requires running event loop)."""
        if self._log_event is None:
            self._log_event = asyncio.Event()
        return self._log_event

    def emit_log(self, message: str) -> None:
        """Add log message and notify waiters."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        self._logs.append(log_line)
        self._get_log_event().set()
        logger.debug(f"[{self.job_id}] {message}")

    async def stream_logs(self) -> AsyncIterator[str]:
        """
        Yield log lines as typed SSE events.

        Uses W3C-compliant event types:
        - event: log - Progress messages
        - event: complete - Job finished (status=completed)
        - event: error - Job failed (status=failed/cancelled)

        Streams all logs including historical, then waits for new logs
        until job completes.
        """
        index = 0
        while self.status in (JobStatus.PENDING, JobStatus.RUNNING):
            if index < len(self._logs):
                for log in self._logs[index:]:
                    yield format_sse_message(
                        SSEMessage(event=JobEventType.LOG.value, data=log)
                    )
                index = len(self._logs)

            self._get_log_event().clear()
            try:
                await asyncio.wait_for(self._get_log_event().wait(), timeout=1.0)
            except TimeoutError:
                # W3C-compliant keepalive: comment line (no event/data fields)
                yield ": keepalive\n\n"

        for log in self._logs[index:]:
            yield format_sse_message(SSEMessage(event=JobEventType.LOG.value, data=log))

        if self.status == JobStatus.COMPLETED:
            yield format_sse_message(
                SSEMessage(
                    event=JobEventType.COMPLETE.value,
                    data={"status": self.status.value, "job_id": self.job_id},
                )
            )
        else:
            yield format_sse_message(
                SSEMessage(
                    event=JobEventType.ERROR.value,
                    data={
                        "status": self.status.value,
                        "job_id": self.job_id,
                        "error": self.error or "Job did not complete successfully",
                    },
                )
            )

    async def start(self) -> None:
        """Start job execution in background."""
        self._task = asyncio.create_task(self._run_wrapper())

    async def cancel(self) -> None:
        """Cancel running job."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self.status = JobStatus.CANCELLED
            self.completed_at = datetime.now()
            self.emit_log("Job cancelled by user")

    async def _run_wrapper(self) -> None:
        """Wrapper that handles status transitions and exceptions."""
        self.status = JobStatus.RUNNING
        self.started_at = datetime.now()

        try:
            await self._run()
            if self.status == JobStatus.RUNNING:
                self.status = JobStatus.COMPLETED
        except asyncio.CancelledError:
            self.status = JobStatus.CANCELLED
            self.emit_log("Job cancelled")
        except Exception as e:
            self.status = JobStatus.FAILED
            self.error = str(e)
            self.emit_log(f"❌ Error: {e}")
            logger.exception(f"[{self.job_id}] Job failed: {e}")
        finally:
            self.completed_at = datetime.now()

    async def _run(self) -> None:
        """Override in subclass to implement job logic."""
        raise NotImplementedError
