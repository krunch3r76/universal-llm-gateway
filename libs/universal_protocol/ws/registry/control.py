"""Registry control plane - cancellation and unload notifications.

Single responsibility: Push control events to streams.
All control-frame emission flows through _signal_and_try_push().
"""

from __future__ import annotations

import asyncio
from universal_logging import get_logger, DEBUG, WARNING
from universal_protocol.ws.registry.entries import StreamEntry
from universal_protocol.ws.registry.protocols import ControlHostProtocol

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Pure helpers (no side effects except logging)
# -----------------------------------------------------------------------------


def _build_control_frame(
    code: str,
    entry_id: str,
    message: str,
    **fields: object,
) -> dict[str, object]:
    """Build a control frame for queue delivery.

    Pure function - no side effects.

    Inputs:
        code: Control frame code (e.g., CODE_CANCELLED)
        entry_id: Stream/request identifier
        message: Human-readable message
        **fields: Additional frame fields

    Outputs:
        Control frame dict ready for queue
    """
    from universal_protocol.ws.frame_types import make_control_frame

    return make_control_frame(code, entry_id, message=message, **fields)


def _log_control_event(log_message: str, log_level: int = DEBUG) -> None:
    """Log a control event.

    Single point for control-plane logging.

    Inputs:
        log_message: Message to log
        log_level: Logging level (default DEBUG)
    """
    logger.log(log_level, log_message)


def _log_queue_push_failure(entry_id: str, reason: str) -> None:
    """Log failure to push control frame to queue.

    Inputs:
        entry_id: Entry whose queue push failed
        reason: "full" or "closed"
    """
    logger.debug(f"Queue {reason}, could not push control frame to {entry_id}")


# -----------------------------------------------------------------------------
# Mixin
# -----------------------------------------------------------------------------


class RegistryControlMixin:
    """Control plane methods for StreamRegistry.

    Contract: Host class must satisfy ControlHostProtocol.

    Invariant: All control events use _signal_and_try_push() (single path).
    """

    _entries: dict[str, StreamEntry]  # Satisfied by ControlHostProtocol

    def _signal_cancel(self, entry: StreamEntry) -> None:
        """Signal cancellation on entry (sync, idempotent).

        Inputs:
            entry: StreamEntry to signal
        """
        entry.cancellation_event.set()

    def _try_push_frame(
        self,
        entry_id: str,
        entry: StreamEntry,
        frame: dict[str, object],
    ) -> None:
        """Attempt to push control frame to queue (non-blocking, best effort).

        Logs and suppresses queue-full/closed errors.

        Note: This is synchronous code calling put_nowait() - CancelledError
        cannot be raised here (no await points).

        Inputs:
            entry_id: Entry identifier for logging
            entry: StreamEntry with queue
            frame: Control frame to push
        """
        if not entry.queue:
            return

        try:
            entry.queue.put_nowait(frame)
        except asyncio.QueueFull:
            _log_queue_push_failure(entry_id, "full")
        except RuntimeError:
            _log_queue_push_failure(entry_id, "closed")

    def _signal_and_try_push(
        self: ControlHostProtocol,
        entry_id: str,
        code: str,
        *,
        message: str,
        log_message: str,
        log_level: int = DEBUG,
        **fields: object,
    ) -> bool:
        """Signal cancellation and push control frame (orchestration only).

        Orchestrates: lookup → signal → build frame → push → log.

        Precondition: entry_id may or may not exist (idempotent)
        Postcondition:
            entry exists ⟹ cancellation_event.set() ∧ control frame attempted

        Inputs:
            entry_id: Stream/request identifier
            code: Control frame code (e.g., CODE_CANCELLED)
            message: Human-readable message for frame
            log_message: Message to log on success
            log_level: Logging level (default DEBUG)
            **fields: Additional frame fields

        Outputs:
            True if entry found and processed, False if not found
        """
        entry = self._entries.get(entry_id)
        if not entry:
            return False

        # 1. Signal engine abort
        RegistryControlMixin._signal_cancel(self, entry)

        # 2. Build control frame (pure)
        frame = _build_control_frame(code, entry_id, message, **fields)

        # 3. Push to queue (best effort)
        RegistryControlMixin._try_push_frame(self, entry_id, entry, frame)

        # 4. Log event (segregated)
        _log_control_event(log_message, log_level)
        return True

    def cancel_entry(
        self: ControlHostProtocol,
        entry_id: str,
        reason: str = "cancelled",
    ) -> bool:
        """Cancel entry: signal engine + notify consumer via queue.

        This is the canonical cancellation path. All cancellation sites
        should call this method, not directly manipulate events/queues.

        Inputs:
            entry_id: Stream/request identifier
            reason: Machine-readable reason (for logging)

        Outputs:
            True if entry found and cancelled, False if not found
        """
        from universal_protocol.ws.frame_types import CODE_CANCELLED

        return RegistryControlMixin._signal_and_try_push(
            self,
            entry_id,
            CODE_CANCELLED,
            message="Stream cancelled",
            reason=reason,
            log_message=f"🛑 Cancelled entry {entry_id} (reason={reason})",
        )

    def notify_model_unload(
        self: ControlHostProtocol,
        entry_id: str,
        model_name: str,
    ) -> bool:
        """Notify consumer that model is being unloaded.

        Inputs:
            entry_id: Stream/request identifier
            model_name: Name of model being unloaded

        Outputs:
            True if entry found and notified, False if not found
        """
        from universal_protocol.ws.frame_types import CODE_MODEL_UNLOADED

        return RegistryControlMixin._signal_and_try_push(
            self,
            entry_id,
            CODE_MODEL_UNLOADED,
            message=f"Model {model_name} is being unloaded",
            model_name=model_name,
            log_message=f"📤 Notified {entry_id} of model unload: {model_name}",
        )

    def notify_idle_timeout(
        self: ControlHostProtocol,
        entry_id: str,
        idle_seconds: float,
    ) -> bool:
        """Notify consumer that stream has exceeded idle timeout.

        Inputs:
            entry_id: Stream/request identifier
            idle_seconds: Time idle in seconds

        Outputs:
            True if entry found and notified, False if not found
        """
        from universal_protocol.ws.frame_types import CODE_IDLE_TIMEOUT

        return RegistryControlMixin._signal_and_try_push(
            self,
            entry_id,
            CODE_IDLE_TIMEOUT,
            message=f"Stream expired after {idle_seconds:.1f}s idle",
            idle_seconds=idle_seconds,
            log_message=f"⏰ Entry {entry_id} idle for {idle_seconds:.1f}s, expired",
            log_level=WARNING,
        )

    def cancel_all_for_unload(
        self: ControlHostProtocol,
        model_name: str,
    ) -> int:
        """Cancel all entries due to model unload.

        Inputs:
            model_name: Name of model being unloaded

        Outputs:
            Count of entries notified
        """
        count = 0
        for entry_id in list(self._entries.keys()):
            if RegistryControlMixin.notify_model_unload(self, entry_id, model_name):
                count += 1
        return count
