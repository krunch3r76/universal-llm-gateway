"""
Worker state reporting interface for process_ipc package.

Allows worker processes to report their state and activity to the manager.
"""

from typing import Any

from . import signals
from .types import ProcessState


class WorkerStateReporter:
    """Allows worker processes to report their state."""

    def __init__(self, transport, worker_id: str | None = None):
        self.transport = transport
        self.worker_id = worker_id

    async def report_state(self, state: ProcessState, details: dict[str, Any] = None):
        """Report current state to the manager."""
        message = signals.StateReport(
            worker_id=self.worker_id,
            state=state.value,
            details=details or {},
        )
        await self.transport.send(message)

    async def report_activity(self, activity_type: str, details: dict[str, Any] = None):
        """Report current activity (e.g., 'loading_model', 'processing_inference')."""
        message = signals.ActivityReport(
            worker_id=self.worker_id,
            activity_type=activity_type,
            details=details or {},
        )
        await self.transport.send(message)

    async def report_progress(self, progress: float, message_text: str = None):
        """Report progress for long-running operations."""
        message = signals.ProgressReport(
            worker_id=self.worker_id,
            progress=progress,
            message=message_text,
        )
        await self.transport.send(message)

    async def report_capabilities(self, capabilities: dict[str, Any]):
        """Report what this process can do (loaded models, etc.)."""
        message = signals.CapabilitiesReport(
            worker_id=self.worker_id,
            capabilities=capabilities,
        )
        await self.transport.send(message)
