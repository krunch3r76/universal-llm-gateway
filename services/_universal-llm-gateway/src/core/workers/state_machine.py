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


@dataclass
class StateTransition:
    """Record of a state transition for audit logging."""

    from_state: WorkerState
    to_state: WorkerState
    timestamp: float
    reason: str
    guard_passed: bool = True
    metadata: dict = field(default_factory=dict)


class WorkerStateMachine:
    """State machine for worker lifecycle management.

    Thread Safety: Not needed. All access from single-threaded async
    RPC handlers. State transitions are synchronous (no await points).
    """

    VALID_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
        WorkerState.UNINITIALIZED: {WorkerState.LOADING, WorkerState.ERROR},
        WorkerState.LOADING: {WorkerState.LOADED, WorkerState.ERROR},
        WorkerState.LOADED: {
            WorkerState.BUSY,
            WorkerState.UNLOADING,
            WorkerState.ERROR,
            WorkerState.LOADING,
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
        self, worker_id: str, initial_state: WorkerState = WorkerState.UNINITIALIZED
    ):
        """Initialize state machine for a worker."""
        self.worker_id = worker_id
        self._state = initial_state
        self._transition_history: list[StateTransition] = []
        self._max_history = 100
        self._error_message: str | None = None

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

    def transition(
        self,
        to_state: WorkerState,
        reason: str,
        guard: Callable[[], bool] | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Attempt a state transition with optional guard."""
        from_state = self._state

        # Check if transition is valid
        if to_state not in self.VALID_TRANSITIONS.get(from_state, set()):
            logger.warning(
                f"[{self.worker_id}] Invalid transition: "
                f"{from_state.value} → {to_state.value}"
            )
            return False

        # Execute guard if provided
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

        # Perform transition
        self._state = to_state

        # Clear error message when leaving error state
        if from_state == WorkerState.ERROR and to_state != WorkerState.ERROR:
            self._error_message = None

        # Record transition
        transition = StateTransition(
            from_state=from_state,
            to_state=to_state,
            timestamp=time.time(),
            reason=reason,
            guard_passed=True,
            metadata=metadata or {},
        )
        self._transition_history.append(transition)

        # Trim history if needed
        if len(self._transition_history) > self._max_history:
            self._transition_history = self._transition_history[-self._max_history :]

        logger.info(
            f"[{self.worker_id}] State transition: "
            f"{from_state.value} → {to_state.value} (reason: {reason})"
        )

        return True

    def force_idle(self, reason: str) -> bool:
        """Force worker to idle state (LOADED) regardless of current state."""
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

        # Record forced transition
        transition = StateTransition(
            from_state=from_state,
            to_state=WorkerState.LOADED,
            timestamp=time.time(),
            reason=f"FORCED_IDLE: {reason}",
            guard_passed=True,
            metadata={"forced": True},
        )
        self._transition_history.append(transition)

        logger.info(
            f"[{self.worker_id}] FORCED state transition: "
            f"{from_state.value} → LOADED (reason: {reason})"
        )

        return True

    def set_error(self, error_message: str) -> bool:
        """Transition to error state with error message."""
        self._error_message = error_message
        return self.transition(
            WorkerState.ERROR,
            f"Error: {error_message}",
            metadata={"error_message": error_message},
        )

    def clear_error(self, reason: str = "Error cleared") -> bool:
        """Clear error state and transition to unloaded state."""
        if self._state != WorkerState.ERROR:
            logger.warning(
                f"[{self.worker_id}] Cannot clear error from state: {self._state.value}"
            )
            return False

        self._error_message = None
        self._state = WorkerState.UNLOADED

        transition = StateTransition(
            from_state=WorkerState.ERROR,
            to_state=WorkerState.UNLOADED,
            timestamp=time.time(),
            reason=f"ERROR_CLEARED: {reason}",
            guard_passed=True,
            metadata={"error_cleared": True, "reason": reason},
        )
        self._transition_history.append(transition)

        if len(self._transition_history) > self._max_history:
            self._transition_history = self._transition_history[-self._max_history :]

        logger.info(
            f"[{self.worker_id}] ERROR_CLEARED: "
            f"{WorkerState.ERROR.value} → UNLOADED (reason: {reason})"
        )
        return True

    def get_error_message(self) -> str | None:
        """Get error message if in error state."""
        return self._error_message if self._state == WorkerState.ERROR else None

    def get_transition_history(self, limit: int = 10) -> list[StateTransition]:
        """Get recent state transitions."""
        return list(reversed(self._transition_history[-limit:]))

    def get_status(self) -> dict:
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
