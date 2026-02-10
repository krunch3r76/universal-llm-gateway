"""
Session-specific controller logic.
"""

from universal_logging import get_logger

from ..model import SessionManager
from ..view import SessionDetailView

logger = get_logger(__name__)


class SessionController:
    """
    Controller for session-specific operations.

    Handles:
    - Session selection
    - View updates
    - Session state changes
    """

    def __init__(self, session_manager: SessionManager, view: SessionDetailView):
        """
        Initialize session controller.

        Args:
            session_manager: SessionManager instance
            view: SessionDetailView instance
        """
        self.session_manager = session_manager
        self.view = view

    def update_view(self) -> None:
        """Update view with current session data."""
        try:
            session = self.session_manager.get_current_session()
            if session:
                self.view.update_session(session)
            else:
                self.view.clear()
        except Exception as e:
            logger.error(f"Error updating session view: {e}")

    def handle_session_changed(self, session_id: str) -> None:
        """
        Handle session selection change.

        Args:
            session_id: ID of newly selected session
        """
        try:
            self.session_manager.select_session(session_id)
            self.update_view()
        except Exception as e:
            logger.error(f"Error handling session change: {e}")

    def clear_view(self) -> None:
        """Clear session view."""
        try:
            self.view.clear()
        except Exception as e:
            logger.error(f"Error clearing session view: {e}")

    def get_current_session_id(self) -> str | None:
        """
        Get currently selected session ID.

        Returns:
            Session ID if one is selected, None otherwise
        """
        return self.session_manager.current_session_id
