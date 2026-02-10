"""
Process state management for process_ipc package.

Provides state tracking and querying capabilities for managed processes.
"""

import asyncio
from datetime import datetime
from typing import Any

from universal_logging import get_logger
from .types import EnhancedProcessInfo, ProcessActivity, ProcessState


class ProcessStateManager:
    """Manages process state and provides querying capabilities."""

    def __init__(self):
        self._process_states: dict[str, EnhancedProcessInfo] = {}
        self._state_listeners: dict[str, list[callable]] = {}
        self._logger = get_logger("process_ipc.core.state_manager")

    async def set_process_state(
        self, process_id: str, state: ProcessState, details: dict[str, Any] = None
    ) -> None:
        """Set the state of a process."""
        if process_id not in self._process_states:
            raise ValueError(f"Process {process_id} not registered")

        old_state = self._process_states[process_id].state
        if old_state != state:
            # Update state
            process_info = self._process_states[process_id]
            state_history = list(process_info.state_history)
            state_history.append((state, datetime.now()))

            self._process_states[process_id] = process_info._replace(
                state=state,
                last_state_change=datetime.now(),
                state_history=state_history[-10:],  # Keep last 10 state changes
            )

            self._logger.info(
                f"Process {process_id}: state changed {old_state} -> {state}"
            )

            # Notify listeners
            await self._notify_state_change(process_id, old_state, state, details)

    async def set_process_activity(
        self, process_id: str, activity: ProcessActivity
    ) -> None:
        """Set the current activity of a process."""
        if process_id not in self._process_states:
            raise ValueError(f"Process {process_id} not registered")

        process_info = self._process_states[process_id]
        self._process_states[process_id] = process_info._replace(
            current_activity=activity
        )

        if activity:
            self._logger.debug(
                f"Process {process_id}: activity set to {activity.activity_type}"
            )
        else:
            self._logger.debug(f"Process {process_id}: activity cleared")

    async def get_process_state(self, process_id: str) -> ProcessState:
        """Get the current state of a process."""
        if process_id not in self._process_states:
            raise ValueError(f"Process {process_id} not found")
        return self._process_states[process_id].state

    async def is_process_busy(self, process_id: str) -> bool:
        """Check if a process is currently busy (working/initializing)."""
        state = await self.get_process_state(process_id)
        return state in [ProcessState.BUSY, ProcessState.INITIALIZING]

    async def is_process_ready(self, process_id: str) -> bool:
        """Check if a process is ready to accept new work."""
        state = await self.get_process_state(process_id)
        return state in [ProcessState.READY, ProcessState.IDLE]

    async def get_process_activity(self, process_id: str) -> ProcessActivity | None:
        """Get detailed activity information about a process."""
        if process_id not in self._process_states:
            return None
        return self._process_states[process_id].current_activity

    async def wait_for_state(
        self, process_id: str, target_state: ProcessState, timeout: float = 60.0
    ) -> bool:
        """Wait for a process to reach a specific state."""
        start_time = datetime.now()
        while (datetime.now() - start_time).total_seconds() < timeout:
            current_state = await self.get_process_state(process_id)
            if current_state == target_state:
                return True
            await asyncio.sleep(0.1)
        return False

    async def register_process(
        self, process_id: str, process_info: EnhancedProcessInfo
    ) -> None:
        """Register a new process for state tracking."""
        self._process_states[process_id] = process_info
        self._logger.info(f"Process {process_id}: registered for state tracking")

    async def unregister_process(self, process_id: str) -> None:
        """Unregister a process from state tracking."""
        if process_id in self._process_states:
            del self._process_states[process_id]
        if process_id in self._state_listeners:
            del self._state_listeners[process_id]
        self._logger.info(f"Process {process_id}: unregistered from state tracking")

    async def _add_state_listener(
        self, listener_id: str, listener_func: callable
    ) -> None:
        """Add a state change listener for all processes."""
        self._state_listeners[listener_id] = listener_func
        self._logger.debug(f"Added state listener: {listener_id}")

    async def _remove_state_listener(self, listener_id: str) -> None:
        """Remove a state change listener."""
        if listener_id in self._state_listeners:
            del self._state_listeners[listener_id]
            self._logger.debug(f"Removed state listener: {listener_id}")

    async def _notify_state_change(
        self,
        process_id: str,
        old_state: ProcessState,
        new_state: ProcessState,
        details: dict[str, Any] = None,
    ) -> None:
        """Notify state change listeners."""
        if process_id in self._state_listeners:
            for listener in self._state_listeners[process_id]:
                try:
                    await listener(process_id, old_state, new_state, details)
                except Exception as e:
                    self._logger.error(f"Error in state change listener: {e}")

        # Also notify global listeners
        for listener_id, listener_func in self._state_listeners.items():
            if listener_id != process_id:  # Skip process-specific listeners
                try:
                    await listener_func(process_id, old_state, new_state, details)
                except Exception as e:
                    self._logger.error(
                        f"Error in global state change listener {listener_id}: {e}"
                    )
