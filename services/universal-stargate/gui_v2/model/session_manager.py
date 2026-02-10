"""
Session management and selection logic.
"""

from collections.abc import Callable

from universal_logging import get_logger

from .data_structures import RequestSession
from .memory_backend import MemoryBackend

logger = get_logger(__name__)


class SessionManager:
    """
    Manages session selection and provides session data to views.

    Features:
    - Current session tracking
    - Session selection notifications
    - Session data access and formatting
    """

    def __init__(self, memory_backend: MemoryBackend):
        """
        Initialize session manager.

        Args:
            memory_backend: MemoryBackend instance for data access
        """
        self.memory_backend = memory_backend
        self.current_session_id = None
        self.observers: list[Callable[[str], None]] = []

    def select_session(self, session_id: str) -> None:
        """
        Select current session and notify observers.

        Args:
            session_id: ID of session to select
        """
        if session_id != self.current_session_id:
            self.current_session_id = session_id
            self.notify_observers()

    def get_current_session(self) -> RequestSession | None:
        """
        Get currently selected session.

        Returns:
            RequestSession if one is selected, None otherwise
        """
        if self.current_session_id:
            return self.memory_backend.get_session(self.current_session_id)
        return None

    def get_all_sessions(self) -> list[tuple[str, str, str]]:
        """
        Get all sessions as (id, summary, status) tuples.

        Returns:
            List of tuples containing session ID, summary text, and status
        """
        sessions = []
        for session in self.memory_backend.get_all_sessions():
            sessions.append((session.id, session.get_summary(), session.status))
        return sessions

    def add_observer(self, observer: Callable[[str], None]) -> None:
        """
        Add observer for session selection changes.

        Args:
            observer: Callback function taking session ID parameter
        """
        if observer not in self.observers:
            self.observers.append(observer)

    def notify_observers(self) -> None:
        """Notify observers of session selection change."""
        current_id = self.current_session_id or ""
        for observer in self.observers:
            try:
                observer(current_id)
            except Exception as e:
                logger.error(f"Error notifying observer: {e}")

    def get_session_count(self) -> int:
        """
        Get total number of sessions.

        Returns:
            Number of sessions in memory
        """
        return len(self.memory_backend.sessions)

    def get_active_sessions(self) -> list[tuple[str, str, str]]:
        """
        Get sessions that are currently active (not complete/error).

        Returns:
            List of (id, summary, status) tuples for active sessions
        """
        return [
            (s.id, s.get_summary(), s.status)
            for s in self.memory_backend.get_all_sessions()
            if s.status in ("pending", "processing", "streaming")
        ]
