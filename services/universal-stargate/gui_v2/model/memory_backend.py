"""
Memory backend for session storage and management.

Thread Safety: Not needed. All access from single async context
(GUI event loop). Dict operations are atomic under GIL.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from universal_logging import get_logger

from .data_structures import RequestSession

logger = get_logger(__name__)


class MemoryBackend:
    """
    Centralized data store for chat completion sessions.

    Thread Safety: Not needed. All methods called from single
    async event loop. Dict operations are atomic under GIL.

    Handles:
    - Session storage and retrieval
    - Event processing and session updates
    - Observer notifications for UI updates
    """

    def __init__(self):
        """Initialize memory backend."""
        self.sessions: dict[str, RequestSession] = {}
        self.session_order: list[str] = []
        self.observers: list[Callable[[], None]] = []

    def add_event(self, event: dict[str, Any]) -> None:
        """Process and store event in appropriate session.

        Args:
            event: Event data dictionary
        """
        try:
            session_id = self._extract_session_id(event)
            if not session_id:
                logger.warning("Could not extract session ID from event")
                return

            # Get or create session
            session = self.sessions.get(session_id)
            if not session:
                session = RequestSession(
                    id=session_id,
                    timestamp=datetime.fromtimestamp(
                        event.get("timestamp", datetime.now().timestamp())
                    ),
                    status="pending",
                    original_request={},
                    modified_request={},
                )
                self.sessions[session_id] = session
                self.session_order.append(session_id)

            # Update session with event
            session.add_event(event)
            self.notify_observers()

        except Exception as e:
            logger.error(f"Error processing event: {e}")

    def get_session(self, session_id: str) -> RequestSession | None:
        """Retrieve session by ID."""
        return self.sessions.get(session_id)

    def get_all_sessions(self) -> list[RequestSession]:
        """Get all sessions in chronological order."""
        return [
            self.sessions[sid] for sid in self.session_order if sid in self.sessions
        ]

    def add_observer(self, callback: Callable[[], None]) -> None:
        """Add observer for UI updates when data changes."""
        if callback not in self.observers:
            self.observers.append(callback)

    def remove_observer(self, callback: Callable[[], None]) -> None:
        """Remove observer."""
        if callback in self.observers:
            self.observers.remove(callback)

    def notify_observers(self) -> None:
        """Notify all observers of data changes."""
        for callback in self.observers:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error notifying observer: {e}")

    def _extract_session_id(self, event: dict[str, Any]) -> str | None:
        """Extract session ID from event data based on event type."""
        signal = event.get("signal", "")

        if signal == "monitoring.request_info":
            return event.get("request", {}).get("id")
        elif signal == "monitoring.chat_completion":
            return event.get("request", {}).get("id")
        elif signal == "monitoring.streaming_chunk":
            return event.get("request_id")
        elif signal == "monitoring.error":
            return event.get("request_id")

        # Generate new ID if not found
        return str(uuid.uuid4())

    def clear(self) -> None:
        """Clear all sessions."""
        self.sessions.clear()
        self.session_order.clear()
        self.notify_observers()
