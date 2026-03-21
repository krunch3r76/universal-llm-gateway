"""
Worker state machine with transition guards and audit logging.

Provides explicit state management for worker processes with:
- Validated state transitions
- Transition guards to prevent invalid states
- Audit logging for debugging and monitoring

Thread Safety: Not needed. All methods called from single-threaded
async RPC handlers. State transitions are synchronous (no await).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from universal_logging import get_logger

logger = get_logger(__name__)


class WorkerState(Enum):
    """Worker states in the lifecycle."""

    UNINITIALIZED = "uninitialized"
    LOADING = "loading"
    LOADED = "loaded"
    BUSY = "busy"
    ERROR = "error"
    UNLOADING = "unloading"
    UNLOADED = "unloaded"


TransitionCallback = Callable[
    [WorkerState, WorkerState, str, dict | None], None
]


@dataclass
class StateTransition:
    """Record of a state transition for audit logging."""

    from_state: WorkerState
    to_state: WorkerState
    timestamp: float
    reason: str
    metadata: dict = field(default_factory=dict)
    guard_passed: bool = False


class WorkerStateMachine:
    """State machine for worker lifecycle management.

    Thread Safety: Not needed. All access from single-threaded async
    RPC handlers. State transitions are synchronous (no await points).

    The optional on_transition callback fires after every successful state
    change (including forced transitions). Signature:
        callback(from_state, to_state, reason, metadata)
    """

    VALID_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
        WorkerState.UNINITIALIZED: {WorkerState.LOADING, WorkerState.ERROR},
        WorkerState.LOADING: {WorkerState.LOADED, WorkerState.ERROR},
        WorkerState.LOADED: {
            WorkerState.BUSY,
            WorkerState.UNLOADING,
            WorkerState.ERROR,
        },
        WorkerState.BUSY: {
            WorkerState.LOADED,
            WorkerState.ERROR,
            WorkerState.UNLOADING,
        },
        WorkerState.ERROR: {WorkerState.UNLOADING, WorkerState.UNLOADED},
        WorkerState.UNLOADING: {WorkerState.UNLOADED, WorkerState.ERROR},
        WorkerState.UNLOADED: {WorkerState.LOADING},
    }

    def __init__(
        self,
        worker_id: str,
        initial_state: WorkerState = WorkerState.UNINITIALIZED,
        on_transition: TransitionCallback | None = None,
    ):
        """Initialize state machine for a worker."""
        self.worker_id = worker_id
        self._state = initial_state
        self._transition_history: list[StateTransition] = []
        self._max_history = 100
        self._error_message: str | None = None
        self._on_transition = on_transition

        logger.info(
            f"[{worker_id}] State machine initialized in state: {initial_state.value}"
        )

    @property
    def current_state(self) -> WorkerState:
        """Get current state."""
        return self._state

    @property
    def is_busy(self) -> bool:
        """Check if worker is busy."""
        return self._state == WorkerState.BUSY

    @property
    def is_ready(self) -> bool:
        """Check if worker is ready for work (loaded and not busy)."""
        return self._state == WorkerState.LOADED

    @property
    def is_error(self) -> bool:
        """Check if worker is in error state."""
        return self._state == WorkerState.ERROR

    def _notify(
        self,
        from_state: WorkerState,
        to_state: WorkerState,
        reason: str,
        metadata: dict | None,
    ) -> None:
        """Fire the on_transition callback after a successful state change.

        Called at the end of transition(), force_idle(), clear_error(), and
        force_unloaded() to notify the ResourceTracker of every state change
        for bookkeeping and event emission.
        """
        if self._on_transition is not None:
            self._on_transition(from_state, to_state, reason, metadata)

    def transition(
        self,
        to_state: WorkerState,
        reason: str,
        guard: Callable[[], bool] | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Attempt a state transition with optional guard."""
        from_state = self._state

        if to_state not in self.VALID_TRANSITIONS.get(from_state, set()):
            logger.warning(
                f"[{self.worker_id}] Invalid transition: "
                f"{from_state.value} → {to_state.value}"
            )
            return False

        if guard is not None:
            try:
                if not guard():
                    logger.warning(
                        f"[{self.worker_id}] Transition guard failed: "
                        f"{from_state.value} → {to_state.value} (reason: {reason})"
                    )
                    return False
            except Exception as e:
                logger.error(f"[{self.worker_id}] Guard execution failed: {e}")
                return False

        self._state = to_state

        if from_state == WorkerState.ERROR and to_state != WorkerState.ERROR:
            self._error_message = None

        self._record_transition(
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            metadata=metadata,
        )

        logger.info(
            f"[{self.worker_id}] State transition: "
            f"{from_state.value} → {to_state.value} (reason: {reason})"
        )

        self._notify(from_state, to_state, reason, metadata)
        return True

    def _record_transition(
        self,
        *,
        from_state: WorkerState,
        to_state: WorkerState,
        reason: str,
        metadata: dict | None = None,
        forced: bool = False,
    ) -> None:
        """Record a state transition in the audit history."""
        meta = dict(metadata) if metadata else {}
        if forced:
            meta["forced"] = True
        record = StateTransition(
            from_state=from_state,
            to_state=to_state,
            timestamp=time.time(),
            reason=reason,
            metadata=meta,
        )
        self._transition_history.append(record)
        if len(self._transition_history) > self._max_history:
            self._transition_history = self._transition_history[-self._max_history :]

    def force_idle(self, reason: str) -> bool:
        """Force worker to LOADED state, bypassing VALID_TRANSITIONS.

        Only valid from BUSY, ERROR, or LOADED. Used when an external mechanism
        determines the worker should be idle (e.g., after stream cancellation
        or error recovery).

        Returns:
            True if state was forced to LOADED, False if current state is
            UNINITIALIZED, LOADING, or UNLOADED (cannot force idle).
        """
        from_state = self._state

        if from_state not in {
            WorkerState.BUSY,
            WorkerState.ERROR,
            WorkerState.LOADED,
        }:
            logger.warning(
                f"[{self.worker_id}] Cannot force idle from {from_state.value}"
            )
            return False

        self._state = WorkerState.LOADED
        self._error_message = None

        self._record_transition(
            from_state=from_state,
            to_state=WorkerState.LOADED,
            reason=f"FORCED_IDLE: {reason}",
            metadata={"forced": True},
            forced=True,
        )
        logger.info(
            f"[{self.worker_id}] FORCED state transition: "
            f"{from_state.value} → LOADED (reason: {reason})"
        )
        self._notify(from_state, WorkerState.LOADED, reason, {"forced": True})
        return True

    def set_error(self, error_message: str) -> bool:
        """Transition to error state with error message."""
        success = self.transition(
            WorkerState.ERROR,
            f"Error: {error_message}",
            metadata={"error_message": error_message},
        )
        if success:
            self._error_message = error_message
        return success

    def clear_error(self, reason: str = "Error cleared") -> bool:
        """Clear error state and set to UNLOADED, bypassing VALID_TRANSITIONS.

        Intended for error recovery: clears the error message and resets to
        UNLOADED so the model can be re-loaded.

        Returns:
            True if error was cleared; False if not in ERROR state.
        """
        if self._state != WorkerState.ERROR:
            logger.warning(
                f"[{self.worker_id}] Cannot clear error from state: {self._state.value}"
            )
            return False

        self._error_message = None
        self._state = WorkerState.UNLOADED

        self._record_transition(
            from_state=WorkerState.ERROR,
            to_state=WorkerState.UNLOADED,
            reason=f"ERROR_CLEARED: {reason}",
            metadata={"error_cleared": True, "reason": reason},
            forced=True,
        )

        logger.info(
            f"[{self.worker_id}] ERROR_CLEARED: "
            f"{WorkerState.ERROR.value} → UNLOADED (reason: {reason})"
        )
        self._notify(
            WorkerState.ERROR,
            WorkerState.UNLOADED,
            reason,
            {"error_cleared": True},
        )
        return True

    def force_unloaded(self, reason: str) -> None:
        """Force UNLOADED after the worker process is confirmed dead.

        Bypasses VALID_TRANSITIONS — only safe when the caller has verified
        termination (e.g. cleanup_failed_worker after kill/stop).
        """
        from_state = self._state
        self._state = WorkerState.UNLOADED
        self._error_message = None

        self._record_transition(
            from_state=from_state,
            to_state=WorkerState.UNLOADED,
            reason=f"FORCED_UNLOADED: {reason}",
            metadata={"forced": True},
            forced=True,
        )
        logger.info(
            f"[{self.worker_id}] FORCED state transition: "
            f"{from_state.value} → UNLOADED (reason: {reason})"
        )
        self._notify(from_state, WorkerState.UNLOADED, reason, {"forced": True})

    def get_error_message(self) -> str | None:
        """Get error message if in error state."""
        return self._error_message if self._state == WorkerState.ERROR else None

    def get_transition_history(self, limit: int = 10) -> list[StateTransition]:
        """Get recent state transitions."""
        return list(reversed(self._transition_history[-limit:]))

    class WorkerStatus(TypedDict):
        worker_id: str
        current_state: str
        is_busy: bool
        is_ready: bool
        is_error: bool
        error_message: str | None
        recent_transitions: list[dict]
        total_transitions: int

    def get_status(self) -> WorkerStatus:
        """Get comprehensive status information."""
        recent_transitions = [
            {
                "from": t.from_state.value,
                "to": t.to_state.value,
                "timestamp": t.timestamp,
                "reason": t.reason,
            }
            for t in reversed(self._transition_history[-5:])
        ]

        return {
            "worker_id": self.worker_id,
            "current_state": self._state.value,
            "is_busy": self.is_busy,
            "is_ready": self.is_ready,
            "is_error": self.is_error,
            "error_message": self._error_message,
            "recent_transitions": recent_transitions,
            "total_transitions": len(self._transition_history),
        }
